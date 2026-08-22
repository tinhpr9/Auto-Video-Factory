"""
Persistent supervisor daemon for Auto-Video-Factory Mobile Web Service.
Provides robust process lifecycle, double-fork daemonization, auto-restart on failure,
and clean socket management on 127.0.0.1:8000.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

DEFAULT_PORT = 8000
DEFAULT_HOST = "127.0.0.1"


def apply_phone_defaults() -> None:
    os.environ.setdefault("AVF_PROVIDER", "flow")
    os.environ.setdefault("AVF_LOCAL_PHONE", "1")
    os.environ.setdefault("AVF_REQUIRE_AUTH", "0")
    os.environ.setdefault("AVF_HOST", DEFAULT_HOST)
    os.environ.setdefault("AVF_PORT", str(DEFAULT_PORT))
    os.environ.setdefault("FLOW_CDP_PORT", "9224")
    os.environ.setdefault("FLOW_CDP_URL", f"http://{DEFAULT_HOST}:9224")
    os.environ.setdefault("AVF_BROWSER_BACKEND", "android_chrome")
    os.environ.setdefault("FLOW_PROJECT_ID", "362c6899-f74f-4118-b7d8-613ade3cd3af")
    os.environ.setdefault("AVF_FLOW_MODE", "flow_balanced")
    os.environ.setdefault("AVF_FLOW_MODEL", "omni_flash")


class AVFSupervisor:
    def __init__(self, state_dir: Path | None = None, port: int = DEFAULT_PORT, host: str = DEFAULT_HOST):
        apply_phone_defaults()
        self.state_dir = state_dir or Path("output/web")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.port = port
        self.host = host
        self.supervisor_pid_file = self.state_dir / "avf_supervisor.pid"
        self.worker_pid_file = self.state_dir / "avf_web.pid"
        self.log_file = self.state_dir / "avf_supervisor.log"

    def is_pid_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def get_supervisor_pid(self) -> int | None:
        if self.supervisor_pid_file.exists():
            try:
                pid = int(self.supervisor_pid_file.read_text().strip())
                if self.is_pid_alive(pid):
                    return pid
            except (ValueError, OSError):
                pass
        return None

    def get_worker_pid(self) -> int | None:
        if self.worker_pid_file.exists():
            try:
                pid = int(self.worker_pid_file.read_text().strip())
                if self.is_pid_alive(pid):
                    return pid
            except (ValueError, OSError):
                pass
        return None

    def check_health(self, timeout: float = 1.0) -> bool:
        try:
            url = f"http://{self.host}:{self.port}/health"
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return resp.status == 200
        except Exception:
            return False

    def start(self, foreground: bool = False) -> bool:
        if self.get_supervisor_pid() and self.check_health():
            print(f"AVF Service already running (Supervisor PID: {self.get_supervisor_pid()}).")
            return True

        if not foreground:
            # Double-fork daemonization
            pid = os.fork()
            if pid > 0:
                # Parent returns; wait for service to be healthy
                ready = False
                for _ in range(40):
                    time.sleep(0.25)
                    if self.check_health():
                        ready = True
                        break
                if ready:
                    print(f"AVF Persistent Service started on http://{self.host}:{self.port}")
                    return True
                else:
                    print(f"Service startup timed out. Check logs at {self.log_file}", file=sys.stderr)
                    return False

            os.setsid()
            pid2 = os.fork()
            if pid2 > 0:
                sys.exit(0)

            # Redirect standard file descriptors in daemon child
            sys.stdout.flush()
            sys.stderr.flush()
            si = open(os.devnull, "r")
            so = open(self.log_file, "a+", buffering=1)
            se = open(self.log_file, "a+", buffering=1)
            os.dup2(si.fileno(), sys.stdin.fileno())
            os.dup2(so.fileno(), sys.stdout.fileno())
            os.dup2(se.fileno(), sys.stderr.fileno())

        self._run_supervisor_loop()
        return True

    def _run_supervisor_loop(self):
        # Record supervisor PID
        self.supervisor_pid_file.write_text(str(os.getpid()))

        stop_requested = [False]

        def _sig_handler(signum, frame):
            stop_requested[0] = True
            wpid = self.get_worker_pid()
            if wpid:
                try:
                    os.kill(wpid, signal.SIGTERM)
                except Exception:
                    pass

        signal.signal(signal.SIGTERM, _sig_handler)
        signal.signal(signal.SIGINT, _sig_handler)

        while not stop_requested[0]:
            try:
                # Ensure CDP port forward before launching worker
                backend = os.getenv("AVF_BROWSER_BACKEND", "android_chrome")
                if backend == "android_chrome":
                    try:
                        from auto_video_factory.flow_provider.android import AndroidCDPManager
                        cdp_port = int(os.getenv("FLOW_CDP_PORT", "9224"))
                        AndroidCDPManager(cdp_port=cdp_port).ensure_cdp_forward()
                    except Exception as e:
                        print(f"CDP forward check error: {e}", file=sys.stderr)

                # Launch web worker subprocess
                proc = subprocess.Popen(
                    [sys.executable, "-m", "auto_video_factory.web"],
                    stdout=sys.stdout,
                    stderr=sys.stderr,
                    env=os.environ.copy(),
                )
                self.worker_pid_file.write_text(str(proc.pid))

                while proc.poll() is None:
                    if stop_requested[0]:
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                        break
                    time.sleep(0.5)

            except Exception as e:
                print(f"Supervisor error in loop: {e}", file=sys.stderr)

            if self.worker_pid_file.exists():
                try:
                    self.worker_pid_file.unlink()
                except Exception:
                    pass

            if stop_requested[0]:
                break

            time.sleep(1)

        if self.supervisor_pid_file.exists():
            try:
                self.supervisor_pid_file.unlink()
            except Exception:
                pass

    def stop(self) -> bool:
        spid = self.get_supervisor_pid()
        if spid:
            try:
                os.kill(spid, signal.SIGTERM)
                for _ in range(25):
                    if not self.is_pid_alive(spid):
                        break
                    time.sleep(0.2)
                if self.is_pid_alive(spid):
                    os.kill(spid, signal.SIGKILL)
            except Exception:
                pass
        if self.supervisor_pid_file.exists():
            self.supervisor_pid_file.unlink(missing_ok=True)

        wpid = self.get_worker_pid()
        if wpid:
            try:
                os.kill(wpid, signal.SIGTERM)
                time.sleep(0.2)
                if self.is_pid_alive(wpid):
                    os.kill(wpid, signal.SIGKILL)
            except Exception:
                pass
        if self.worker_pid_file.exists():
            self.worker_pid_file.unlink(missing_ok=True)

        print("AVF Service stopped.")
        return True

    def status(self) -> int:
        spid = self.get_supervisor_pid()
        wpid = self.get_worker_pid()
        healthy = self.check_health()
        if spid and healthy:
            print(f"AVF Service is RUNNING (Supervisor PID: {spid}, Worker PID: {wpid}, Health: OK)")
            print(f"URL: http://{self.host}:{self.port}")
            return 0
        elif spid:
            print(f"AVF Supervisor is RUNNING (PID: {spid}), but web health check not responding yet.")
            return 1
        else:
            print("AVF Service is STOPPED.")
            return 2


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "start"
    port = int(os.getenv("AVF_PORT", str(DEFAULT_PORT)))
    host = os.getenv("AVF_HOST", DEFAULT_HOST)
    state_dir = Path(os.getenv("AVF_STATE_DIR", "output/web"))

    supervisor = AVFSupervisor(state_dir=state_dir, port=port, host=host)

    if cmd == "start":
        success = supervisor.start(foreground=False)
        sys.exit(0 if success else 1)
    elif cmd == "foreground":
        supervisor.start(foreground=True)
    elif cmd == "stop":
        supervisor.stop()
    elif cmd == "restart":
        supervisor.stop()
        time.sleep(1)
        success = supervisor.start(foreground=False)
        sys.exit(0 if success else 1)
    elif cmd == "status":
        sys.exit(supervisor.status())
    else:
        print(f"Usage: {sys.argv[0]} [start|stop|restart|status|foreground]")
        sys.exit(1)


if __name__ == "__main__":
    main()
