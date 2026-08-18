import base64
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from auto_video_factory.models import Scene
from auto_video_factory.openai_providers import (
    OpenAIImageProvider,
    OpenAIStoryPlanner,
    OpenAITTS,
    ProviderConfigurationError,
    ProviderResponseError,
)


class FakeClient:
    def __init__(self, json_responses=None, byte_responses=None):
        self.json_responses = list(json_responses or [])
        self.byte_responses = list(byte_responses or [])
        self.calls = []

    def post_json(self, path, payload):
        self.calls.append(("json", path, payload))
        return self.json_responses.pop(0)

    def post_bytes(self, path, payload):
        self.calls.append(("bytes", path, payload))
        return self.byte_responses.pop(0)


def response_with_text(text: str) -> dict:
    return {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": text}],
            }
        ]
    }


def test_story_planner_builds_exact_scene_count_and_character_anchor():
    payload = {
        "title": "Kẻ bị lãng quên",
        "character_anchor": "young wandering swordsman, dark teal robe, silver hair tie",
        "scenes": [
            {"narration": "Cảnh một mở ra giữa sơn môn lạnh giá.", "visual_prompt": "standing before an ancient gate"},
            {"narration": "Cảnh hai hé lộ luồng linh lực kỳ lạ.", "visual_prompt": "cyan spiritual light in a quiet courtyard"},
        ],
    }
    client = FakeClient(json_responses=[response_with_text(json.dumps(payload, ensure_ascii=False))])
    planner = OpenAIStoryPlanner(client=client, scene_count=2, model="gpt-5-mini")

    plan = planner.plan("Một kiếm tu bị ruồng bỏ")

    assert plan.title == "Kẻ bị lãng quên"
    assert len(plan.scenes) == 2
    assert plan.scenes[0].index == 1
    assert "dark teal robe" in plan.scenes[0].visual_prompt
    assert "dark teal robe" in plan.scenes[1].visual_prompt
    kind, path, request = client.calls[0]
    assert kind == "json"
    assert path == "/responses"
    assert request["model"] == "gpt-5-mini"
    assert "2 scenes" in request["input"]
    assert "Return JSON only" in request["input"]


def test_story_planner_rejects_invalid_json():
    client = FakeClient(json_responses=[response_with_text("not json")])
    planner = OpenAIStoryPlanner(client=client, scene_count=2)
    with pytest.raises(ProviderResponseError):
        planner.plan("Chủ đề")


def test_story_planner_rejects_wrong_scene_count():
    payload = {
        "title": "X",
        "character_anchor": "original wanderer",
        "scenes": [{"narration": "Một cảnh.", "visual_prompt": "snow"}],
    }
    client = FakeClient(json_responses=[response_with_text(json.dumps(payload))])
    planner = OpenAIStoryPlanner(client=client, scene_count=2)
    with pytest.raises(ProviderResponseError, match="scene count"):
        planner.plan("Chủ đề")


def test_image_provider_decodes_and_crops_vertical_png(tmp_path: Path):
    source = Image.new("RGB", (1024, 1536), (20, 40, 60))
    buffer = io.BytesIO()
    source.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    client = FakeClient(json_responses=[{"data": [{"b64_json": encoded}]}])
    provider = OpenAIImageProvider(client=client, width=720, height=1280, model="gpt-image-1-mini")
    scene = Scene(index=1, narration="Một người trở về.", visual_prompt="original swordsman in snow")
    output = tmp_path / "scene.png"

    provider.create(scene, output)

    with Image.open(output) as image:
        assert image.size == (720, 1280)
    _, path, request = client.calls[0]
    assert path == "/images/generations"
    assert request["model"] == "gpt-image-1-mini"
    assert request["size"] == "1024x1536"
    assert request["output_format"] == "png"


def test_tts_writes_wav_and_requests_natural_vietnamese_voice(tmp_path: Path):
    fake_wav = b"RIFF" + b"\0" * 2048
    client = FakeClient(byte_responses=[fake_wav])
    tts = OpenAITTS(client=client, model="gpt-4o-mini-tts", voice="marin")
    output = tmp_path / "voice.wav"

    tts.synthesize("Đây là lời kể thử nghiệm.", output)

    assert output.read_bytes() == fake_wav
    _, path, request = client.calls[0]
    assert path == "/audio/speech"
    assert request["model"] == "gpt-4o-mini-tts"
    assert request["voice"] == "marin"
    assert request["response_format"] == "wav"
    assert "Vietnamese" in request["instructions"]


