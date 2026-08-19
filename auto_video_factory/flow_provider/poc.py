"""
Minimal bounded Flow Provider POC execution.
Guarantees:
- Exactly ONE generation
- Zero paid stress test / no batch
- Records credits_before and credits_after
- Verifies downloaded media artifact
- Fails closed with USER_INTERACTION_REQUIRED on CAPTCHA/challenge
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .contract import FlowProvider
from .controller import FlowController
from .models import (
    FlowAspectRatio,
    FlowFailureClass,
    FlowJobStatus,
    FlowModel,
)
from .provider import MockFlowProvider, ProductionFlowProvider

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("flow_poc")


def run_poc(
    provider: FlowProvider,
    output_dir: Path,
    prompt: str = "A tranquil bamboo forest with morning sunlight filtering through mist",
) -> dict:
    """
    Execute minimal 7-step POC workflow.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    storage_path = output_dir / "poc_jobs.json"

    controller = FlowController(
        provider=provider,
        storage_path=storage_path,
        output_dir=output_dir,
    )

    log.info("=== STEP 1: HEALTH CHECK ===")
    health = controller.health()
    log.info("Health: %s (Details: %s)", health.healthy, health.details)
    assert health.healthy is True, "Provider health check failed"

    log.info("=== STEP 2: CREDITS CHECK ===")
    initial_credits = controller.get_credits()
    log.info("Initial Credits: available=%d, total=%d", initial_credits.available_credits, initial_credits.total_credits)

    log.info("=== STEP 3: MODELS LIST ===")
    models = controller.get_models()
    for m in models:
        log.info("  Model: %s | Cost: %d credits | Capabilities: %s", m.name, m.cost_credits, m.capabilities)
    assert len(models) > 0, "No models available"

    log.info("=== STEP 4: SUBMIT EXACTLY ONE GENERATION ===")
    credits_before = controller.get_credits().available_credits
    job_id = controller.submit(
        prompt=prompt,
        model=FlowModel.VEO_3_1_FAST,
        aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
    )
    log.info("Submitted Job ID: %s", job_id)

    log.info("=== STEP 5: PROCESS & POLL GENERATION ===")
    result = controller.process_next()
    assert result is not None, "Process returned None"

    if result.status == FlowJobStatus.USER_INTERACTION_REQUIRED or result.failure_class == FlowFailureClass.USER_INTERACTION_REQUIRED:
        log.warning("POC Result: USER_INTERACTION_REQUIRED (Automation stopped safely without bypass)")
        return {
            "poc_result": "USER_INTERACTION_REQUIRED",
            "job_id": job_id,
            "credits_before": credits_before,
            "credits_after": credits_before,
            "output_file": None,
        }

    if result.status != FlowJobStatus.COMPLETED:
        log.error("POC Generation Failed: %s (%s)", result.failure_class, result.failure_message)
        return {
            "poc_result": "FAILED",
            "job_id": job_id,
            "credits_before": credits_before,
            "credits_after": controller.get_credits().available_credits,
            "output_file": None,
            "failure_class": result.failure_class.value if result.failure_class else None,
        }

    log.info("=== STEP 6: DOWNLOAD VERIFICATION ===")
    credits_after = controller.get_credits().available_credits
    assert len(result.output_files) > 0, "No output files downloaded"
    primary_output = result.output_files[0]
    log.info("Downloaded Artifact: %s (%d bytes)", primary_output, primary_output.stat().st_size)

    log.info("=== STEP 7: VERIFY ARTIFACT INTEGRITY ===")
    assert primary_output.exists() and primary_output.stat().st_size > 0, "Artifact empty or missing"
    log.info("POC COMPLETED SUCCESSFULLY!")
    log.info("Credits Before: %d | Credits After: %d | Delta: -%d", credits_before, credits_after, credits_before - credits_after)

    return {
        "poc_result": "PASS",
        "job_id": job_id,
        "credits_before": credits_before,
        "credits_after": credits_after,
        "output_file": str(primary_output),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Flow Provider POC")
    parser.add_argument("--mock", action="store_true", default=True, help="Use MockFlowProvider (default)")
    parser.add_argument("--out", type=str, default="./output_flow_poc", help="Output directory")
    args = parser.parse_args()

    prov = MockFlowProvider(initial_credits=200)
    res = run_poc(prov, Path(args.out))
    print(json.dumps(res, indent=2))
