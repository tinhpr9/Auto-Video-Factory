"""
Concrete implementations of FlowProvider: MockFlowProvider and ProductionFlowProvider.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Optional

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
      - Sync bridge: _run() wraps all async FlowClient calls.
      - Zero CAPTCHA bypass. USER_INTERACTION_REQUIRED on any auth/UI failure.
      - Zero EXPERIMENTAL_RPC fallback (EXPERIMENTAL_RPC_PROVIDER = False).
      - Fail-closed: any exception that is not a generation transient maps to
        USER_INTERACTION_REQUIRED or UNKNOWN — never silently succeeds.

    Required environment (set before use):
      ~/.flow-py/config.json  — written by `flow login` from flow-py CLI
      ~/.flow-py/browser-profile/  — Playwright persistent context with live session

    Optional constructor args:
      project_id   — Flow project UUID (falls back to flow-py's saved active project)
      cdp_url      — Connect to existing Chrome CDP endpoint instead of launching browser
      headless     — Launch browser headless (default True)
    """

    UPSTREAM_PACKAGE  = "flow-py"
    UPSTREAM_REPO     = "eddie-fqh/flow-py"
    UPSTREAM_COMMIT   = "bd01679304d6fccc4c96fea3b33151c2e9e836f4"
    UPSTREAM_PYPI_VER = "0.1.0"

    def __init__(
        self,
        project_id: Optional[str] = None,
        cdp_url: Optional[str] = None,
        headless: bool = True,
        # Legacy compat — ignored; kept so existing instantiation code doesn't break
        auth_token: Optional[str] = None,
        profile_path: Optional[Path] = None,
        cdp_endpoint: Optional[str] = None,
    ):
        self._project_id = project_id
        self._cdp_url    = cdp_url or cdp_endpoint
        self._headless   = headless
        # _client is created lazily on first real call so health() can be called
        # before the browser is launched.
        self._client_cache: Optional[object] = None

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

    # ── Async bridge ─────────────────────────────────────────────────────────

    @staticmethod
    def _run(coro):
        """Run an async coroutine synchronously."""
        import asyncio  # noqa: PLC0415
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            import concurrent.futures  # noqa: PLC0415
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        return asyncio.run(coro)

    # ── FlowClient factory ───────────────────────────────────────────────────

    def _get_client(self):
        """Return cached FlowClient or create a new one."""
        if self._client_cache is None:
            FlowClient, _, _ = self._import_flow()
            self._client_cache = self._run(
                FlowClient.create(
                    project_id=self._project_id,
                    headless=self._headless,
                    cdp_url=self._cdp_url,
                )
            )
        return self._client_cache

    # ── Exception classification ─────────────────────────────────────────────

    @staticmethod
    def _classify_exception(exc: Exception, flow_exc) -> FlowFailureClass:
        """Map flow-py exceptions to our FlowFailureClass taxonomy."""
        if isinstance(exc, (flow_exc.AuthError, flow_exc.NotLoggedInError)):
            return FlowFailureClass.AUTH
        if isinstance(exc, flow_exc.GenerationTimeout):
            return FlowFailureClass.TIMEOUT
        if isinstance(exc, flow_exc.PolicyError):
            return FlowFailureClass.USER_INTERACTION_REQUIRED
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
        Check health by attempting to import flow-py and reach an active project.
        Never launches browser here — only validates that the package and config exist.
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

        # Check saved profile exists (browser session)
        try:
            from flow._storage import PROFILE_DIR, get_active_project  # noqa: PLC0415
            profile_exists = PROFILE_DIR.exists()
            project_id, _ = get_active_project()
            has_project = bool(project_id or self._project_id)
        except Exception:
            profile_exists = False
            has_project = False

        is_healthy = profile_exists and has_project
        details: dict = {
            "provider_type": "production",
            "upstream": self.UPSTREAM_REPO,
            "upstream_commit": self.UPSTREAM_COMMIT,
            "captcha_bypass_disabled": True,
            "experimental_rpc_disabled": not EXPERIMENTAL_RPC_PROVIDER,
        }
        if not is_healthy:
            reasons = []
            if not profile_exists:
                reasons.append("unauthenticated: missing_auth (browser_profile_missing — run 'flow login')")
            if not has_project:
                reasons.append("no_active_project — run 'flow projects use <id>'")
            details["reason"] = "; ".join(reasons)

        return FlowHealthStatus(
            healthy=is_healthy,
            authenticated=profile_exists,
            profile_exists=profile_exists,
            browser_ready=bool(self._cdp_url) or profile_exists,
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
        """Return the three standard T2V models matching our FlowModel enum."""
        return [
            FlowModelInfo(
                model_id=FlowModel.VEO_3_1_FAST.value,
                name="Veo 3.1 Fast (portrait, audio, 20cr)",
                capabilities=["text_to_video", "image_to_video", "audio"],
                cost_credits=20,
                default_aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
            ),
            FlowModelInfo(
                model_id=FlowModel.VEO_3_1_QUALITY.value,
                name="Veo 3.1 Quality (portrait, audio, 100cr)",
                capabilities=["text_to_video", "image_to_video", "audio", "upscale"],
                cost_credits=100,
                default_aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
            ),
            FlowModelInfo(
                model_id=FlowModel.VEO_2_1_FAST.value,
                name="Veo 2.1 Fast (portrait, no-audio, 10cr)",
                capabilities=["text_to_video", "image_to_video"],
                cost_credits=10,
                default_aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
            ),
        ]

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

        _, flow_exc, _ = self._import_flow()

        # Map aspect ratio
        aspect_map = {
            FlowAspectRatio.PORTRAIT_9_16:  "portrait",
            FlowAspectRatio.LANDSCAPE_16_9: "landscape",
            FlowAspectRatio.SQUARE_1_1:     "square",
        }
        aspect_str = aspect_map.get(request.aspect_ratio, "portrait")

        # Map start image
        start_image: Optional[str] = None
        if request.start_image_path and Path(request.start_image_path).exists():
            start_image = str(request.start_image_path)

        try:
            client = self._get_client()
            credits_before_obj = self._run(client._api.get_credits())
            credit_before = credits_before_obj.credits

            jobs = self._run(
                client.generate_video(
                    prompt=request.prompt,
                    model=request.model.value,   # e.g. "veo_3_1_t2v_fast_portrait"
                    aspect=aspect_str,
                    count=1,
                    start_image=start_image,
                )
            )

            if not jobs:
                return FlowJobResult(
                    job_id=request.job_id,
                    provider_job_id="",
                    status=FlowJobStatus.FAILED,
                    failure_class=FlowFailureClass.UNKNOWN,
                    failure_message="generate_video returned no jobs from upstream",
                )

            job = jobs[0]
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
                failure_class=FlowFailureClass.USER_INTERACTION_REQUIRED,
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

            # flow-py poll: batchCheckAsyncVideoGenerationStatus
            dummy_job = VideoJob.__new__(VideoJob)
            dummy_job._raw = {}
            dummy_job.media_name = provider_job_id
            dummy_job.project_id = self._project_id or ""
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
        Returns empty list on any failure (controller treats this as DOWNLOAD_FAILED).
        """
        _, flow_exc, VideoJob = self._import_flow()
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            client = self._get_client()

            # Resolve fife_url via a single-poll status check
            dummy_job = VideoJob.__new__(VideoJob)
            dummy_job._raw = {}
            dummy_job.media_name = provider_job_id
            dummy_job.project_id = self._project_id or ""
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

        except Exception as exc:
            log.exception("download failed for provider_job_id=%s: %s", provider_job_id, exc)
            return []

    def reconcile_by_client_id(self, client_request_id: str) -> Optional[FlowJobResult]:
        """Not supported by flow-py upstream; returns None."""
        return None

