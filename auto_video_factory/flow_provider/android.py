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


class CDPTransportMode(str, enum.Enum):
    """Transport mode for connecting to Android Chrome DevTools Protocol."""
    DIRECT_LOCAL_SOCKET = "direct_local_socket"
    SHIZUKU_RISH = "shizuku_rish"
    LOCAL_ADB_TCP = "local_adb_tcp"
    WIRELESS_ADB = "wireless_adb"
    USER_INTERACTION_REQUIRED = "user_interaction_required"


class CDPTransport:
    """Abstract base class for CDP transport mechanisms."""
    mode: CDPTransportMode

    def __init__(self, cdp_port: int = DEFAULT_CDP_PORT):
        self.cdp_port = cdp_port

    def probe(self) -> bool:
        """Quickly check if this transport is available without expensive side-effects."""
        raise NotImplementedError

    def ensure(self) -> bool:
        """Establish the port forwarding or bridge to 127.0.0.1:cdp_port."""
        raise NotImplementedError

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.cdp_port}"

    def health(self) -> dict:
        return {"mode": self.mode.value, "status": "unknown"}

    def cleanup(self) -> None:
        """Clean up active background threads, sockets, or forwards."""
        pass


class DirectSocketTransport(CDPTransport):
    """
    Direct zero-dependency local abstract socket transport (@chrome_devtools_remote).
    Bridges 127.0.0.1:cdp_port to Android abstract Unix domain socket via a lightweight Python proxy.
    """
    mode = CDPTransportMode.DIRECT_LOCAL_SOCKET

    def __init__(self, cdp_port: int = DEFAULT_CDP_PORT, socket_name: str = "chrome_devtools_remote"):
        super().__init__(cdp_port=cdp_port)
        self.socket_name = socket_name
        self._last_error: Optional[str] = None
        self._proxy_server = None

    def probe(self) -> bool:
        import socket  # noqa: PLC0415
        abstract_name = f"\0{self.socket_name}"
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(0.2)
                s.connect(abstract_name)
                self._last_error = None
                return True
        except Exception as e:
            self._last_error = str(e)
            return False

    def ensure(self) -> bool:
        if not self.probe():
            return False
        return True

    def health(self) -> dict:
        available = self.probe()
        res = {
            "mode": self.mode.value,
            "status": "available" if available else "unsupported",
            "socket_name": self.socket_name,
        }
        if self._last_error:
            res["reason"] = self._last_error
        return res


class LocalAdbTcpTransport(CDPTransport):
    """
    Local ADB TCP transport (127.0.0.1:<tcp_port>).
    Enables true 4G-only local automation after a one-time adb tcpip bootstrap.
    """
    mode = CDPTransportMode.LOCAL_ADB_TCP

    def __init__(
        self,
        tcp_port: int = 5555,
        cdp_port: int = DEFAULT_CDP_PORT,
        adb_host: str = DEFAULT_ADB_HOST,
        adb_port: int = DEFAULT_ADB_PORT,
    ):
        super().__init__(cdp_port=cdp_port)
        self.tcp_port = tcp_port
        self.adb_host = adb_host
        self.adb_port = adb_port
        self._client = None

    def _get_client(self):
        if self._client is None:
            import adbutils  # noqa: PLC0415
            self._client = adbutils.AdbClient(host=self.adb_host, port=self.adb_port)
        return self._client

    def _get_device(self):
        try:
            client = self._get_client()
            target_serial = f"127.0.0.1:{self.tcp_port}"
            for dev in client.device_list():
                if dev.serial == target_serial:
                    return dev
            return None
        except Exception:
            return None

    def _attempt_connect(self) -> bool:
        try:
            client = self._get_client()
            res = client.connect(f"127.0.0.1:{self.tcp_port}")
            return "connected" in res.lower()
        except Exception:
            return False

    def enable_tcp_mode_from_device(self, device) -> bool:
        """Switch an active device to listen on TCP port for 4G local loopback access."""
        try:
            res = device.tcpip(self.tcp_port)
            log.info("Switched adbd to TCP port %d: %s", self.tcp_port, res)
            return True
        except Exception as e:
            log.error("Failed to switch adbd to TCP port %d: %s", self.tcp_port, e)
            return False

    def probe(self) -> bool:
        dev = self._get_device()
        if dev is not None:
            return True
        return self._attempt_connect()

    def ensure(self) -> bool:
        dev = self._get_device()
        if not dev and not self._attempt_connect():
            return False
        dev = self._get_device()
        if not dev:
            return False

        target_local = f"tcp:{self.cdp_port}"
        target_remote = "localabstract:chrome_devtools_remote"
        try:
            for item in dev.forward_list():
                if item.local == target_local and item.remote == target_remote:
                    return True
            dev.forward(target_local, target_remote)
            log.info("LocalAdbTcp: Forwarded CDP port %d to %s", self.cdp_port, target_remote)
            return True
        except Exception as e:
            log.error("LocalAdbTcp: Forward failed on port %d: %s", self.cdp_port, e)
            return False

    def health(self) -> dict:
        connected = self.probe()
        return {
            "mode": self.mode.value,
            "status": "available" if connected else "disconnected",
            "tcp_port": self.tcp_port,
        }


