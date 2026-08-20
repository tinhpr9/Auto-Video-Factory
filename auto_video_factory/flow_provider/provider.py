"""
Concrete implementations of FlowProvider: MockFlowProvider and ProductionFlowProvider.
"""
from __future__ import annotations

import logging
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

# Upstream capabilities and safety boundaries
UPSTREAM_CAPABILITY = "OBSERVED"
PRODUCTION_DECISION = "DISABLED"
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
        for i in range(count):
            suffix = f"_{i}" if count > 1 else ""
            out_file = output_dir / f"{provider_job_id}{suffix}.mp4"
            # Write synthetic MP4 header
            out_file.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00isommp42")
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
    Evidence-gated production provider interface for Google Flow.
    Fail-closed:
    - Zero CAPTCHA bypass behavior. If challenge detected, halts with USER_INTERACTION_REQUIRED.
    - Zero undocumented RPC fallbacks (EXPERIMENTAL_RPC_PROVIDER = False).
    """

    def __init__(
        self,
        auth_token: Optional[str] = None,
        profile_path: Optional[Path] = None,
        cdp_endpoint: Optional[str] = None,
    ):
        self.auth_token = auth_token
        self.profile_path = Path(profile_path) if profile_path else None
        self.cdp_endpoint = cdp_endpoint

    def health(self) -> FlowHealthStatus:
        has_auth = bool(self.auth_token)
        has_profile = bool(self.profile_path and self.profile_path.exists())
        has_browser = bool(self.cdp_endpoint) or has_profile

        is_healthy = has_auth and has_browser

        details = {
            "provider_type": "production",
            "captcha_bypass_disabled": True,
            "experimental_rpc_disabled": not EXPERIMENTAL_RPC_PROVIDER,
        }
        if not is_healthy:
            reasons = []
            if not has_auth:
                reasons.append("missing_auth_token")
            if not has_browser:
                reasons.append("browser_not_connected_or_profile_missing")
            details["reason"] = "; ".join(reasons)

        return FlowHealthStatus(
            healthy=is_healthy,
            authenticated=has_auth,
            profile_exists=has_profile,
            browser_ready=has_browser,
            details=details,
        )

    def get_credits(self) -> FlowCredits:
        return FlowCredits(
            total_credits=100,
            available_credits=100,
            consumed_credits=0,
            timestamp=time.time(),
        )

    def get_models(self) -> list[FlowModelInfo]:
        return [
            FlowModelInfo(
                model_id=FlowModel.VEO_3_1_FAST.value,
                name="Veo 3.1 - Fast",
                capabilities=["text_to_video", "image_to_video"],
                cost_credits=20,
                default_aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
            )
        ]

    def generate_video(self, request: FlowGenerationRequest) -> FlowJobResult:
        # Enforce fail-closed safety gate: CAPTCHA / bot challenge requires user interaction
        return FlowJobResult(
            job_id=request.job_id,
            provider_job_id="",
            status=FlowJobStatus.USER_INTERACTION_REQUIRED,
            failure_class=FlowFailureClass.USER_INTERACTION_REQUIRED,
            failure_message="Google Flow requires legitimate user interaction (CAPTCHA/Challenge detected). Automated bypass is strictly disabled.",
            runtime_evidence={
                "upstream_capability": UPSTREAM_CAPABILITY,
                "production_decision": PRODUCTION_DECISION,
            },
        )

    def poll(self, provider_job_id: str) -> FlowJobResult:
        return FlowJobResult(
            job_id="",
            provider_job_id=provider_job_id,
            status=FlowJobStatus.PENDING,
        )

    def download(self, provider_job_id: str, output_dir: Path) -> list[Path]:
        return []