def test_empty_api_key_is_rejected_without_echoing_secret(monkeypatch):
    from auto_video_factory.openai_providers import OpenAIHTTPClient

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderConfigurationError, match="OPENAI_API_KEY") as exc:
        OpenAIHTTPClient()
    assert "sk-" not in str(exc.value)


def test_http_client_does_not_retry_auth_failure(monkeypatch):
    import urllib.error
    from auto_video_factory.openai_providers import OpenAIHTTPClient, ProviderRequestError

    calls = {"count": 0}

    def fail_auth(request, timeout):
        calls["count"] += 1
        raise urllib.error.HTTPError(request.full_url, 401, "unauthorized", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fail_auth)
    client = OpenAIHTTPClient(api_key="secret-test-key", max_attempts=3, retry_base_seconds=0)
    with pytest.raises(ProviderRequestError) as exc:
        client.post_json("/responses", {"model": "x", "input": "y"})
    assert exc.value.code == "AUTH_FAILED"
    assert exc.value.retryable is False
    assert calls["count"] == 1
    assert "secret-test-key" not in str(exc.value)


def test_http_client_retries_transient_server_error(monkeypatch):
    import urllib.error
    from auto_video_factory.openai_providers import OpenAIHTTPClient

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"output_text":"ok"}'

    calls = {"count": 0}

    def flaky(request, timeout):
        calls["count"] += 1
        if calls["count"] == 1:
            raise urllib.error.HTTPError(request.full_url, 500, "server", {}, None)
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", flaky)
    monkeypatch.setattr("time.sleep", lambda _: None)
    client = OpenAIHTTPClient(api_key="test-key", max_attempts=2, retry_base_seconds=0)
    result = client.post_json("/responses", {"model": "x", "input": "y"})
    assert result["output_text"] == "ok"
    assert calls["count"] == 2


@pytest.mark.skipif(__import__("shutil").which("ffmpeg") is None or __import__("shutil").which("ffprobe") is None, reason="ffmpeg unavailable")
def test_ai_provider_contract_runs_end_to_end_with_fake_transport(tmp_path: Path):
    import wave
    from auto_video_factory.media import FFmpegRenderer
    from auto_video_factory.pipeline import VideoFactory

    plan_payload = {
        "title": "Tuyết Trên Sơn Môn",
        "character_anchor": "young adult wanderer, dark teal robe, black hair, silver hair tie",
        "scenes": [
            {"narration": "Hắn trở lại sơn môn giữa một đêm tuyết lạnh.", "visual_prompt": "ancient sect gate in snow"},
            {"narration": "Một luồng kiếm ý bỗng thức tỉnh dưới chân hắn.", "visual_prompt": "cyan spiritual light, close cinematic framing"},
        ],
    }

    source = Image.new("RGB", (1024, 1536), (36, 58, 76))
    image_buffer = io.BytesIO()
    source.save(image_buffer, format="PNG")
    image_b64 = base64.b64encode(image_buffer.getvalue()).decode("ascii")

    wav_path = tmp_path / "sample.wav"
    with wave.open(str(wav_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * 16000)
    wav_bytes = wav_path.read_bytes()

    client = FakeClient(
        json_responses=[
            response_with_text(json.dumps(plan_payload, ensure_ascii=False)),
            {"data": [{"b64_json": image_b64}]},
            {"data": [{"b64_json": image_b64}]},
        ],
        byte_responses=[wav_bytes, wav_bytes],
    )
    factory = VideoFactory(
        planner=OpenAIStoryPlanner(client=client, scene_count=2),
        tts=OpenAITTS(client=client),
        image_provider=OpenAIImageProvider(client=client, width=360, height=640),
        renderer=FFmpegRenderer(width=360, height=640, fps=24),
    )

    result = factory.generate("Một kiếm tu trở lại", tmp_path / "job")

    assert result.video.exists() and result.video.stat().st_size > 10_000
    assert result.subtitles.exists()
    assert len(result.plan.scenes) == 2
