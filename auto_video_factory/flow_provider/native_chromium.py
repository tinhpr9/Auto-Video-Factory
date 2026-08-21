"""
Native Termux / Linux Chromium Process Lifecycle Manager for Auto-Video-Factory.

Manages direct, zero-ADB native Chromium execution on mobile and local environments:
  - Discovers native Chromium binary in Termux host, glibc prefix, or Linux system paths.
  - Controls lifecycle (start, stop, restart, ensure) with isolated persistent user profile.
  - Binds remote debugging exclusively to localhost (127.0.0.1:<configured_port>).
  - Uses bounded CDP endpoint verification (verify_cdp_endpoint) for authoritative readiness.
  - Guarantees zero ADB commands (ADB_COMMAND_COUNT=0) for daily operation.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .cdp_endpoint import (
    DEFAULT_CDP_HOST,
    DEFAULT_CDP_PORT,
    check_cdp_endpoint_detailed,
    verify_cdp_endpoint,
)

log = logging.getLogger(__name__)

DEFAULT_CHROMIUM_PROFILE_DIR = Path.home() / ".config" / "auto-video-factory" / "chromium-profile"


@dataclass
class NativeChromiumConfig:
    binary_path: Optional[str] = None
    host: str = DEFAULT_CDP_HOST
    port: int = DEFAULT_CDP_PORT
    user_data_dir: Optional[Path] = None
    headless: bool = True
    extra_flags: list[str] = field(default_factory=list)


class NativeChromiumManager:
    """
    Authoritative lifecycle manager for native Chromium instances.
    """

    CANDIDATE_BINARIES = [
        "/data/data/com.termux/files/usr/bin/chromium",
        "/data/data/com.termux/files/usr/glibc/bin/chromium",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ]

    def __init__(self, config: Optional[NativeChromiumConfig] = None):
        self.config = config or NativeChromiumConfig()
        self._process: Optional[subprocess.Popen] = None
        self._resolved_binary: Optional[str] = None

    @property
    def cdp_url(self) -> str:
        return f"http://{self.config.host}:{self.config.port}"

    def _find_binary(self) -> Optional[str]:
        if self._resolved_binary:
            return self._resolved_binary

        # 1. Explicit configuration or environment
        configured = self.config.binary_path or os.getenv("FLOW_CHROMIUM_BIN") or os.getenv("CHROMIUM_BIN")
        if configured:
            p = Path(configured)
            if p.exists() and os.access(p, os.X_OK):
                self._resolved_binary = str(p)
                return self._resolved_binary

        # 2. PATH lookup
        for name in ("chromium", "chromium-browser", "google-chrome-stable", "google-chrome", "chrome"):
            found = shutil.which(name)
            if found:
                self._resolved_binary = found
                return self._resolved_binary

        # 3. Known system candidate paths
        for candidate in self.CANDIDATE_BINARIES:
            p = Path(candidate)
            if p.exists() and os.access(p, os.X_OK):
                self._resolved_binary = str(p)
                return self._resolved_binary

        return None

    def get_binary_path(self) -> Optional[str]:
        return self._find_binary()

    def build_launch_args(self) -> list[str]:
        binary = self._find_binary() or "chromium"
        user_dir = self.config.user_data_dir or DEFAULT_CHROMIUM_PROFILE_DIR
        user_dir = Path(user_dir)
        user_dir.mkdir(parents=True, exist_ok=True)

        args = [
            binary,
            f"--remote-debugging-port={self.config.port}",
            f"--remote-debugging-address={self.config.host}",
            f"--user-data-dir={user_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-sync",
            "--disable-features=Translate",
            "--disable-component-update",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ]

        if self.config.headless:
            args.append("--headless=new")

        if self.config.extra_flags:
            args.extend(self.config.extra_flags)

        args.append("about:blank")
        return args

    def is_running(self) -> bool:
        if self._process is None:
            return False
        return self._process.poll() is None

    def is_ready(self, timeout: float = 1.0) -> bool:
        return verify_cdp_endpoint(self.cdp_url, timeout=timeout)

    def start(self) -> subprocess.Popen:
        binary = self._find_binary()
        if not binary:
            raise FileNotFoundError(
                "Chromium binary not found on this system. "
                "Set FLOW_CHROMIUM_BIN or install chromium (e.g. pkg install x11-repo && pkg install chromium)."
            )

        if self.is_running():
            log.debug("Native Chromium process is already running (PID: %s)", self._process.pid)
            return self._process

        args = self.build_launch_args()
        log.info("Launching native Chromium on %s: %s", self.cdp_url, " ".join(args[:4]))

        self._process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid if hasattr(os, "setsid") else None,
        )
        return self._process

    def stop(self, timeout: float = 3.0) -> None:
        if self._process is not None and self._process.poll() is None:
            log.info("Stopping native Chromium process (PID: %s)...", self._process.pid)
            try:
                self._process.terminate()
                self._process.wait(timeout=timeout)
            except Exception:
                try:
                    self._process.kill()
                    self._process.wait(timeout=1.0)
                except Exception as e:
                    log.debug("Failed to force kill Chromium process: %s", e)
        self._process = None

    def ensure(self, timeout: float = 10.0) -> bool:
        """
        Idempotently ensure native Chromium is running and exact CDP endpoint is ready.
        """
        # 1. Check if already running and endpoint is ready
        if self.is_ready(timeout=1.0):
            log.debug("Native CDP endpoint %s is already ready.", self.cdp_url)
            return True

        # 2. If endpoint is not ready, start fresh process
        self.stop(timeout=2.0)
        try:
            self.start()
        except Exception as e:
            log.error("Failed to start native Chromium: %s", e)
            return False

        # 3. Bounded readiness polling
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.is_running():
                log.error("Native Chromium process exited prematurely.")
                return False
            if self.is_ready(timeout=0.5):
                log.info("Native Chromium CDP endpoint %s is verified ready.", self.cdp_url)
                return True
            time.sleep(0.25)

        log.error("Native Chromium CDP readiness timed out after %s seconds on %s.", timeout, self.cdp_url)
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Native Chromium Lifecycle Manager")
    parser.add_argument("--port", type=int, default=DEFAULT_CDP_PORT, help="CDP port")
    parser.add_argument("--host", default=DEFAULT_CDP_HOST, help="CDP host")
    parser.add_argument("--ensure", action="store_true", help="Ensure running and ready")
    parser.add_argument("--check", action="store_true", help="Check readiness and exit")
    parser.add_argument("--headless", action="store_true", default=True, help="Run headless")
    args = parser.parse_args()

    cfg = NativeChromiumConfig(host=args.host, port=args.port, headless=args.headless)
    mgr = NativeChromiumManager(config=cfg)
    if args.check:
        status = check_cdp_endpoint_detailed(mgr.cdp_url)
        print(f"CDP endpoint {mgr.cdp_url} ready: {status.ready}")
        sys.exit(0 if status.ready else 1)
    elif args.ensure:
        ok = mgr.ensure()
        sys.exit(0 if ok else 1)
    else:
        mgr.start()


if __name__ == "__main__":
    main()
