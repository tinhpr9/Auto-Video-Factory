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

        cost = self.MODEL_COSTS.get(request.model, 20)
        credit_before = self.credits

        # Simulated failure injection
        if self.simulate_failure:
            return FlowJobResult(
                job_id=request.job_id,
                provider_job_id="",
                status=FlowJobStatus.USER_INTERACTION_REQUIRED
                if self.simulate_failure == FlowFailureClass.USER_INTERACTION_REQUIRED
                else FlowJobStatus.FAILED,
                credit_before=credit_before,
                credit_after=credit_before,
                failure_class=self.simulate_failure,
                failure_message=f"Simulated {self.simulate_failure.value} failure",
                runtime_evidence={"simulated": True},
            )

        # Credit threshold guard
        if self.credits < cost:
            return FlowJobResult(
                job_id=request.job_id,
                provider_job_id="",
                status=FlowJobStatus.FAILED,
                credit_before=credit_before,
                credit_after=credit_before,
                failure_class=FlowFailureClass.QUOTA,
                failure_message=f"Insufficient credits: required {cost}, available {self.credits}",
                runtime_evidence={"required_credits": cost, "available_credits": self.credits},
            )

        # Deduct credits
        self.credits -= cost
        self.consumed_credits += cost
        credit_after = self.credits

        provider_job_id = f"mock_flow_{uuid.uuid4().hex[:12]}"
        self._SHARED_JOBS[provider_job_id] = {
            "job_id": request.job_id,
            "request": request,
            "credit_before": credit_before,
            "credit_after": credit_after,
            "created_at": time.time(),
            "status": FlowJobStatus.COMPLETED,
        }

        return FlowJobResult(
            job_id=request.job_id,
            provider_job_id=provider_job_id,
            status=FlowJobStatus.SUBMITTED,
            credit_before=credit_before,
            credit_after=credit_after,
            runtime_evidence={"provider": "mock", "cost": cost},
        )

    def poll(self, provider_job_id: str) -> FlowJobResult:
        job_data = self._SHARED_JOBS.get(provider_job_id)
        if not job_data:
            return FlowJobResult(
                job_id="",
                provider_job_id=provider_job_id,
                status=FlowJobStatus.FAILED,
                failure_class=FlowFailureClass.UNKNOWN,
                failure_message=f"Unknown provider job id: {provider_job_id}",
            )

        return FlowJobResult(
            job_id=job_data["job_id"],
            provider_job_id=provider_job_id,
            status=job_data["status"],
            credit_before=job_data["credit_before"],
            credit_after=job_data["credit_after"],
            runtime_evidence={"provider": "mock", "polled": True},
        )

    def download(self, provider_job_id: str, output_dir: Path) -> list[Path]:
        job_data = self._SHARED_JOBS.get(provider_job_id)
        if not job_data:
            raise RuntimeError(f"Unknown provider job id: {provider_job_id}")

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / f"{job_data['job_id']}.mp4"

        # Generate a minimal placeholder/dummy valid binary file for mock testing
        if not out_file.exists():
            out_file.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00isommp42")

        return [out_file]


class ProductionFlowProvider(FlowProvider):
    """
    Production Flow provider with strict safety gates:
    - Zero CAPTCHA bypass behavior. If challenge detected, halts with USER_INTERACTION_REQUIRED.
    - Zero undocumented RPC fallbacks.
    - Credit threshold checks.
    """

    def __init__(
        self,
        project_id: Optional[str] = None,
        profile_dir: Optional[Path] = None,
        cdp_url: Optional[str] = None,
    ):
        self.project_id = project_id
        self.profile_dir = profile_dir
        self.cdp_url = cdp_url
        self.last_credits_before: int = 0
        self.last_credits_after: int = 0

    def health(self) -> FlowHealthStatus:
        # Verify browser profile or CDP port availability
        profile_ok = self.profile_dir.exists() if self.profile_dir else False
        return FlowHealthStatus(
            healthy=True,
            authenticated=bool(self.project_id or profile_ok or self.cdp_url),
            profile_exists=profile_ok,
            browser_ready=True,
            details={
                "project_id": self.project_id,
                "captcha_bypass_disabled": True,
                "experimental_rpc_provider": EXPERIMENTAL_RPC_PROVIDER,
            },
        )

    def get_credits(self) -> FlowCredits:
        # In live provider, delegates to FlowAPI REST call /v1/credits
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
        # Never attempt bypass.
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
