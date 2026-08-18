from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path


def test_wheel_builds_and_contains_mobile_web_ui(tmp_path: Path):
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                ".",
                "--no-deps",
                "--no-build-isolation",
                "-w",
                str(wheel_dir),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        subprocess.run(
            ["uv", "build", "--wheel", "-o", str(wheel_dir)],
            check=True,
            capture_output=True,
            text=True,
        )
    wheels = list(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
    assert "auto_video_factory/web.py" in names
    assert "auto_video_factory/webui/index.html" in names
