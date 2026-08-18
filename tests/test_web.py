from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from auto_video_factory.models import GenerationResult, Scene, SceneTiming, StoryPlan
from auto_video_factory.web import WebSettings, create_app


class FakeFactory:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def generate(self, topic: str, job_dir: Path, on_progress=None) -> GenerationResult:
        if on_progress:
            on_progress(10, "Đang viết kịch bản")
        if self.fail:
            raise RuntimeError("provider exploded with sk-super-secret")
        job_dir.mkdir(parents=True, exist_ok=True)
        video = job_dir / "video.mp4"
        subtitles = job_dir / "captions.srt"
        video.write_bytes(b"fake-mp4")
        subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\nXin chào\n", encoding="utf-8")
        if on_progress:
            on_progress(100, "Hoàn tất")
        plan = StoryPlan(
            title=f"{topic} — test",
            scenes=[Scene(index=1, narration="Xin chào", visual_prompt="test")],
        )
        return GenerationResult(
            video=video,
            subtitles=subtitles,
            timings=[SceneTiming(index=1, start=0.0, end=1.0, text="Xin chào")],
            plan=plan,
        )


def wait_for_terminal(client: TestClient, status_url: str) -> dict:
    deadline = time.time() + 2
    while time.time() < deadline:
        payload = client.get(status_url).json()
        if payload["status"] in {"completed", "failed"}:
            return payload
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def make_client(tmp_path: Path, *, fail: bool = False) -> TestClient:
    settings = WebSettings(provider="offline", output_root=tmp_path / "jobs", max_workers=1)
    app = create_app(settings=settings, factory_builder=lambda request: FakeFactory(fail=fail))
    return TestClient(app)


def test_mobile_home_has_generate_flow_and_no_secret_field(tmp_path: Path):
    client = make_client(tmp_path)
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert 'name="viewport"' in html
    assert "Tạo video" in html
    assert "Chủ đề" in html
    assert 'id="duration"' in html and 'id="voice"' in html and 'id="style"' in html
    assert "Đang tải cấu hình" in html
    assert "OPENAI_API_KEY" not in html
    assert 'name="api_key"' not in html.lower()
    assert 'name="openai_api_key"' not in html.lower()


def test_config_exposes_safe_server_capabilities_only(tmp_path: Path):
    client = make_client(tmp_path)
    payload = client.get("/api/config").json()
    assert payload["provider"] == "offline"
    assert payload["durations"] == [45, 60, 90]
    assert {item["id"] for item in payload["styles"]} >= {"xianxia-cinematic"}
    assert "api_key" not in json.dumps(payload).lower()


def test_create_job_runs_factory_and_video_can_be_downloaded(tmp_path: Path):
    client = make_client(tmp_path)
    response = client.post(
        "/api/jobs",
        json={
            "topic": "Một kiếm tu trở lại",
            "duration_seconds": 60,
            "voice": "marin",
            "style": "xianxia-cinematic",
        },
    )
    assert response.status_code == 202
    created = response.json()
    assert created["status"] in {"queued", "running", "completed"}
    assert created["job_id"]
    assert created["status_url"].endswith(created["job_id"])

    finished = wait_for_terminal(client, created["status_url"])
    assert finished["status"] == "completed"
    assert finished["progress"] == 100
    assert finished["title"].startswith("Một kiếm tu trở lại")
    assert finished["video_url"].endswith("/video")

    video = client.get(finished["video_url"])
    assert video.status_code == 200
    assert video.content == b"fake-mp4"
    assert video.headers["content-type"].startswith("video/mp4")
    assert "attachment" not in video.headers.get("content-disposition", "").lower()


def test_job_request_validation_rejects_empty_topic_and_unknown_duration(tmp_path: Path):
    client = make_client(tmp_path)
    empty = client.post(
        "/api/jobs",
        json={"topic": "   ", "duration_seconds": 60, "voice": "marin", "style": "xianxia-cinematic"},
    )
    assert empty.status_code == 422

    duration = client.post(
        "/api/jobs",
        json={"topic": "x", "duration_seconds": 30, "voice": "marin", "style": "xianxia-cinematic"},
    )
    assert duration.status_code == 422


def test_failed_job_returns_safe_user_error_without_provider_secret(tmp_path: Path, caplog):
    client = make_client(tmp_path, fail=True)
    response = client.post(
        "/api/jobs",
        json={"topic": "Một kiếm tu", "duration_seconds": 45, "voice": "marin", "style": "xianxia-cinematic"},
    )
    finished = wait_for_terminal(client, response.json()["status_url"])
    assert finished["status"] == "failed"
    assert finished["error_code"] == "GENERATION_FAILED"
    dumped = json.dumps(finished, ensure_ascii=False)
    assert "sk-super-secret" not in dumped
    assert "provider exploded" not in dumped
    assert "sk-super-secret" not in caplog.text
    assert "provider exploded" not in caplog.text


def test_video_route_is_not_available_before_job_finishes(tmp_path: Path):
    class SlowFactory(FakeFactory):
        def generate(self, topic: str, job_dir: Path, on_progress=None) -> GenerationResult:
            time.sleep(0.15)
            return super().generate(topic, job_dir, on_progress=on_progress)

    settings = WebSettings(provider="offline", output_root=tmp_path / "jobs", max_workers=1)
    client = TestClient(create_app(settings=settings, factory_builder=lambda request: SlowFactory()))
    created = client.post(
        "/api/jobs",
        json={"topic": "Một kiếm tu", "duration_seconds": 45, "voice": "marin", "style": "xianxia-cinematic"},
    ).json()
    early = client.get(f"/api/jobs/{created['job_id']}/video")
    assert early.status_code == 409


def test_openapi_snapshot_matches_version_controlled_contract(tmp_path: Path):
    client = make_client(tmp_path)
    expected = json.loads(Path("docs/openapi-v3.1.json").read_text(encoding="utf-8"))
    assert client.app.openapi() == expected


def test_default_offline_factory_maps_duration_to_scene_count(tmp_path: Path):
    from auto_video_factory.story import TemplateStoryPlanner
    from auto_video_factory.web import WebJobRequest, build_factory_for_request

    settings = WebSettings(provider="offline", output_root=tmp_path / "jobs")
    request = WebJobRequest(topic="Kiếm tu", duration_seconds=90, voice="marin", style="ink-wash")
    factory = build_factory_for_request(request, settings)
    assert isinstance(factory.planner, TemplateStoryPlanner)
    assert factory.planner.scene_count == 12


def test_default_openai_factory_keeps_key_server_side_and_applies_style(monkeypatch, tmp_path: Path):
    from auto_video_factory.openai_providers import OpenAIImageProvider, OpenAIStoryPlanner, OpenAITTS
    from auto_video_factory.web import STYLE_OPTIONS, WebJobRequest, build_factory_for_request

    monkeypatch.setenv("OPENAI_API_KEY", "test-only-server-key")
    settings = WebSettings(provider="openai", output_root=tmp_path / "jobs")
    request = WebJobRequest(topic="Kiếm tu", duration_seconds=45, voice="onyx", style="dark-fantasy")
    factory = build_factory_for_request(request, settings)
    assert isinstance(factory.planner, OpenAIStoryPlanner)
    assert factory.planner.scene_count == 6
    assert factory.planner.visual_style == STYLE_OPTIONS["dark-fantasy"]["prompt"]
    assert isinstance(factory.tts, OpenAITTS) and factory.tts.voice == "onyx"
    assert isinstance(factory.image_provider, OpenAIImageProvider)
    assert factory.image_provider.style_prompt == STYLE_OPTIONS["dark-fantasy"]["prompt"]
