"""
Shared Chrome DevTools Protocol (CDP) endpoint verification and readiness contract.

Guarantees bounded verification through the EXACT configured TCP endpoint:
  1. TCP connection to host:port succeeds (TCP_REACHABLE=PASS)
  2. HTTP GET /json/version returns 200 OK and valid metadata (CDP_HTTP_VERSION_VALID=PASS)
  3. HTTP GET /json or /json/list returns 200 OK and valid targets list (CDP_TARGET_DISCOVERY_VALID=PASS)

Readiness is NEVER inferred from process existence, socket existence, or adb forward rows alone.
"""
from __future__ import annotations

import json
import logging
import socket
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional, Tuple

log = logging.getLogger(__name__)

DEFAULT_CDP_HOST = "127.0.0.1"
DEFAULT_CDP_PORT = 9222
DEFAULT_CDP_URL = f"http://{DEFAULT_CDP_HOST}:{DEFAULT_CDP_PORT}"


@dataclass
class CDPEndpointStatus:
    ready: bool
    tcp_reachable: bool
    version_valid: bool
    targets_valid: bool
    endpoint_url: str
    browser_version: str = ""
    protocol_version: str = ""
    websocket_debugger_url: str = ""
    targets_count: int = 0
    error: Optional[str] = None


def parse_cdp_url(endpoint_url: str) -> Tuple[str, int, str]:
    """
    Parse a CDP URL into (host, port, base_http_url).
    Supports http://, https://, ws://, wss://, localhost:PORT, 127.0.0.1:PORT.
    """
    raw = endpoint_url.strip()
    if not raw:
        return DEFAULT_CDP_HOST, DEFAULT_CDP_PORT, DEFAULT_CDP_URL

    if not (raw.startswith("http://") or raw.startswith("https://") or raw.startswith("ws://") or raw.startswith("wss://")):
        raw = "http://" + raw

    parsed = urllib.parse.urlparse(raw)
    host = parsed.hostname or DEFAULT_CDP_HOST
    port = parsed.port or DEFAULT_CDP_PORT
    scheme = "https" if parsed.scheme in ("https", "wss") else "http"
    base_url = f"{scheme}://{host}:{port}"
    return host, port, base_url


def is_tcp_port_reachable(host: str, port: int, timeout: float = 1.0) -> bool:
    """Test raw TCP connection to the specified host and port within a bounded timeout."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect((host, port))
            return True
    except Exception:
        return False


def check_cdp_endpoint_detailed(endpoint_url: str, timeout: float = 3.0) -> CDPEndpointStatus:
    """
    Perform rigorous, bounded HTTP DevTools Protocol checks against the EXACT configured endpoint.
    """
    host, port, base_url = parse_cdp_url(endpoint_url)

    # 1. TCP Port Reachability Probe
    tcp_timeout = min(timeout, 1.0)
    if not is_tcp_port_reachable(host, port, timeout=tcp_timeout):
        return CDPEndpointStatus(
            ready=False,
            tcp_reachable=False,
            version_valid=False,
            targets_valid=False,
            endpoint_url=endpoint_url,
            error=f"TCP port unreachable at {host}:{port}",
        )

    # 2. Chrome DevTools /json/version check
    version_valid = False
    browser_ver = ""
    proto_ver = ""
    ws_url = ""
    http_timeout = max(0.5, timeout - tcp_timeout)

    try:
        req = urllib.request.Request(
            f"{base_url}/json/version",
            headers={"User-Agent": "AVF-CDP-Verifier/1.0", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=http_timeout) as resp:
            if resp.status == 200:
                raw_bytes = resp.read()
                data = json.loads(raw_bytes.decode("utf-8"))
                if isinstance(data, dict):
                    browser_ver = str(data.get("Browser", "") or data.get("Product", ""))
                    proto_ver = str(data.get("Protocol-Version", ""))
                    ws_url = str(data.get("webSocketDebuggerUrl", ""))
                    # Valid Chrome / Chromium DevTools endpoint always supplies Browser / Protocol-Version or webSocketDebuggerUrl
                    if browser_ver or proto_ver or ws_url or "Chrome" in str(data):
                        version_valid = True
    except Exception as e:
        log.debug("CDP /json/version check failed on %s: %s", base_url, e)

    if not version_valid:
        return CDPEndpointStatus(
            ready=False,
            tcp_reachable=True,
            version_valid=False,
            targets_valid=False,
            endpoint_url=endpoint_url,
            error=f"DevTools /json/version returned invalid or non-CDP response at {base_url}",
        )

    # 3. Chrome DevTools /json or /json/list target discovery check
    targets_valid = False
    targets_count = 0
    for target_path in ("/json", "/json/list"):
        try:
            req = urllib.request.Request(
                f"{base_url}{target_path}",
                headers={"User-Agent": "AVF-CDP-Verifier/1.0", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=http_timeout) as resp:
                if resp.status == 200:
                    raw_bytes = resp.read()
                    targets = json.loads(raw_bytes.decode("utf-8"))
                    if isinstance(targets, list):
                        targets_valid = True
                        targets_count = len(targets)
                        break
        except Exception as e:
            log.debug("CDP %s check failed on %s: %s", target_path, base_url, e)

    is_ready = tcp_reachable = version_valid and targets_valid
    return CDPEndpointStatus(
        ready=is_ready,
        tcp_reachable=True,
        version_valid=version_valid,
        targets_valid=targets_valid,
        endpoint_url=endpoint_url,
        browser_version=browser_ver,
        protocol_version=proto_ver,
        websocket_debugger_url=ws_url,
        targets_count=targets_count,
        error=None if is_ready else f"CDP targets discovery failed at {base_url}",
    )


def verify_cdp_endpoint(endpoint_url: str, timeout: float = 3.0) -> bool:
    """Convenience boolean check for exact CDP endpoint readiness."""
    status = check_cdp_endpoint_detailed(endpoint_url, timeout=timeout)
    return status.ready
