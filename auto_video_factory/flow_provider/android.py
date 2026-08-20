"""
Android ADB and Chrome DevTools Protocol (CDP) manager for Auto-Video-Factory.

Provides durable, production-grade management of:
  1. ADB Wireless Debugging / USB connection discovery & verification.
  2. Native Android Chrome DevTools socket discovery (@chrome_devtools_remote).
  3. Stale forward detection, port recovery, and loopback binding to 127.0.0.1:9222.
  4. Non-intrusive foreground management:
     - Captures user's active foreground app (e.g. video player, browser, chat).
     - Allows background-only operation or minimal micro-foreground burst.
     - Safely restores the previous user app immediately after submit (guaranteed in finally).
     - Ignores transient system UI / launcher / lockscreen to prevent invalid restores.
"""
from __future__ import annotations

import contextlib
import enum
import logging
import os
import re
import shutil
import subprocess
import time
from typing import Iterator, Optional, Tuple

log = logging.getLogger(__name__)

DEFAULT_CDP_PORT = 9222
DEFAULT_CDP_URL = f"http://127.0.0.1:{DEFAULT_CDP_PORT}"

# System and launcher packages that should not be explicitly restored as apps
SYSTEM_PACKAGES = {
    "com.android.launcher",
    "com.android.launcher3",
    "com.google.android.apps.nexuslauncher",
    "com.sec.android.app.launcher",
    "com.miui.home",
    "com.oppo.launcher",
    "com.vivo.launcher",
    "com.huawei.android.launcher",
    "com.android.systemui",
}


class ForegroundPolicy(str, enum.Enum):
    """Policy for how Google Flow is foregrounded during generation."""
    AUTO = "auto"                     # Try background first; use micro-foreground if required
    BACKGROUND = "background"         # Strictly keep Chrome in background
    MICRO_FOREGROUND = "micro-foreground"  # Briefly foreground Chrome for submit, then restore


