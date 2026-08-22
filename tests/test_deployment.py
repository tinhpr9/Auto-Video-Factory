from __future__ import annotations

import os
from pathlib import Path

from auto_video_factory.web import WebSettings


def test_platform_port_env_is_supported(monkeypatch):
    monkeypatch.delenv("AVF_PORT", raising=False)
    monkeypatch.setenv("PORT", "9123")
    monkeypatch.setenv("AVF_PROVIDER", "offline")
    settings = WebSettings.from_env()
    assert settings.port == 9123


def test_dockerfile_contains_required_media_runtime_and_non_root_user():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert "python:3.12-slim-bookworm" in dockerfile
    assert "ffmpeg" in dockerfile
    assert "espeak" in dockerfile
    assert "fonts-dejavu-core" in dockerfile
    assert "HEALTHCHECK" in dockerfile and "/health" in dockerfile
    assert "USER appuser" in dockerfile
    assert "OPENAI_API_KEY=" not in dockerfile
    assert "AVF_ACCESS_CODE=" not in dockerfile


def test_dockerignore_excludes_local_artifacts_and_secrets():
    ignored = Path(".dockerignore").read_text(encoding="utf-8")
    for entry in [".git", "output", "*.zip", "*.whl", ".env", "__pycache__"]:
        assert entry in ignored


def test_package_version_is_v32():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "0.3.2"' in pyproject


def test_deploy_env_example_contains_names_only_not_real_secrets():
    env_example = Path("deploy.env.example").read_text(encoding="utf-8")
    assert "AVF_PROVIDER=openai" in env_example
    assert "OPENAI_API_KEY=" in env_example
    assert "AVF_ACCESS_CODE=" in env_example
    assert "sk-" not in env_example
    assert "correct-horse-42" not in env_example


def test_start_mobile_service_script_contains_supervisor_lifecycle():
    script = Path("start_mobile_service.sh").read_text(encoding="utf-8")
    assert "auto_video_factory.supervisor" in script
    assert "start" in script
    assert "stop" in script
    assert "restart" in script
    assert "status" in script


def test_supervisor_module_contains_daemon_and_lifecycle():
    from auto_video_factory.supervisor import AVFSupervisor
    supervisor = AVFSupervisor(port=8000, host="127.0.0.1")
    assert supervisor.port == 8000
    assert supervisor.host == "127.0.0.1"
    assert hasattr(supervisor, "start")
    assert hasattr(supervisor, "stop")
    assert hasattr(supervisor, "status")
    assert hasattr(supervisor, "check_health")


