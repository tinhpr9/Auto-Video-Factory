"""
Concrete implementations of FlowProvider: MockFlowProvider and ProductionFlowProvider.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from .android import AndroidCDPManager, ForegroundPolicy
from .contract import FlowProvider
from .models import (
    FlowAspectRatio,
    FlowCredits,
    FlowFailureClass,
    FlowGenerationRequest,
    FlowHealthStatus,
    FlowJobResult,
    FlowJobStatus,
    FlowModel,
    FlowModelInfo,
)


log = logging.getLogger(__name__)

# EXPERIMENTAL_RPC_PROVIDER must remain False.
# hurara210/google-flow-cli, Hafointosher/veo3tool, and amzbase-com/veo-automation-extension
# are explicitly excluded from the production call path.
EXPERIMENTAL_RPC_PROVIDER = False


class MockFlowProvider(FlowProvider):
    """
    Production-grade Mock provider for local development, deterministic unit testing,
    and credit-free POC execution.
    """

    MODEL_COSTS = {
        FlowModel.VEO_3_1_FAST: 20,
        FlowModel.VEO_3_1_QUALITY: 100,
        FlowModel.VEO_2_1_FAST: 10,
    }

    _SHARED_JOBS: dict[str, dict] = {}

    def __init__(
        self,
        initial_credits: int = 500,
        simulate_failure: Optional[FlowFailureClass] = None,
        simulate_latency_s: float = 0.0,
    ):
        self.credits = initial_credits
        self.consumed_credits = 0
        self.simulate_failure = simulate_failure
        self.simulate_latency_s = simulate_latency_s
        self.generate_video_calls = 0

    def health(self) -> FlowHealthStatus:
        return FlowHealthStatus(
            healthy=True,
            authenticated=True,
            profile_exists=True,
            browser_ready=True,
            details={"provider_type": "mock", "mode": "isolated_test"},
        )

    def get_credits(self) -> FlowCredits:
        return FlowCredits(
            total_credits=self.credits + self.consumed_credits,
            available_credits=self.credits,
            consumed_credits=self.consumed_credits,
            timestamp=time.time(),
        )

    def get_models(self) -> list[FlowModelInfo]:
        return [
            FlowModelInfo(
                model_id=FlowModel.VEO_3_1_FAST.value,
                name="Veo 3.1 - Fast (1080p, audio)",
                capabilities=["text_to_video", "image_to_video", "audio"],
                cost_credits=20,
                default_aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
            ),
            FlowModelInfo(
                model_id=FlowModel.VEO_3_1_QUALITY.value,
                name="Veo 3.1 - Quality (Cinematic, audio)",
                capabilities=["text_to_video", "image_to_video", "audio", "upscale"],
                cost_credits=100,
                default_aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
            ),
            FlowModelInfo(
                model_id=FlowModel.VEO_2_1_FAST.value,
                name="Veo 2.1 - Fast (Video only)",
                capabilities=["text_to_video", "image_to_video"],
                cost_credits=10,
                default_aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
            ),
        ]

    def generate_video(self, request: FlowGenerationRequest) -> FlowJobResult:
        if request.start_image_path is not None:
            p = Path(request.start_image_path)
            if not p.exists() or not p.is_file() or p.stat().st_size == 0:
                return FlowJobResult(
                    job_id=request.job_id,
                    provider_job_id="",
                    status=FlowJobStatus.FAILED,
                    failure_class=FlowFailureClass.UNKNOWN,
                    failure_message=f"Invalid start_image_path: file does not exist or is empty: {request.start_image_path}",
                )

        self.generate_video_calls += 1
        if self.simulate_latency_s > 0:
            time.sleep(self.simulate_latency_s)

        # Injected failure simulation
        if self.simulate_failure:
            if self.simulate_failure == FlowFailureClass.USER_INTERACTION_REQUIRED:
                return FlowJobResult(
                    job_id=request.job_id,
                    provider_job_id="",
                    status=FlowJobStatus.USER_INTERACTION_REQUIRED,
                    failure_class=FlowFailureClass.USER_INTERACTION_REQUIRED,
                    failure_message="Simulated CAPTCHA challenge requires manual interaction.",
                )
            return FlowJobResult(
                job_id=request.job_id,
                provider_job_id="",
                status=FlowJobStatus.FAILED,
                failure_class=self.simulate_failure,
                failure_message=f"Simulated failure: {self.simulate_failure.value}",
            )

        unit_cost = self.MODEL_COSTS.get(request.model, 20)
        total_cost = unit_cost * max(1, request.count)

        if self.credits < total_cost:
            return FlowJobResult(
                job_id=request.job_id,
                provider_job_id="",
                status=FlowJobStatus.FAILED,
                failure_class=FlowFailureClass.QUOTA,
                failure_message=f"Insufficient credits: requires {total_cost}, available {self.credits}",
            )

        credits_before = self.credits
        self.credits -= total_cost
        self.consumed_credits += total_cost
        credits_after = self.credits

        provider_job_id = f"mock_prov_{uuid.uuid4().hex[:8]}"

        self._SHARED_JOBS[provider_job_id] = {
            "job_id": request.job_id,
            "client_request_id": request.client_request_id or request.job_id,
            "prompt": request.prompt,
            "model": request.model.value,
            "aspect_ratio": request.aspect_ratio.value,
            "count": request.count,
            "status": FlowJobStatus.COMPLETED,
            "created_at": time.time(),
        }

        return FlowJobResult(
            job_id=request.job_id,
            provider_job_id=provider_job_id,
            status=FlowJobStatus.SUBMITTED,
            credit_before=credits_before,
            credit_after=credits_after,
            runtime_evidence={"cost_deducted": total_cost, "count": request.count},
        )

    def poll(self, provider_job_id: str) -> FlowJobResult:
        if self.simulate_latency_s > 0:
            time.sleep(self.simulate_latency_s)

        job = self._SHARED_JOBS.get(provider_job_id)
        if not job:
            return FlowJobResult(
                job_id="",
                provider_job_id=provider_job_id,
                status=FlowJobStatus.FAILED,
                failure_class=FlowFailureClass.UNKNOWN,
                failure_message=f"Unknown provider job ID: {provider_job_id}",
            )

        return FlowJobResult(
            job_id=job["job_id"],
            provider_job_id=provider_job_id,
            status=job["status"],
            runtime_evidence={"model": job["model"], "aspect_ratio": job["aspect_ratio"]},
        )

    def download(self, provider_job_id: str, output_dir: Path) -> list[Path]:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        job = self._SHARED_JOBS.get(provider_job_id)

        count = job.get("count", 1) if job else 1
        paths = []
        ffmpeg_bin = shutil.which("ffmpeg")
        for i in range(count):
            suffix = f"_{i}" if count > 1 else ""
            out_file = output_dir / f"{provider_job_id}{suffix}.mp4"
            if out_file.exists() and out_file.stat().st_size > 0:
                paths.append(out_file)
                continue
            tmp_file = out_file.with_name(f".tmp_{uuid.uuid4().hex[:8]}_{out_file.name}")
            if ffmpeg_bin:
                subprocess.run(
                    [
                        ffmpeg_bin, "-y", "-f", "lavfi", "-i", "color=c=black:s=720x1280:d=0.1:r=10",
                        "-preset", "ultrafast", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "0.1", str(tmp_file)
                    ],
                    capture_output=True,
                    check=False,
                    timeout=30,
                )
            if not tmp_file.exists() or tmp_file.stat().st_size == 0:
                tmp_file.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00isommp42")
            os.replace(tmp_file, out_file)
            paths.append(out_file)
        return paths

    def reconcile_by_client_id(self, client_request_id: str) -> Optional[FlowJobResult]:
        for p_id, job in self._SHARED_JOBS.items():
            if job.get("client_request_id") == client_request_id or job.get("job_id") == client_request_id:
                return FlowJobResult(
                    job_id=job["job_id"],
                    provider_job_id=p_id,
                    status=job["status"],
                )
        return None


class ProductionFlowProvider(FlowProvider):
    """
    Production provider: wraps FlowClient from eddie-fqh/flow-py
    (PRODUCTION_CORE upstream, pinned to bd01679304d6fccc4c96fea3b33151c2e9e836f4).

    Architecture invariants:
      - UPSTREAM: eddie-fqh/flow-py ONLY. Never google-flow-cli / veo3tool /
        veo-automation-extension.
      - Lazy-imports flow-py so the package is not required for mock/test paths.
      - Sync bridge: _run() executes all async FlowClient calls on a persistent,
        dedicated background event loop thread.
      - Zero CAPTCHA bypass. USER_INTERACTION_REQUIRED on any auth/UI failure.
      - Zero EXPERIMENTAL_RPC fallback (EXPERIMENTAL_RPC_PROVIDER = False).
      - Fail-closed: any exception that is not a generation transient maps to
        USER_INTERACTION_REQUIRED or UNKNOWN — never silently succeeds.

    Required environment (set before use):
      ~/.flow-py/config.json  — written by `flow login` from flow-py CLI
      ~/.flow-py/browser-profile/  — Playwright persistent context with live session (if not using CDP)

    Optional constructor args:
      project_id   — Flow project UUID (falls back to flow-py's saved active project)
      cdp_url      — Connect to existing Chrome CDP endpoint instead of launching browser
      headless     — Launch browser headless (default True)
    """

    UPSTREAM_PACKAGE  = "flow-py"
    UPSTREAM_REPO     = "eddie-fqh/flow-py"
    UPSTREAM_COMMIT   = "bd01679304d6fccc4c96fea3b33151c2e9e836f4"
    UPSTREAM_PYPI_VER = "0.1.0"

    _global_loop: Optional[asyncio.AbstractEventLoop] = None
    _global_thread: Optional[threading.Thread] = None
    _global_lock = threading.RLock()

    def __init__(
        self,
        project_id: Optional[str] = None,
        cdp_url: Optional[str] = None,
        headless: bool = True,
        auth_token: Optional[str] = None,
        profile_path: Optional[Path] = None,
        cdp_endpoint: Optional[str] = None,
        android_manager: Optional[AndroidCDPManager] = None,
        foreground_policy: Optional[ForegroundPolicy | str] = None,
    ):
        if auth_token is not None:
            raise ValueError(
                "ProductionFlowProvider does not accept 'auth_token'. "
                "flow-py uses Playwright session storage ('flow login') or CDP. "
                "Remove auth_token or configure FLOW_CDP_URL."
            )
        if profile_path is not None:
            raise ValueError(
                "ProductionFlowProvider does not accept 'profile_path'. "
                "flow-py uses default profile storage in ~/.flow-py/ or CDP endpoint. "
                "Remove profile_path or configure FLOW_CDP_URL."
            )
        self._project_id = project_id
        self._resolved_project_id: Optional[str] = project_id
        self._cdp_url    = cdp_url or cdp_endpoint
        self._headless   = headless
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._client: Optional[object] = None
        self._lock = threading.RLock()

        # Foreground policy & Android CDP management
        if foreground_policy is not None:
            if isinstance(foreground_policy, str):
                self._foreground_policy = ForegroundPolicy(foreground_policy)
            else:
                self._foreground_policy = foreground_policy
        else:
            env_pol = os.getenv("FLOW_FOREGROUND_POLICY", "auto").lower()
            self._foreground_policy = (
                ForegroundPolicy(env_pol)
                if env_pol in [p.value for p in ForegroundPolicy]
                else ForegroundPolicy.AUTO
            )

        if android_manager is not None:
            self._android_manager: Optional[AndroidCDPManager] = android_manager
        elif os.getenv("FLOW_ANDROID_CDP", "").lower() in ("1", "true", "yes"):
            self._android_manager = AndroidCDPManager(foreground_policy=self._foreground_policy)
        elif (
            os.getenv("FLOW_ANDROID_CDP") is None
            and (os.path.exists("/data/data/com.termux") or "ANDROID_ROOT" in os.environ)
            and self._cdp_url
            and any(h in self._cdp_url for h in ("127.0.0.1", "localhost", "9222"))
        ):
            self._android_manager = AndroidCDPManager(foreground_policy=self._foreground_policy)
        else:
            self._android_manager = None



    @property
    def _client_cache(self) -> Optional[object]:
        """Backward-compatible property alias for _client."""
        return self._client

    @_client_cache.setter
    def _client_cache(self, value: Optional[object]) -> None:
        self._client = value

    # ── Import guard ──────────────────────────────────────────────────────────

    @staticmethod
    def _import_flow() -> tuple:
        """
        Lazy-import flow-py. Raises ImportError with install instructions if absent.
        Returns (FlowClient, exceptions_module, VideoJob_class) tuple.
        Only eddie-fqh/flow-py is the valid upstream — see UPSTREAM_REPO.
        """
        try:
            from flow import FlowClient  # noqa: PLC0415
            import flow._exceptions as flow_exc  # noqa: PLC0415
            from flow._api import VideoJob  # noqa: PLC0415
            return FlowClient, flow_exc, VideoJob
        except ImportError as exc:
            raise ImportError(
                "Production Flow provider requires the 'flow-py' package "
                f"(upstream: {ProductionFlowProvider.UPSTREAM_REPO} @ "
                f"{ProductionFlowProvider.UPSTREAM_COMMIT}). "
                "Install with: pip install 'auto-video-factory[prod]'"
            ) from exc

    # ── Async bridge & event loop lifecycle ───────────────────────────────────

    @classmethod
    def _get_global_loop(cls) -> asyncio.AbstractEventLoop:
        with cls._global_lock:
            if cls._global_loop is None or cls._global_loop.is_closed():
                loop = asyncio.new_event_loop()
                def _worker():
                    asyncio.set_event_loop(loop)
                    loop.run_forever()
                thread = threading.Thread(
                    target=_worker,
                    daemon=True,
                    name="ProductionFlowGlobalRunner",
                )
                thread.start()
                cls._global_loop = loop
                cls._global_thread = thread
            return cls._global_loop

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """Ensure persistent event loop running in a dedicated background thread for this provider."""
        with self._lock:
            if self._loop is None or self._loop.is_closed():
                loop = asyncio.new_event_loop()
                def _worker():
                    asyncio.set_event_loop(loop)
                    loop.run_forever()
                thread = threading.Thread(
                    target=_worker,
                    daemon=True,
                    name="ProductionFlowRunner",
                )
                thread.start()
                self._loop = loop
                self._thread = thread
            return self._loop

    def _run(self_or_cls, coro):
        """
        Run an async coroutine synchronously on the persistent dedicated event loop.
        Works both as an instance method (provider._run) and class/static method (ProductionFlowProvider._run).
        """
        if isinstance(self_or_cls, ProductionFlowProvider):
            loop = self_or_cls._ensure_loop()
        else:
            loop = ProductionFlowProvider._get_global_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result()

    def close(self) -> None:
        """Orderly shutdown: close underlying client and stop the persistent event loop thread."""
        with self._lock:
            if self._client is not None:
                if hasattr(self._client, "close"):
                    try:
                        if self._loop is not None and not self._loop.is_closed():
                            fut = asyncio.run_coroutine_threadsafe(self._client.close(), self._loop)
                            fut.result(timeout=5.0)
                    except Exception as exc:
                        log.debug("Error closing client during provider shutdown: %s", exc)
                self._client = None
            if self._loop is not None and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._loop.stop)
                if self._thread is not None and self._thread.is_alive():
                    self._thread.join(timeout=2.0)
                self._loop = None
                self._thread = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    # ── FlowClient factory ───────────────────────────────────────────────────

    def _get_client(self):
        """Return cached FlowClient or create a new one on the persistent event loop."""
        with self._lock:
            if self._client is None:
                FlowClient, _, _ = self._import_flow()
                if self._android_manager:
                    try:
                        self._android_manager.ensure_cdp_forward()
                    except Exception as e:
                        log.debug("Android CDP forward check before client creation: %s", e)
                resolved_project_id = self._project_id or self._resolved_project_id
                if not resolved_project_id:
                    try:
                        from flow._storage import get_active_project  # noqa: PLC0415
                        saved_proj, _ = get_active_project()
                        if saved_proj:
                            resolved_project_id = saved_proj
                            self._resolved_project_id = saved_proj
                    except Exception:
                        pass
                self._client = self._run(
                    FlowClient.create(
                        project_id=resolved_project_id,
                        headless=self._headless,
                        cdp_url=self._cdp_url,
                    )
                )
            return self._client


    # ── Exception classification ─────────────────────────────────────────────

    @staticmethod
    def _classify_exception(exc: Exception, flow_exc) -> FlowFailureClass:
        """Map flow-py exceptions to our FlowFailureClass taxonomy."""
        if isinstance(exc, (flow_exc.AuthError, flow_exc.NotLoggedInError)):
            return FlowFailureClass.AUTH
        if isinstance(exc, flow_exc.GenerationTimeout):
            return FlowFailureClass.TIMEOUT
        if isinstance(exc, flow_exc.PolicyError):
            return FlowFailureClass.POLICY_VIOLATION
        if isinstance(exc, flow_exc.UIError):
            return FlowFailureClass.UI_CHANGED
        if isinstance(exc, flow_exc.NotFoundError):
            return FlowFailureClass.MODEL_UNAVAILABLE
        if isinstance(exc, flow_exc.GenerationError):
            return FlowFailureClass.UNKNOWN
        # Network/connection errors
        msg = str(exc).lower()
        if any(w in msg for w in ("timeout", "timed out")):
            return FlowFailureClass.TIMEOUT
        if any(w in msg for w in ("rate", "429", "quota")):
            return FlowFailureClass.RATE_LIMIT
        return FlowFailureClass.UNKNOWN

    # ── FlowProvider implementation ──────────────────────────────────────────

    def health(self) -> FlowHealthStatus:
        """
        Check health by attempting to import flow-py and validate project and browser readiness.
        Never launches browser here — only validates that the package and config exist.
        In CDP mode, local browser profile is NOT required.
        """
        try:
            FlowClient, flow_exc, _ = self._import_flow()
        except ImportError as exc:
            return FlowHealthStatus(
                healthy=False,
                authenticated=False,
                profile_exists=False,
                browser_ready=False,
                details={
                    "provider_type": "production",
                    "upstream": self.UPSTREAM_REPO,
                    "upstream_commit": self.UPSTREAM_COMMIT,
                    "captcha_bypass_disabled": True,
                    "experimental_rpc_disabled": not EXPERIMENTAL_RPC_PROVIDER,
                    "reason": f"unauthenticated (missing_auth): flow-py not installed: {exc}",
                },
            )

        # Check saved profile exists (browser session) and active project
        try:
            from flow._storage import PROFILE_DIR, get_active_project  # noqa: PLC0415
            profile_exists = PROFILE_DIR.exists()
            project_id, _ = get_active_project()
            has_project = bool(project_id or self._project_id)
        except Exception:
            profile_exists = False
            has_project = bool(self._project_id)

        # CDP configuration validation
        has_cdp = bool(self._cdp_url and self._cdp_url.strip())
        cdp_valid = False
        cdp_error = None
        if has_cdp:
            cdp_str = self._cdp_url.strip()
            if not any(cdp_str.startswith(prefix) for prefix in ("http://", "https://", "ws://", "wss://", "localhost:", "127.0.0.1:")):
                cdp_error = f"invalid_cdp_url: '{cdp_str}' must start with http://, https://, ws://, or wss://"
            elif self._android_manager:
                try:
                    forward_ok = self._android_manager.ensure_cdp_forward()
                    if not forward_ok:
                        cdp_error = "android_cdp_forward_failed (no connected Android device or port forward failed)"
                    else:
                        cdp_valid = True
                except Exception as e:
                    log.debug("Android CDP manager forward check: %s", e)
                    cdp_error = f"android_cdp_forward_error: {e}"
            else:
                cdp_valid = True


        if has_cdp:
            browser_ready = cdp_valid
            authenticated = cdp_valid  # In CDP mode, auth is managed by external Chrome session
            is_healthy = cdp_valid and has_project
        else:
            browser_ready = profile_exists
            authenticated = profile_exists
            is_healthy = profile_exists and has_project

        details: dict = {
            "provider_type": "production",
            "upstream": self.UPSTREAM_REPO,
            "upstream_commit": self.UPSTREAM_COMMIT,
            "captcha_bypass_disabled": True,
            "experimental_rpc_disabled": not EXPERIMENTAL_RPC_PROVIDER,
        }
        if self._android_manager:
            details["android_cdp"] = True
            details["foreground_policy"] = self._foreground_policy.value
        if not is_healthy:
            reasons = []
            if has_cdp and not cdp_valid:
                reasons.append(cdp_error or "invalid_cdp_url")
            elif not has_cdp and not profile_exists:
                reasons.append("unauthenticated: missing_auth (browser_profile_missing — run 'flow login' or configure cdp_url)")
            if not has_project:
                reasons.append("no_active_project — run 'flow projects use <id>' or pass project_id")
            details["reason"] = "; ".join(reasons)

        return FlowHealthStatus(
            healthy=is_healthy,
            authenticated=authenticated,
            profile_exists=profile_exists,
            browser_ready=browser_ready,
            details=details,
        )

    def get_credits(self) -> FlowCredits:
        _, flow_exc, _ = self._import_flow()
        try:
            client = self._get_client()
            credits_obj = self._run(client._api.get_credits())
            available = credits_obj.credits
            return FlowCredits(
                total_credits=available,   # flow-py only exposes remaining credits
                available_credits=available,
                consumed_credits=0,        # not exposed by upstream API
                timestamp=time.time(),
            )
        except Exception as exc:
            failure_cls = self._classify_exception(exc, flow_exc)
            log.error("get_credits failed (%s): %s", failure_cls.value, exc)
            raise

    def get_models(self) -> list[FlowModelInfo]:
        """
        Dynamically fetch video model configuration from flow-py client.

        Maps upstream videoModels into Auto-Video's public FlowModelInfo contract.
        """
        _, flow_exc, _ = self._import_flow()
        try:
            client = self._get_client()
            if hasattr(client, "get_model_config"):
                config = self._run(client.get_model_config())
            elif hasattr(client, "_api") and hasattr(client._api, "get_video_model_config"):
                config = self._run(client._api.get_video_model_config())
            else:
                config = {}

            video_models = []
            if isinstance(config, dict):
                video_models = config.get("videoModels") or config.get("result", {}).get("videoModels") or []

            models_out: list[FlowModelInfo] = []
            for item in video_models:
                if not isinstance(item, dict):
                    continue
                # Skip deprecated models
                if item.get("modelStatus") == "MODEL_STATUS_DEPRECATED":
                    continue
                key = item.get("key") or item.get("id") or item.get("model_id") or ""
                if not key:
                    continue
                display_name = item.get("displayName") or item.get("display_name") or key
                cost = int(item.get("creditCost") or item.get("credit_cost") or item.get("cost") or 20)
                raw_caps = item.get("capabilities") or ["text_to_video"]
                caps = [c.lower().replace("start_image", "image_to_video").replace("text", "text_to_video") for c in raw_caps]
                seen = set()
                dedup_caps = []
                for c in caps:
                    if c not in seen:
                        seen.add(c)
                        dedup_caps.append(c)

                aspects = item.get("supportedAspectRatios") or []
                if "PORTRAIT" in aspects or "9:16" in aspects:
                    def_aspect = FlowAspectRatio.PORTRAIT_9_16
                elif "LANDSCAPE" in aspects or "16:9" in aspects:
                    def_aspect = FlowAspectRatio.LANDSCAPE_16_9
                elif "SQUARE" in aspects or "1:1" in aspects:
                    def_aspect = FlowAspectRatio.SQUARE_1_1
                else:
                    def_aspect = FlowAspectRatio.PORTRAIT_9_16

                models_out.append(
                    FlowModelInfo(
                        model_id=key,
                        name=display_name,
                        capabilities=dedup_caps,
                        cost_credits=cost,
                        default_aspect_ratio=def_aspect,
                    )
                )

            return models_out
        except Exception as exc:
            failure_cls = self._classify_exception(exc, flow_exc)
            log.error("get_models failed (%s): %s", failure_cls.value, exc)
            raise

    def generate_video(self, request: FlowGenerationRequest) -> FlowJobResult:
        """
        Submit a T2V or I2V generation via FlowClient.generate_video().

        Maps our FlowModel → flow-py model key string.
        Maps our FlowAspectRatio → flow-py aspect string.
        Returns SUBMITTED with provider_job_id = media_name (used for poll/download).

        Fail-closed on any auth/UI/captcha error → USER_INTERACTION_REQUIRED.
        """
        # Enforce count==1 (architectural constraint, same as visual_provider guard)
        if request.count != 1:
            return FlowJobResult(
                job_id=request.job_id,
                provider_job_id="",
                status=FlowJobStatus.FAILED,
                failure_class=FlowFailureClass.UNKNOWN,
                failure_message=f"ProductionFlowProvider only supports count=1, got {request.count}",
            )

        # Validate start image if specified (fail closed if missing/empty to avoid degrading I2V to T2V)
        start_image: Optional[str] = None
        if request.start_image_path is not None:
            p = Path(request.start_image_path)
            if not p.exists() or not p.is_file() or p.stat().st_size == 0:
                log.error("Invalid start_image_path: %s", request.start_image_path)
                return FlowJobResult(
                    job_id=request.job_id,
                    provider_job_id="",
                    status=FlowJobStatus.FAILED,
                    failure_class=FlowFailureClass.UNKNOWN,
                    failure_message=f"Invalid start_image_path: file does not exist or is empty: {request.start_image_path}",
                )
            start_image = str(p)

        _, flow_exc, _ = self._import_flow()

        # Map aspect ratio
        aspect_map = {
            FlowAspectRatio.PORTRAIT_9_16:  "portrait",
            FlowAspectRatio.LANDSCAPE_16_9: "landscape",
            FlowAspectRatio.SQUARE_1_1:     "square",
        }
        aspect_str = aspect_map.get(request.aspect_ratio, "portrait")

        try:
            client = self._get_client()
            credits_before_obj = self._run(client._api.get_credits())
            credit_before = credits_before_obj.credits

            def _submit_call():
                return self._run(
                    client.generate_video(
                        prompt=request.prompt,
                        model=request.model.value,   # e.g. "veo_3_1_t2v_fast_portrait"
                        aspect=aspect_str,
                        count=1,
                        start_image=start_image,
                    )
                )

            if self._android_manager:
                project_url = (
                    f"https://labs.google/fx/tools/flow/project/{self._resolved_project_id}"
                    if self._resolved_project_id
                    else None
                )
                with self._android_manager.scoped_foreground_for_submit(flow_url=project_url):
                    jobs = _submit_call()
            else:
                jobs = _submit_call()


            if not jobs:
                return FlowJobResult(
                    job_id=request.job_id,
                    provider_job_id="",
                    status=FlowJobStatus.FAILED,
                    failure_class=FlowFailureClass.UNKNOWN,
                    failure_message="generate_video returned no jobs from upstream",
                )

            job = jobs[0]
            if getattr(job, "project_id", None):
                self._resolved_project_id = job.project_id
            # media_name is the stable upstream job identifier used for poll+download
            provider_job_id = job.media_name or job.workflow_id

            return FlowJobResult(
                job_id=request.job_id,
                provider_job_id=provider_job_id,
                status=FlowJobStatus.SUBMITTED,
                credit_before=credit_before,
                runtime_evidence={
                    "upstream": self.UPSTREAM_REPO,
                    "upstream_commit": self.UPSTREAM_COMMIT,
                    "media_name": job.media_name,
                    "workflow_id": job.workflow_id,
                    "project_id": job.project_id,
                },
            )

        except (flow_exc.AuthError, flow_exc.NotLoggedInError, flow_exc.UIError) as exc:
            log.error("USER_INTERACTION_REQUIRED during generate_video: %s", exc)
            return FlowJobResult(
                job_id=request.job_id,
                provider_job_id="",
                status=FlowJobStatus.USER_INTERACTION_REQUIRED,
                failure_class=FlowFailureClass.USER_INTERACTION_REQUIRED,
                failure_message=f"Google Flow requires user interaction: {exc}",
            )
        except flow_exc.PolicyError as exc:
            log.error("Policy rejection during generate_video: %s", exc)
            return FlowJobResult(
                job_id=request.job_id,
                provider_job_id="",
                status=FlowJobStatus.FAILED,
                failure_class=FlowFailureClass.POLICY_VIOLATION,
                failure_message=f"Prompt rejected by Google content policy: {exc}",
            )
        except flow_exc.GenerationTimeout as exc:
            log.error("GenerationTimeout during generate_video: %s", exc)
            return FlowJobResult(
                job_id=request.job_id,
                provider_job_id="",
                status=FlowJobStatus.FAILED,
                failure_class=FlowFailureClass.TIMEOUT,
                failure_message=str(exc),
            )
        except Exception as exc:
            _, flow_exc2, _ = self._import_flow()
            failure_cls = self._classify_exception(exc, flow_exc2)
            log.exception("Unexpected error in generate_video for job %s: %s", request.job_id, exc)
            return FlowJobResult(
                job_id=request.job_id,
                provider_job_id="",
                status=FlowJobStatus.FAILED,
                failure_class=failure_cls,
                failure_message=str(exc),
            )

    def poll(self, provider_job_id: str) -> FlowJobResult:
        """
        Poll the upstream status of a previously submitted job.

        provider_job_id = media_name from generate_video().
        Maps COMPLETE → COMPLETED, FAILED → FAILED, else PENDING.
        Treats any auth/UI error as USER_INTERACTION_REQUIRED (fail-closed).
        """
        _, flow_exc, VideoJob = self._import_flow()
        try:
            client = self._get_client()

            # Resolve active project id if not explicitly passed
            proj = self._project_id or self._resolved_project_id
            if not proj:
                try:
                    from flow._storage import get_active_project  # noqa: PLC0415
                    saved_proj, _ = get_active_project()
                    if saved_proj:
                        proj = saved_proj
                except Exception:
                    pass

            # flow-py poll: batchCheckAsyncVideoGenerationStatus
            dummy_job = VideoJob.__new__(VideoJob)
            dummy_job._raw = {}
            dummy_job.media_name = provider_job_id
            dummy_job.project_id = proj or ""
            dummy_job.workflow_id = ""

            status = self._run(client._api.wait_for_video(
                dummy_job, timeout_s=30, poll_s=5.0
            ))

            if status.complete:
                return FlowJobResult(
                    job_id="",
                    provider_job_id=provider_job_id,
                    status=FlowJobStatus.COMPLETED,
                    runtime_evidence={"fife_url": getattr(status, "fife_url", "")},
                )
            if status.failed:
                return FlowJobResult(
                    job_id="",
                    provider_job_id=provider_job_id,
                    status=FlowJobStatus.FAILED,
                    failure_class=FlowFailureClass.UNKNOWN,
                    failure_message=f"Upstream status: {status.status}",
                )

            # Still in progress
            return FlowJobResult(
                job_id="",
                provider_job_id=provider_job_id,
                status=FlowJobStatus.PENDING,
            )

        except flow_exc.GenerationTimeout:
            # Not done yet in the 30s window — tell controller to keep polling
            return FlowJobResult(
                job_id="",
                provider_job_id=provider_job_id,
                status=FlowJobStatus.PENDING,
            )
        except (flow_exc.AuthError, flow_exc.NotLoggedInError, flow_exc.UIError) as exc:
            return FlowJobResult(
                job_id="",
                provider_job_id=provider_job_id,
                status=FlowJobStatus.USER_INTERACTION_REQUIRED,
                failure_class=FlowFailureClass.USER_INTERACTION_REQUIRED,
                failure_message=str(exc),
            )
        except Exception as exc:
            _, flow_exc2, _ = self._import_flow()
            failure_cls = self._classify_exception(exc, flow_exc2)
            log.exception("poll failed for provider_job_id=%s: %s", provider_job_id, exc)
            return FlowJobResult(
                job_id="",
                provider_job_id=provider_job_id,
                status=FlowJobStatus.FAILED,
                failure_class=failure_cls,
                failure_message=str(exc),
            )

    def download(self, provider_job_id: str, output_dir: Path) -> list[Path]:
        """
        Download a completed video by its media_name.

        Uses FlowClient.download(fife_url, output_path) from flow-py.
        Preserves AuthError, NotLoggedInError, UIError by raising them so the controller
        can activate safety pause; returns empty list on ordinary file failures.
        """
        _, flow_exc, VideoJob = self._import_flow()
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            client = self._get_client()

            proj = self._project_id or self._resolved_project_id
            if not proj:
                try:
                    from flow._storage import get_active_project  # noqa: PLC0415
                    saved_proj, _ = get_active_project()
                    if saved_proj:
                        proj = saved_proj
                except Exception:
                    pass

            # Resolve fife_url via a single-poll status check
            dummy_job = VideoJob.__new__(VideoJob)
            dummy_job._raw = {}
            dummy_job.media_name = provider_job_id
            dummy_job.project_id = proj or ""
            dummy_job.workflow_id = ""

            status = self._run(client._api.wait_for_video(
                dummy_job, timeout_s=10, poll_s=2.0
            ))

            fife_url = getattr(status, "fife_url", "") or ""
            if not fife_url:
                log.error("download: no fife_url for provider_job_id=%s", provider_job_id)
                return []

            safe_name = provider_job_id.replace("/", "_").replace(":", "_")
            out_file = output_dir / f"{safe_name}.mp4"
            tmp_file = output_dir / f".tmp_{uuid.uuid4().hex[:8]}_{safe_name}.mp4"

            downloaded = self._run(client.download(fife_url, str(tmp_file)))
            if not downloaded or not Path(downloaded).exists() or Path(downloaded).stat().st_size == 0:
                log.error("download: zero-byte or missing file for provider_job_id=%s", provider_job_id)
                return []

            os.replace(str(downloaded), out_file)
            return [out_file]

        except (flow_exc.AuthError, flow_exc.NotLoggedInError, flow_exc.UIError) as exc:
            log.error("USER_INTERACTION_REQUIRED during download for %s: %s", provider_job_id, exc)
            raise
        except Exception as exc:
            log.exception("download failed for provider_job_id=%s: %s", provider_job_id, exc)
            return []

    def reconcile_by_client_id(self, client_request_id: str) -> Optional[FlowJobResult]:
        """Not supported by flow-py upstream; returns None."""
        return None