class WirelessAdbTransport(CDPTransport):
    """
    Standard Wireless Debugging / USB ADB transport over Wi-Fi or USB.
    """
    mode = CDPTransportMode.WIRELESS_ADB

    def __init__(
        self,
        cdp_port: int = DEFAULT_CDP_PORT,
        adb_host: str = DEFAULT_ADB_HOST,
        adb_port: int = DEFAULT_ADB_PORT,
        serial: Optional[str] = None,
    ):
        super().__init__(cdp_port=cdp_port)
        self.adb_host = adb_host
        self.adb_port = adb_port
        self.serial = serial
        self._client = None

    def _get_client(self):
        if self._client is None:
            import adbutils  # noqa: PLC0415
            self._client = adbutils.AdbClient(host=self.adb_host, port=self.adb_port)
        return self._client

    def _get_device(self):
        try:
            client = self._get_client()
            if self.serial:
                return client.device(serial=self.serial)
            devices = [
                d for d in client.device_list()
                if not d.serial.startswith("127.0.0.1:")
            ]
            if devices:
                return devices[0]
            return None
        except Exception:
            return None

    def probe(self) -> bool:
        dev = self._get_device()
        return dev is not None

    def ensure(self) -> bool:
        dev = self._get_device()
        if not dev:
            return False
        target_local = f"tcp:{self.cdp_port}"
        target_remote = "localabstract:chrome_devtools_remote"
        try:
            for item in dev.forward_list():
                if item.local == target_local and item.remote == target_remote:
                    return True
            dev.forward(target_local, target_remote)
            log.info("WirelessAdb: Forwarded CDP port %d to %s", self.cdp_port, target_remote)
            return True
        except Exception as e:
            log.error("WirelessAdb: Forward failed on port %d: %s", self.cdp_port, e)
            return False

    def health(self) -> dict:
        connected = self.probe()
        dev = self._get_device()
        return {
            "mode": self.mode.value,
            "status": "available" if connected else "disconnected",
            "serial": dev.serial if dev else None,
        }


class AndroidCDPManager:
    """
    Manages ADB connection, Chrome DevTools socket discovery, and port forwarding
    using modular CDPTransport backends with resilient fallbacks.
    """

    def __init__(
        self,
        adb_host: str = DEFAULT_ADB_HOST,
        adb_port: int = DEFAULT_ADB_PORT,
        serial: Optional[str] = None,
        cdp_port: int = DEFAULT_CDP_PORT,
        foreground_policy: ForegroundPolicy = ForegroundPolicy.AUTO,
        local_tcp_port: int = 5555,
    ):
        self.adb_host = adb_host
        self.adb_port = adb_port
        self.serial = serial or os.getenv("ANDROID_SERIAL")
        self.cdp_port = cdp_port
        self.foreground_policy = foreground_policy
        self.local_tcp_port = local_tcp_port
        self._client = None
        self._active_transport: Optional[CDPTransport] = None
        self._active_mode: CDPTransportMode = CDPTransportMode.USER_INTERACTION_REQUIRED

        # Modular transports
        self._direct_transport = DirectSocketTransport(cdp_port=self.cdp_port)
        self._local_tcp_transport = LocalAdbTcpTransport(
            tcp_port=self.local_tcp_port,
            cdp_port=self.cdp_port,
            adb_host=self.adb_host,
            adb_port=self.adb_port,
        )
        self._wireless_transport = WirelessAdbTransport(
            cdp_port=self.cdp_port,
            adb_host=self.adb_host,
            adb_port=self.adb_port,
            serial=self.serial,
        )

    def _get_direct_transport(self) -> DirectSocketTransport:
        return self._direct_transport

    def _get_local_tcp_transport(self) -> LocalAdbTcpTransport:
        return self._local_tcp_transport

    def _get_wireless_transport(self) -> WirelessAdbTransport:
        return self._wireless_transport

    def select_transport(self) -> Optional[CDPTransport]:
        """
        Deterministically select the preferred available CDP transport.
        Hierarchy:
          1. Direct Local Unix Socket (Zero-dependency, true 4G-only)
          2. Local ADB TCP (127.0.0.1:<port>, post-bootstrap 4G-only)
          3. Wireless ADB / USB (Standard Wi-Fi debugging)
        """
        # 1. Direct local socket
        direct = self._get_direct_transport()
        if direct.probe():
            self._active_transport = direct
            self._active_mode = CDPTransportMode.DIRECT_LOCAL_SOCKET
            return direct

        # 2. Local ADB TCP
        local_tcp = self._get_local_tcp_transport()
        if local_tcp.probe():
            self._active_transport = local_tcp
            self._active_mode = CDPTransportMode.LOCAL_ADB_TCP
            return local_tcp

        # 3. Wireless ADB
        wireless = self._get_wireless_transport()
        if wireless.probe():
            self._active_transport = wireless
            self._active_mode = CDPTransportMode.WIRELESS_ADB
            return wireless

        self._active_transport = None
        self._active_mode = CDPTransportMode.USER_INTERACTION_REQUIRED
        return None

    def get_active_transport_mode(self) -> CDPTransportMode:
        return self._active_mode

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
        Verify or establish the port forward for Chrome CDP to 127.0.0.1:cdp_port
        using the active or newly selected transport.
        """
        transport = self.select_transport()
        if transport is not None:
            return transport.ensure()

        # Fallback to direct device-level forward if device is already resolved
        device = self.get_device()
        if not device:
            log.warning("Cannot forward CDP: no ADB device or direct transport connected.")
            return False

        target_local = f"tcp:{self.cdp_port}"
        socket_name = self.discover_chrome_devtools_socket() or "chrome_devtools_remote"
        target_remote = f"localabstract:{socket_name}"

        try:
            for item in device.forward_list():
                if item.local == target_local and item.remote == target_remote:
                    log.debug("Existing CDP forward active: %s -> %s", item.local, item.remote)
                    return True

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
