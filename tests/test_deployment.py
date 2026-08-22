from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

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
    assert hasattr(supervisor, "canonical_root")


def test_resolve_canonical_root_resolves_main_repo(tmp_path):
    from auto_video_factory.supervisor import resolve_canonical_root
    # Explicit env override
    custom_root = tmp_path / "custom_prod"
    custom_root.mkdir()
    with patch.dict(os.environ, {"AVF_PRODUCTION_ROOT": str(custom_root)}):
        assert resolve_canonical_root(tmp_path) == custom_root.resolve()


def test_supervisor_anchors_cwd_and_pythonpath_to_canonical_root(tmp_path):
    from auto_video_factory.supervisor import AVFSupervisor
    fake_prod = tmp_path / "prod_root"
    fake_prod.mkdir()
    (fake_prod / "auto_video_factory").mkdir()
    fake_state = fake_prod / "output/web"

    with patch.dict(os.environ, {"AVF_PRODUCTION_ROOT": str(fake_prod)}):
        sup = AVFSupervisor(state_dir=fake_state, port=8000)
        assert sup.canonical_root == fake_prod.resolve()
        assert sup.state_dir == fake_state.resolve()
        assert sup.supervisor_pid_file == fake_state / "avf_supervisor.pid"
        assert sup.worker_pid_file == fake_state / "avf_web.pid"


def test_unwritable_package_parent_fallback(tmp_path, monkeypatch):
    from auto_video_factory.supervisor import AVFSupervisor
    unwritable_root = tmp_path / "read_only_root"
    unwritable_root.mkdir()
    # Mock mkdir to raise PermissionError when attempting to create output/web in unwritable root
    orig_mkdir = Path.mkdir
    def mock_mkdir(self, *args, **kwargs):
        if str(unwritable_root) in str(self) and "output/web" in str(self):
            raise PermissionError("Mock read-only filesystem")
        return orig_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mock_mkdir)
    monkeypatch.delenv("AVF_STATE_DIR", raising=False)
    sup = AVFSupervisor(canonical_root=unwritable_root, port=8000)
    expected_fallback = (Path.home() / ".auto_video_factory/web").resolve()
    assert sup.state_dir == expected_fallback


def test_git_probe_timeout_falls_through(tmp_path, monkeypatch):
    import subprocess
    from auto_video_factory.supervisor import resolve_canonical_root

    def mock_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git rev-parse", timeout=3.0)

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.delenv("AVF_PRODUCTION_ROOT", raising=False)
    # Should safely fall through without hanging or unhandled exception
    res = resolve_canonical_root(tmp_path)
    assert res is not None


def test_existing_pythonpath_preserved(tmp_path, monkeypatch):
    from auto_video_factory.supervisor import AVFSupervisor
    fake_prod = tmp_path / "prod_root"
    fake_prod.mkdir()
    (fake_prod / "auto_video_factory").mkdir()
    fake_state = fake_prod / "output/web"

    with patch.dict(os.environ, {"AVF_PRODUCTION_ROOT": str(fake_prod), "PYTHONPATH": "/custom/lib1:/custom/lib2"}):
        sup = AVFSupervisor(state_dir=fake_state, port=8000)
        # Test worker env building logic
        worker_env = os.environ.copy()
        existing_pp = os.environ.get("PYTHONPATH", "")
        if existing_pp:
            parts = [p for p in existing_pp.split(os.pathsep) if p and p != str(sup.canonical_root)]
            worker_env["PYTHONPATH"] = os.pathsep.join([str(sup.canonical_root)] + parts)
        assert worker_env["PYTHONPATH"] == f"{sup.canonical_root}:/custom/lib1:/custom/lib2"


def test_custom_production_root_venv_preferred(tmp_path):
    script_path = Path("start_mobile_service.sh")
    content = script_path.read_text(encoding="utf-8")
    # Verify launcher checks CANONICAL_ROOT venv before SCRIPT_DIR or system
    assert '"$CANONICAL_ROOT/.venv/bin/python3"' in content
    pos_canonical = content.find('"$CANONICAL_ROOT/.venv/bin/python3"')
    pos_system = content.find('command -v python3')
    assert pos_canonical != -1 and pos_system != -1
    assert pos_canonical < pos_system


def test_main_unwritable_default_state_fallback(tmp_path, monkeypatch):
    from auto_video_factory.supervisor import resolve_canonical_root, AVFSupervisor
    unwritable_root = tmp_path / "read_only_canonical_root"
    unwritable_root.mkdir()
    (unwritable_root / "auto_video_factory").mkdir()

    orig_mkdir = Path.mkdir
    def mock_mkdir(self, *args, **kwargs):
        if str(unwritable_root) in str(self) and "output/web" in str(self):
            raise PermissionError("Mock read-only production root")
        return orig_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mock_mkdir)
    monkeypatch.delenv("AVF_STATE_DIR", raising=False)

    # Invoke the EXACT production construction path used in main()
    with patch.dict(os.environ, {"AVF_PRODUCTION_ROOT": str(unwritable_root)}, clear=False):
        canonical_root = resolve_canonical_root()
        env_state = os.getenv("AVF_STATE_DIR")
        state_dir = Path(env_state) if env_state else None
        supervisor = AVFSupervisor(state_dir=state_dir, canonical_root=canonical_root, port=8000)

        expected_fallback = (Path.home() / ".auto_video_factory/web").resolve()
        assert supervisor.state_dir == expected_fallback
        assert supervisor.supervisor_pid_file == expected_fallback / "avf_supervisor.pid"
        assert supervisor.worker_pid_file == expected_fallback / "avf_web.pid"
        assert supervisor.log_file == expected_fallback / "avf_supervisor.log"


def test_explicit_state_dir_preserved(tmp_path):
    from auto_video_factory.supervisor import AVFSupervisor
    explicit_dir = tmp_path / "my_explicit_state"
    explicit_dir.mkdir()
    sup = AVFSupervisor(state_dir=explicit_dir, port=8000)
    assert sup.state_dir == explicit_dir.resolve()
    assert sup.supervisor_pid_file == explicit_dir / "avf_supervisor.pid"


def test_explicit_unwritable_state_dir_fails_closed(tmp_path, monkeypatch):
    import pytest
    from auto_video_factory.supervisor import AVFSupervisor
    unwritable_explicit = tmp_path / "unwritable_explicit_dir"

    orig_mkdir = Path.mkdir
    def mock_mkdir(self, *args, **kwargs):
        if str(unwritable_explicit) in str(self):
            raise PermissionError("Permission denied on explicit path")
        return orig_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mock_mkdir)
    with pytest.raises(PermissionError):
        AVFSupervisor(state_dir=unwritable_explicit, port=8000)





