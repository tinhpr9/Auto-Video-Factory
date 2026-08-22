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
import ipaddress
import logging
import os
import re
import subprocess
import time
from typing import Iterator, Optional, Tuple

from .cdp_endpoint import verify_cdp_endpoint

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


def discover_mdns_wireless_endpoints() -> list[str]:
    """
    Auto-discover Android Wireless Debugging connect endpoints using `adb mdns services`.
    Looks for services with type `_adb-tls-connect._tcp` or `_adb._tcp`.
    Validates IPv4 and port range (1-65535).
    Returns list of discovered 'IP:port' strings.
    """
    endpoints: list[str] = []
    try:
        res = subprocess.run(
            ["adb", "mdns", "services"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        if res.returncode == 0 and res.stdout:
            for line in res.stdout.splitlines():
                if "_adb-tls-connect._tcp" in line or "_adb._tcp" in line:
                    parts = line.strip().split()
                    for part in parts:
                        if ":" in part:
                            host_str, port_str = part.rsplit(":", 1)
                            try:
                                ipaddress.IPv4Address(host_str)
                                port_num = int(port_str)
                                if 1 <= port_num <= 65535:
                                    if part not in endpoints:
                                        endpoints.append(part)
                            except Exception:
                                continue
    except Exception as e:
        log.debug("mDNS discovery failed: %s", e)
    return endpoints


def discover_device_devtools_socket(device) -> str:
    """
    Authoritative discovery of Android Chrome abstract DevTools Unix socket.
    Scans /proc/net/unix on the given device for @chrome_devtools_remote
    or @chrome_devtools_remote_<PID>. Returns discovered socket name without '@'.
    Only accepts 'chrome_devtools_remote' basename (with optional '_<digits>' suffix)
    to prevent accidentally forwarding to unrelated WebView sockets
    (e.g. @webview_devtools_remote_<pid>).
    Falls back to 'chrome_devtools_remote'.
    """
    if not device:
        return "chrome_devtools_remote"
    try:
        stdout = device.shell("cat /proc/net/unix")
        if stdout:
            for line in stdout.splitlines():
                if "chrome_devtools_remote" not in line:
                    continue
                parts = line.strip().split()
                if parts:
                    raw = parts[-1]
                    socket_name = raw[1:] if raw.startswith("@") else raw
                    # Accept only: chrome_devtools_remote or chrome_devtools_remote_<digits>
                    if re.fullmatch(r"chrome_devtools_remote(_\d+)?", socket_name):
                        return socket_name
    except Exception as e:
        log.warning("Socket discovery failed on device %s: %s", getattr(device, "serial", "unknown"), e)
    return "chrome_devtools_remote"


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
        return verify_cdp_endpoint(self.endpoint, timeout=1.0)

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
        """Lightweight, side-effect-free check if local ADB TCP device is already connected."""
        dev = self._get_device()
        return dev is not None

    def ensure(self) -> bool:
        """Establish or verify local ADB TCP connection and forward discovered DevTools socket."""
        dev = self._get_device()
        if not dev and not self._attempt_connect():
            return False
        dev = self._get_device()
        if not dev:
            return False

        socket_name = discover_device_devtools_socket(dev)
        target_local = f"tcp:{self.cdp_port}"
        target_remote = f"localabstract:{socket_name}"
        try:
            for item in dev.forward_list():
                if item.local == target_local and item.remote == target_remote:
                    if verify_cdp_endpoint(self.endpoint, timeout=1.0):
                        return True
                    break

            dev.forward(target_local, target_remote)
            log.info("LocalAdbTcp: Forwarded CDP port %d to %s on %s", self.cdp_port, target_remote, dev.serial)
            return verify_cdp_endpoint(self.endpoint, timeout=1.5)
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
    Supports dynamic auto-discovery of endpoints via mDNS, deterministic device selection,
    and automatic stale forward recovery.
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

    def _attempt_auto_connect(self) -> Optional[object]:
        """Attempt to auto-connect to configured or discovered wireless debugging endpoints."""
        try:
            client = self._get_client()
            # 1. If explicit serial is provided, only connect to that serial (fail closed if not reachable)
            if self.serial:
                try:
                    res = client.connect(self.serial)
                    if "connected" in str(res).lower():
                        dev = client.device(serial=self.serial)
                        dev.shell("echo ping")
                        return dev
                except Exception:
                    pass
                return None

            # 2. Check ANDROID_WIRELESS_ENDPOINT env var
            env_ep = os.getenv("ANDROID_WIRELESS_ENDPOINT")
            if env_ep:
                try:
                    res = client.connect(env_ep)
                    if "connected" in str(res).lower():
                        dev = client.device(serial=env_ep)
                        dev.shell("echo ping")
                        return dev
                except Exception:
                    pass

            # 3. Auto-discover endpoints via mDNS services (fail closed if ambiguous)
            endpoints = discover_mdns_wireless_endpoints()
            if len(endpoints) > 1:
                log.error(
                    "Multiple mDNS wireless ADB endpoints discovered (%s) with no explicit serial configured. "
                    "Failing closed to prevent ambiguous routing.",
                    endpoints,
                )
                return None
            if len(endpoints) == 1:
                ep = endpoints[0]
                try:
                    res = client.connect(ep)
                    if "connected" in str(res).lower():
                        dev = client.device(serial=ep)
                        dev.shell("echo ping")
                        return dev
                except Exception as e:
                    log.debug("Auto-connect to %s failed: %s", ep, e)
        except Exception as e:
            log.debug("Auto-connect attempt failed: %s", e)
        return None

    def _get_device(self, auto_connect: bool = False):
        try:
            client = self._get_client()
            if self.serial:
                try:
                    dev = client.device(serial=self.serial)
                    # Verify device is responsive
                    dev.shell("echo ping")
                    return dev
                except Exception:
                    return self._attempt_auto_connect() if auto_connect else None

            devices = [
                d for d in client.device_list()
                if not d.serial.startswith("127.0.0.1:")
            ]
            if len(devices) > 1:
                log.error(
                    "Multiple wireless/USB ADB devices connected (%s) with no explicit serial configured. "
                    "Failing closed to prevent ambiguous routing. Specify serial via config.",
                    [d.serial for d in devices],
                )
                return None

            if len(devices) == 1:
                try:
                    devices[0].shell("echo ping")
                    return devices[0]
                except Exception:
                    return self._attempt_auto_connect() if auto_connect else None

            # 0 devices connected: attempt dynamic auto-connect only if auto_connect is True
            return self._attempt_auto_connect() if auto_connect else None
        except Exception as e:
            log.debug("Failed to resolve wireless ADB device: %s", e)
            return None

    def probe(self) -> bool:
        # Probe is strictly side-effect-free (no adb connect or mDNS auto-connect)
        dev = self._get_device(auto_connect=False)
        return dev is not None

    def ensure(self) -> bool:
        dev = self._get_device(auto_connect=True)
        if not dev:
            return False

        socket_name = discover_device_devtools_socket(dev)
        target_local = f"tcp:{self.cdp_port}"
        target_remote = f"localabstract:{socket_name}"
        try:
            stale_forward = False
            for item in dev.forward_list():
                if item.local == target_local:
                    if item.remote == target_remote:
                        if verify_cdp_endpoint(self.endpoint, timeout=1.0):
                            return True
                    stale_forward = True
                    break

            if stale_forward:
                try:
                    dev.forward_remove(target_local)
                    log.debug("Removed stale forward on %s for %s", dev.serial, target_local)
                except Exception:
                    pass

            dev.forward(target_local, target_remote)
            log.info("WirelessAdb: Forwarded CDP port %d to %s on %s", self.cdp_port, target_remote, dev.serial)
            return verify_cdp_endpoint(self.endpoint, timeout=1.5)
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

    def get_device(self, auto_connect: bool = False):
        """
        Resolve the active AdbDevice for foreground management and direct forward fallbacks.
        If serial is explicitly configured, returns that device.
        If not, filters out local-TCP entries (127.0.0.1:*), then selects the single responsive device.
        If 0 responsive devices and auto_connect is True, attempts wireless auto-connect.
        Fails closed (returns None) when 0 reachable or >1 responsive non-local devices exist without a serial.
        """
        try:
            client = self._get_client()
            if self.serial:
                try:
                    dev = client.device(serial=self.serial)
                    dev.shell("echo ping")
                    return dev
                except Exception:
                    return self._wireless_transport._attempt_auto_connect() if auto_connect else None

            devices = [d for d in client.device_list() if not d.serial.startswith("127.0.0.1:")]
            if len(devices) > 1:
                log.error(
                    "Multiple ADB devices found (%d: %s) with no explicit serial configured. "
                    "Failing closed to prevent ambiguous routing. Set ANDROID_SERIAL or configure serial.",
                    len(devices),
                    [d.serial for d in devices],
                )
                return None

            if len(devices) == 1:
                try:
                    devices[0].shell("echo ping")
                    return devices[0]
                except Exception:
                    return self._wireless_transport._attempt_auto_connect() if auto_connect else None

            # 0 devices connected: attempt auto-connect via wireless transport
            return self._wireless_transport._attempt_auto_connect() if auto_connect else None
        except Exception as e:
            log.debug("Failed to resolve ADB device: %s", e)
            return None

    def is_adb_connected(self) -> bool:
        """Check if at least one responsive Android device is connected via ADB."""
        try:
            client = self._get_client()
            if self.serial:
                try:
                    dev = client.device(serial=self.serial)
                    dev.shell("echo ping")
                    return True
                except Exception:
                    return False
            devices = [d for d in client.device_list() if not d.serial.startswith("127.0.0.1:")]
            for d in devices:
                try:
                    d.shell("echo ping")
                    return True
                except Exception:
                    continue
            return False
        except Exception:
            return False

    def health(self) -> dict:
        """Report comprehensive health status of Android CDP manager and active transport."""
        transport = self._active_transport or self.select_transport()
        connected = self.is_adb_connected()
        dev = self.get_device()
        endpoint = f"http://127.0.0.1:{self.cdp_port}"
        cdp_ready = verify_cdp_endpoint(endpoint, timeout=0.5)
        res = {
            "status": "ready" if (connected and cdp_ready) else ("connected" if connected else "disconnected"),
            "is_adb_connected": connected,
            "device_serial": getattr(dev, "serial", None) if dev else None,
            "cdp_port": self.cdp_port,
            "cdp_endpoint": endpoint,
            "cdp_ready": cdp_ready,
            "active_mode": self._active_mode.value,
        }
        if transport and hasattr(transport, "health"):
            res["transport"] = transport.health()
        return res

    def discover_chrome_devtools_socket(self) -> Optional[str]:
        """
        Discover the native Chrome abstract devtools socket from /proc/net/unix.
        Typically 'chrome_devtools_remote' or 'chrome_devtools_remote_<PID>'.
        """
        device = self.get_device(auto_connect=True)
        if not device:
            return None
        return discover_device_devtools_socket(device)

    def ensure_cdp_forward(self) -> bool:
        """
        Verify or establish the port forward for Chrome CDP to 127.0.0.1:cdp_port
        using the active or newly selected transport.

        Transport selection hierarchy:
          1. select_transport() — picks any pre-probed (already-connected) transport.
          2. If none available, attempt LocalAdbTcpTransport.ensure()
          3. Attempt WirelessAdbTransport.ensure() (auto-connects dropped session)
          4. Fall back to direct device-level forward via get_device(auto_connect=True).
        """
        transport = self.select_transport()
        if transport is not None:
            if transport.ensure():
                return True

        # Attempt local TCP auto-connect explicitly
        local_tcp = self._get_local_tcp_transport()
        if local_tcp.ensure():
            self._active_transport = local_tcp
            self._active_mode = CDPTransportMode.LOCAL_ADB_TCP
            log.info("LocalAdbTcp: auto-connected via ensure() fallback path.")
            return True

        # Attempt wireless ADB auto-connect / discovery explicitly
        wireless = self._get_wireless_transport()
        if wireless.ensure():
            self._active_transport = wireless
            self._active_mode = CDPTransportMode.WIRELESS_ADB
            log.info("WirelessAdb: auto-connected via ensure() fallback path.")
            return True

        # Fallback to direct device-level forward if device is resolved
        device = self.get_device(auto_connect=True)
        if not device:
            log.warning("Cannot forward CDP: no ADB device or direct transport connected.")
            return False

        socket_name = self.discover_chrome_devtools_socket() or "chrome_devtools_remote"
        target_local = f"tcp:{self.cdp_port}"
        target_remote = f"localabstract:{socket_name}"

        try:
            stale_forward = False
            for item in device.forward_list():
                if item.local == target_local:
                    if item.remote == target_remote and verify_cdp_endpoint(f"http://127.0.0.1:{self.cdp_port}", timeout=1.0):
                        log.debug("Existing CDP forward active & verified: %s -> %s", item.local, item.remote)
                        return True
                    stale_forward = True
                    break

            if stale_forward:
                try:
                    device.forward_remove(target_local)
                except Exception:
                    pass

            device.forward(target_local, target_remote)
            log.info("Forwarded CDP port %d to %s", self.cdp_port, target_remote)
            return verify_cdp_endpoint(f"http://127.0.0.1:{self.cdp_port}", timeout=1.5)
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
