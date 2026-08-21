from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from auto_video_factory.web import WebSettings, create_app
from tests.test_web import FakeFactory


ACCESS_CODE = "correct-horse-42"


def auth_client(tmp_path: Path, **overrides) -> TestClient:
    values = {
        "provider": "offline",
        "output_root": tmp_path / "jobs",
        "max_workers": 1,
        "access_code": ACCESS_CODE,
        "auth_attempts_per_minute": 3,
        "max_jobs_per_hour": 2,
    }
    values.update(overrides)
    settings = WebSettings(**values)
    app = create_app(settings=settings, factory_builder=lambda request: FakeFactory())
    return TestClient(app)


def login(client: TestClient, code: str = ACCESS_CODE) -> str:
    response = client.post("/api/session", json={"code": code})
    assert response.status_code == 200
    payload = response.json()
    assert payload["token"]
    assert payload["expires_in_seconds"] > 0
    assert code not in json.dumps(payload)
    return payload["token"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_access_contract_is_versioned_and_explicit():
    contract = json.loads(Path("docs/access-contract-v3.1.json").read_text(encoding="utf-8"))
    assert contract["version"] == "0.3.1"
    assert contract["paths"]["POST /api/session"]["errors"] == [401, 429]
    assert contract["paths"]["POST /api/jobs"]["bearer"] is True


def test_openai_web_requires_strong_access_code(monkeypatch):
    monkeypatch.setenv("AVF_PROVIDER", "openai")
    monkeypatch.delenv("AVF_ACCESS_CODE", raising=False)
    with pytest.raises(ValueError, match="AVF_ACCESS_CODE"):
        WebSettings.from_env()

    monkeypatch.setenv("AVF_ACCESS_CODE", "short")
    with pytest.raises(ValueError, match="12"):
        WebSettings.from_env()


def test_private_api_rejects_missing_token_but_health_and_shell_stay_public(tmp_path: Path):
    client = auth_client(tmp_path)
    assert client.get("/").status_code == 200
    assert client.get("/health").status_code == 200
    assert client.get("/api/config").status_code == 401
    assert client.post(
        "/api/jobs",
        json={"topic": "Kiếm tu", "duration_seconds": 45, "voice": "marin", "style": "xianxia-cinematic"},
    ).status_code == 401


def test_login_issues_opaque_token_and_unlocks_protected_flow(tmp_path: Path):
    client = auth_client(tmp_path)
    before = client.get("/api/session").json()
    assert before == {"auth_required": True, "authenticated": False}

    token = login(client)
    headers = bearer(token)
    session = client.get("/api/session", headers=headers).json()
    assert session == {"auth_required": True, "authenticated": True}
    assert client.get("/api/config", headers=headers).status_code == 200

    created = client.post(
        "/api/jobs",
        headers=headers,
        json={"topic": "Một kiếm tu trở lại", "duration_seconds": 45, "voice": "marin", "style": "xianxia-cinematic"},
    )
    assert created.status_code == 202
    status_url = created.json()["status_url"]
    status_payload = client.get(status_url, headers=headers).json()
    assert status_payload["status"] in {"queued", "running", "completed"}


def test_bad_code_is_rejected_and_login_attempts_are_rate_limited(tmp_path: Path):
    client = auth_client(tmp_path)
    for _ in range(3):
        response = client.post("/api/session", json={"code": "wrong-code-999"})
        assert response.status_code == 401
    limited = client.post("/api/session", json={"code": "wrong-code-999"})
    assert limited.status_code == 429
    assert ACCESS_CODE not in limited.text


def test_job_quota_blocks_extra_paid_generation_per_session(tmp_path: Path):
    client = auth_client(tmp_path, max_jobs_per_hour=1)
    token = login(client)
    payload = {"topic": "Kiếm tu", "duration_seconds": 45, "voice": "marin", "style": "xianxia-cinematic"}
    assert client.post("/api/jobs", headers=bearer(token), json=payload).status_code == 202
    blocked = client.post("/api/jobs", headers=bearer(token), json=payload)
    assert blocked.status_code == 429


def test_logout_revokes_token(tmp_path: Path):
    client = auth_client(tmp_path)
    token = login(client)
    headers = bearer(token)
    assert client.delete("/api/session", headers=headers).status_code == 204
    assert client.get("/api/config", headers=headers).status_code == 401


def test_mobile_shell_has_access_code_gate_without_persisting_raw_code(tmp_path: Path):
    client = auth_client(tmp_path)
    html = client.get("/").text
    assert 'id="accessCode"' in html
    assert 'type="password"' in html
    assert "Mở khóa" in html
    assert "localStorage" not in html
    assert ACCESS_CODE not in html


def test_production_flow_with_require_auth_and_no_code_rejected(monkeypatch):
    monkeypatch.setenv("AVF_PROVIDER", "flow")
    monkeypatch.delenv("AVF_ACCESS_CODE", raising=False)
    monkeypatch.delenv("AVF_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("AVF_LOCAL_PHONE", raising=False)
    monkeypatch.delenv("AVF_FLOW_MOCK", raising=False)
    with pytest.raises(ValueError, match="AVF_ACCESS_CODE is required when AVF_PROVIDER=flow"):
        WebSettings.from_env()


def test_local_phone_unauthenticated_flow_allowed_on_loopback(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("AVF_PROVIDER", "flow")
    monkeypatch.delenv("AVF_ACCESS_CODE", raising=False)
    monkeypatch.setenv("AVF_LOCAL_PHONE", "1")
    monkeypatch.setenv("AVF_REQUIRE_AUTH", "0")
    monkeypatch.setenv("AVF_HOST", "127.0.0.1")
    monkeypatch.delenv("AVF_FLOW_MOCK", raising=False)

    settings = WebSettings.from_env()
    assert settings.provider == "flow"
    assert settings.require_auth is False
    assert settings.access_code is None
    assert settings.host == "127.0.0.1"

    app = create_app(
        settings=WebSettings(
            provider="flow",
            flow_mock=True,
            output_root=tmp_path / "jobs",
            host="127.0.0.1",
            require_auth=False,
            access_code=None,
        ),
        factory_builder=lambda request: FakeFactory(),
    )
    client = TestClient(app)
    status_resp = client.get("/api/session")
    assert status_resp.status_code == 200
    assert status_resp.json() == {"auth_required": False, "authenticated": True}
    # Unauthenticated job creation must succeed on loopback
    job_resp = client.post(
        "/api/jobs",
        json={"topic": "Loopback Test", "duration_seconds": 60, "voice": "marin", "style": "xianxia-cinematic"},
    )
    assert job_resp.status_code == 202


def test_unauthenticated_mode_rejected_on_all_interfaces_0000(monkeypatch):
    monkeypatch.setenv("AVF_PROVIDER", "flow")
    monkeypatch.delenv("AVF_ACCESS_CODE", raising=False)
    monkeypatch.setenv("AVF_REQUIRE_AUTH", "0")
    monkeypatch.setenv("AVF_HOST", "0.0.0.0")

    with pytest.raises(ValueError, match="loopback"):
        WebSettings.from_env()


def test_unauthenticated_mode_rejected_on_non_loopback_external_host(monkeypatch):
    monkeypatch.setenv("AVF_PROVIDER", "flow")
    monkeypatch.delenv("AVF_ACCESS_CODE", raising=False)
    monkeypatch.setenv("AVF_REQUIRE_AUTH", "0")
    monkeypatch.setenv("AVF_HOST", "192.168.1.100")

    with pytest.raises(ValueError, match="loopback"):
        WebSettings.from_env()


def test_create_app_rejects_unauthenticated_mode_on_non_loopback_settings(tmp_path: Path):
    settings = WebSettings(
        provider="flow",
        flow_mock=True,
        output_root=tmp_path / "jobs",
        host="0.0.0.0",
        require_auth=False,
        access_code=None,
    )
    with pytest.raises(ValueError, match="loopback"):
        create_app(settings=settings)


def test_existing_authenticated_production_flow_behavior_unchanged(tmp_path: Path):
    settings = WebSettings(
        provider="flow",
        flow_mock=True,
        output_root=tmp_path / "jobs",
        host="0.0.0.0",
        require_auth=True,
        access_code=ACCESS_CODE,
    )
    app = create_app(settings=settings, factory_builder=lambda request: FakeFactory())
    client = TestClient(app)
    # Without token, config and jobs return 401
    assert client.get("/api/config").status_code == 401
    token = login(client)
    assert client.get("/api/config", headers=bearer(token)).status_code == 200

