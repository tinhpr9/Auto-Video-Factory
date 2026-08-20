"""
Android ADB and Chrome DevTools Protocol (CDP) manager for Auto-Video-Factory.

Provides durable, production-grade management of:
  1. ADB Wireless Debugging / USB connection discovery & verification via adbutils.
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
import time
from typing import Iterator, Optional, Tuple

log = logging.getLogger(__name__)

DEFAULT_ADB_HOST = "127.0.0.1"
DEFAULT_ADB_PORT = 5037
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
    Manages ADB connection, Chrome DevTools socket discovery, and port forwarding
    using openatx/adbutils with resilient fallbacks.
    """

    def __init__(
        self,
        adb_host: str = DEFAULT_ADB_HOST,
        adb_port: int = DEFAULT_ADB_PORT,
        serial: Optional[str] = None,
        cdp_port: int = DEFAULT_CDP_PORT,
        foreground_policy: ForegroundPolicy = ForegroundPolicy.AUTO,
    ):
        self.adb_host = adb_host
        self.adb_port = adb_port
        self.serial = serial or os.getenv("ANDROID_SERIAL")
        self.cdp_port = cdp_port
        self.foreground_policy = foreground_policy
        self._client = None

    def _get_client(self):
        """Lazy-import and return adbutils.AdbClient."""
        if self._client is None:
            try:
                import adbutils  # noqa: PLC0415
                self._client = adbutils.AdbClient(host=self.adb_host, port=self.adb_port)
            except ImportError as exc:
                raise ImportError(
                    "AndroidCDPManager requires 'adbutils'. "
                    "Install with: pip install 'auto-video-factory[prod]' or pip install adbutils"
                ) from exc
        return self._client

    def get_device(self):
        """
        Resolve the active AdbDevice.
        If serial is explicitly configured, returns that device.
        If not, selects the single connected device, or returns None if 0 devices.
        """
        try:
            client = self._get_client()
            if self.serial:
                return client.device(serial=self.serial)
            devices = client.device_list()
            if not devices:
                return None
            if len(devices) > 1:
                log.warning(
                    "Multiple ADB devices found (%d). Selecting first device: %s",
                    len(devices),
                    devices[0].serial,
                )
            return devices[0]
        except Exception as e:
            log.debug("Failed to resolve ADB device: %s", e)
            return None

    def is_adb_connected(self) -> bool:
        """Check if at least one Android device is connected via ADB."""
        try:
            device = self.get_device()
            return device is not None
        except Exception:
            return False

    def discover_chrome_devtools_socket(self) -> Optional[str]:
        """
        Discover the native Chrome abstract devtools socket from /proc/net/unix.
        Typically 'chrome_devtools_remote' or 'chrome_devtools_remote_<PID>'.
        """
        device = self.get_device()
        if not device:
            return None

        try:
            stdout = device.shell("cat /proc/net/unix")
            if not stdout:
                return "chrome_devtools_remote"

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
        except Exception as e:
            log.warning("Socket discovery failed: %s", e)
            return "chrome_devtools_remote"

    def ensure_cdp_forward(self) -> bool:
        """
        Verify or establish the ADB port forward for Chrome CDP to 127.0.0.1:cdp_port.
        """
        device = self.get_device()
        if not device:
            log.warning("Cannot forward CDP: no ADB device connected.")
            return False

        target_local = f"tcp:{self.cdp_port}"
        socket_name = self.discover_chrome_devtools_socket() or "chrome_devtools_remote"
        target_remote = f"localabstract:{socket_name}"

        try:
            # Check existing active forwards
            for item in device.forward_list():
                if item.local == target_local and item.remote == target_remote:
                    log.debug("Existing CDP forward active: %s -> %s", item.local, item.remote)
                    return True

            # Establish forward
            device.forward(target_local, target_remote)
            log.info("Forwarded CDP port %d to %s", self.cdp_port, target_remote)
            return True
        except Exception as e:
            log.error("Failed to forward CDP port %d: %s", self.cdp_port, e)
            return False

    def get_current_foreground_app(self) -> Tuple[Optional[str], Optional[str]]:
        """
        Inspect current foreground package and activity.
        Uses adbutils app_current() with dumpsys fallback.
        Returns: (package_name, activity_name) or (None, None).
        """
        device = self.get_device()
        if not device:
            return None, None

        # 1. Primary: adbutils app_current()
        try:
            curr = device.app_current()
            if curr and curr.package:
                act = curr.activity or ""
                if act.startswith("."):
                    act = curr.package + act
                return curr.package, act
        except Exception as e:
            log.debug("app_current() lookup failed, falling back to dumpsys: %s", e)

        # 2. Fallback: dumpsys window
        try:
            out = device.shell("dumpsys window")
            if out:
                m = re.search(r"mCurrentFocus=Window\{[^\}]*\s+u0\s+([^/\s\}]+)/([^\s\}]+)", out)
                if m:
                    pkg, act = m.group(1), m.group(2)
                    if act.startswith("."):
                        act = pkg + act
                    return pkg, act
        except Exception:
            pass

        # 3. Fallback: dumpsys activity activities
        try:
            out = device.shell("dumpsys activity activities")
            if out:
                m = re.search(r"mFocusedApp=ActivityRecord\{[^\}]*\s+u0\s+([^/\s\}]+)/([^\s\}]+)", out)
                if m:
                    pkg, act = m.group(1), m.group(2)
                    if act.startswith("."):
                        act = pkg + act
                    return pkg, act
        except Exception:
            pass

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

        device = self.get_device()
        if not device:
            return False

        log.info("Restoring previous user app: %s/%s", package, activity)
        try:
            res = device.shell(f"am start --activity-brought-to-front -n {package}/{activity}")
            if "Error" in res or "error" in res.lower():
                # Fallback without --activity-brought-to-front
                device.shell(f"am start -n {package}/{activity}")
            return True
        except Exception as e:
            log.warning("Failed to restore previous app %s/%s: %s", package, activity, e)
            return False

    def bring_chrome_to_foreground(self, flow_url: Optional[str] = None) -> bool:
        """Briefly bring the existing Chrome Flow tab to the foreground for UI submit."""
        device = self.get_device()
        if not device:
            return False

        try:
            if flow_url:
                device.shell(f"am start -n com.android.chrome/com.google.android.apps.chrome.Main -d '{flow_url}'")
            else:
                device.shell("am start -n com.android.chrome/com.google.android.apps.chrome.Main")
            return True
        except Exception as e:
            log.warning("Failed to bring Chrome to foreground: %s", e)
            return False

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