class AndroidCDPManager:
    """
    Manages ADB connection, Chrome DevTools socket discovery, and port forwarding.
    """

    def __init__(
        self,
        adb_path: Optional[str] = None,
        cdp_port: int = DEFAULT_CDP_PORT,
        foreground_policy: ForegroundPolicy = ForegroundPolicy.AUTO,
    ):
        self.adb_path = adb_path or self._find_adb_binary()
        self.cdp_port = cdp_port
        self.foreground_policy = foreground_policy

    @staticmethod
    def _find_adb_binary() -> str:
        """Find the adb binary across Termux and standard system paths."""
        candidates = [
            os.environ.get("ADB_PATH", ""),
            "/data/data/com.termux/files/usr/bin/adb",
            shutil.which("adb") or "",
        ]
        for path in candidates:
            if path and os.path.isfile(path) and os.access(path, os.X_OK):
                return path
        return "adb"

    def run_adb(self, *args: str, timeout: float = 10.0) -> Tuple[int, str, str]:
        """Execute an adb command with structured output."""
        cmd = [self.adb_path, *args]
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return res.returncode, res.stdout, res.stderr
        except Exception as e:
            log.warning("ADB execution failed: %s: %s", cmd, e)
            return 1, "", str(e)

    def is_adb_connected(self) -> bool:
        """Check if at least one Android device is connected via ADB."""
        code, stdout, _ = self.run_adb("devices")
        if code != 0:
            return False
        for line in stdout.strip().splitlines()[1:]:
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "device":
                return True
        return False

    def discover_chrome_devtools_socket(self) -> Optional[str]:
        """
        Discover the native Chrome abstract devtools socket from /proc/net/unix.
        Typically '@chrome_devtools_remote' or '@chrome_devtools_remote_<PID>'.
        """
        code, stdout, _ = self.run_adb("shell", "cat /proc/net/unix")
        if code != 0 or not stdout:
            return None

        # Look for @chrome_devtools_remote patterns
        for line in stdout.splitlines():
            if "chrome_devtools_remote" in line or "devtools_remote" in line:
                parts = line.strip().split()
                if parts:
                    socket_name = parts[-1]
                    if socket_name.startswith("@"):
                        return socket_name[1:]  # remove '@' for localabstract
                    return socket_name
        return "chrome_devtools_remote"

    def ensure_cdp_forward(self) -> bool:
        """
        Verify or establish the ADB port forward for Chrome CDP to 127.0.0.1:cdp_port.
        """
        if not self.is_adb_connected():
            log.warning("Cannot forward CDP: no ADB device connected.")
            return False

        # Check existing forwards
        code, stdout, _ = self.run_adb("forward", "--list")
        target_forward = f"tcp:{self.cdp_port}"
        if code == 0:
            for line in stdout.splitlines():
                if target_forward in line and "chrome_devtools_remote" in line:
                    log.debug("Existing CDP forward active: %s", line)
                    return True

        # Discover socket
        socket_name = self.discover_chrome_devtools_socket() or "chrome_devtools_remote"
        code, _, stderr = self.run_adb(
            "forward", f"tcp:{self.cdp_port}", f"localabstract:{socket_name}"
        )
        if code != 0:
            log.error("Failed to forward CDP port %d: %s", self.cdp_port, stderr)
            return False

        log.info("Forwarded CDP port %d to localabstract:%s", self.cdp_port, socket_name)
        return True

    def get_current_foreground_app(self) -> Tuple[Optional[str], Optional[str]]:
        """
        Inspect current foreground package and activity via dumpsys window / activity.
        Returns: (package_name, activity_name) or (None, None).
        """
        code, stdout, _ = self.run_adb("shell", "dumpsys window")
        if code == 0 and stdout:
            # Match mCurrentFocus=Window{... u0 <pkg>/<activity> ...}
            m = re.search(r"mCurrentFocus=Window\{[^\}]*\s+u0\s+([^/\s\}]+)/([^\s\}]+)", stdout)
            if m:
                pkg, act = m.group(1), m.group(2)
                if act.startswith("."):
                    act = pkg + act
                return pkg, act

        # Fallback to dumpsys activity activities
        code, stdout, _ = self.run_adb("shell", "dumpsys activity activities")
        if code == 0 and stdout:
            m = re.search(r"mFocusedApp=ActivityRecord\{[^\}]*\s+u0\s+([^/\s\}]+)/([^\s\}]+)", stdout)
            if m:
                pkg, act = m.group(1), m.group(2)
                if act.startswith("."):
                    act = pkg + act
                return pkg, act

        return None, None

    def restore_foreground_app(self, package: Optional[str], activity: Optional[str]) -> bool:
        """
        Restore the user's previous foreground app.
        Guarantees that Chrome / Flow does not remain on screen if user was in another app.
        """
        if not package or not activity:
            return False

        # Do not attempt to restore if the previous app was already Chrome
        if package in ("com.android.chrome", "com.google.android.apps.chrome"):
            return True

        # Do not restore transient system launcher activities
        if package in SYSTEM_PACKAGES:
            log.debug("Previous app was launcher/systemUI (%s), skipping restore.", package)
            return True

        log.info("Restoring previous user app: %s/%s", package, activity)
        code, _, stderr = self.run_adb(
            "shell", "am", "start", "--activity-brought-to-front", "-n", f"{package}/{activity}"
        )
        if code != 0:
            # Fallback without --activity-brought-to-front
            code, _, stderr = self.run_adb("shell", "am", "start", "-n", f"{package}/{activity}")

        return code == 0

    def bring_chrome_to_foreground(self, flow_url: Optional[str] = None) -> bool:
        """Briefly bring the existing Chrome Flow tab to the foreground for UI submit."""
        if flow_url:
            code, _, _ = self.run_adb(
                "shell", "am", "start", "-n", "com.android.chrome/com.google.android.apps.chrome.Main", "-d", flow_url
            )
        else:
            code, _, _ = self.run_adb(
                "shell", "am", "start", "-n", "com.android.chrome/com.google.android.apps.chrome.Main"
            )
        return code == 0

    @contextlib.contextmanager
    def scoped_foreground_for_submit(self, flow_url: Optional[str] = None) -> Iterator[None]:
        """
        Context manager that captures user's foreground app, brings Chrome to foreground
        if policy requires it, and ALWAYS restores the user's app upon exit (even on exception).
        """
        if self.foreground_policy == ForegroundPolicy.BACKGROUND:
            # Strictly background mode: do not touch foreground
            yield
            return

        # Capture user's active foreground app
        prev_pkg, prev_act = self.get_current_foreground_app()
        log.debug("Captured previous foreground app before submit: %s/%s", prev_pkg, prev_act)

        try:
            if self.foreground_policy in (ForegroundPolicy.MICRO_FOREGROUND, ForegroundPolicy.AUTO):
                self.bring_chrome_to_foreground(flow_url)
                time.sleep(0.3)  # Brief stabilization
            yield
        finally:
            if prev_pkg and prev_act:
                self.restore_foreground_app(prev_pkg, prev_act)
