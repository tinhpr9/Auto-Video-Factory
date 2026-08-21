"""
Flow Visual Provider for Auto-Video-Factory pipeline integration.

Adapts FlowController into the ImageProvider / VisualProvider pipeline contract:
- Explicit provider boundary
- Health check gate (fails closed if unhealthy)
- Safety gate (USER_INTERACTION_REQUIRED halts automation without bypass)
- Durable job state & recovery without duplicate generation
- Audio stripping fail-closed so narration + BGM remain authoritative
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional, Union

from ..models import Scene
from .contract import FlowProvider
from .controller import FlowController
from .models import (
    FlowAspectRatio,
    FlowFailureClass,
    FlowGenerationRequest,
    FlowJobResult,
    FlowJobStatus,
    FlowModel,
    FLOW_MODE_TO_MODEL,
    FLOW_MODEL_MAP,
    resolve_flow_model,
)

logger = logging.getLogger(__name__)


class FlowProviderError(RuntimeError):
    """Base error for Flow provider failures in the pipeline."""
    def __init__(self, message: str, failure_class: Optional[FlowFailureClass] = None) -> None:
        super().__init__(message)
        self.failure_class = failure_class


class FlowUserInteractionRequiredError(FlowProviderError):
    """Raised when Flow provider requires manual user interaction (e.g. CAPTCHA)."""
    def __init__(self, message: str = "Flow provider requires manual user interaction. Automation paused.") -> None:
        super().__init__(message, failure_class=FlowFailureClass.USER_INTERACTION_REQUIRED)


class FlowGenerationError(FlowProviderError):
    """Raised when Flow video generation fails."""
    pass


class FlowVisualProvider:
    """
    Image/video provider adapter implementing the pipeline ImageProvider interface.
    Delegates generation jobs to FlowController.
    """

    def __init__(
        self,
        controller: FlowController,
        model: Optional[Union[FlowModel, str]] = None,
        aspect_ratio: FlowAspectRatio = FlowAspectRatio.PORTRAIT_9_16,
        count: int = 1,
        flow_mode: str = "flow_balanced",
        width: int = 720,
        height: int = 1280,
    ) -> None:
        if flow_mode not in FLOW_MODE_TO_MODEL:
            raise ValueError(f"Unknown flow_mode '{flow_mode}'. Must be one of {list(FLOW_MODE_TO_MODEL.keys())}")
        if model is not None:
            model = resolve_flow_model(model)
        if count != 1:
            raise ValueError(f"FlowVisualProvider only supports count=1 for single-scene visual generation, got count={count}")
        self.controller = controller
        self.flow_mode = flow_mode
        self.explicit_model = model
        # Precedence: explicit model override > flow mode routing > default
        self.model = model if model is not None else FLOW_MODE_TO_MODEL[flow_mode]
        self.aspect_ratio = aspect_ratio
        self.count = count
        self.width = width
        self.height = height

    def _strip_audio_if_present(self, video_path: Path) -> None:
        """
        Strip audio streams from generated video clip to ensure zero native audio leakage.
        MUST FAIL CLOSED: Never silently pass if audio verification or stripping fails.
        """
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            raise FlowGenerationError(
                f"ffprobe is required for audio verification of {video_path} but is unavailable.",
                failure_class=FlowFailureClass.UNKNOWN,
            )

        probe_cmd = [
            ffprobe, "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=codec_type",
            "-of", "json",
            str(video_path),
        ]
        try:
            probe_res = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=15)
        except Exception as exc:
            raise FlowGenerationError(
                f"ffprobe execution failed for {video_path}: {exc}",
                failure_class=FlowFailureClass.UNKNOWN,
            ) from exc

        if probe_res.returncode != 0:
            raise FlowGenerationError(
                f"ffprobe failed to verify audio streams in {video_path}: {probe_res.stderr.strip()}",
                failure_class=FlowFailureClass.UNKNOWN,
            )

        try:
            data = json.loads(probe_res.stdout)
        except Exception as exc:
            raise FlowGenerationError(
                f"Failed to parse ffprobe json output for {video_path}: {exc}",
                failure_class=FlowFailureClass.UNKNOWN,
            ) from exc

        if data.get("streams"):
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                raise FlowGenerationError(
                    f"ffmpeg is required for audio stripping of {video_path} but is unavailable.",
                    failure_class=FlowFailureClass.UNKNOWN,
                )

            tmp_out = video_path.with_name(f".tmp_no_audio_{uuid.uuid4().hex[:8]}_{video_path.name}")
            strip_cmd = [
                ffmpeg, "-y",
                "-i", str(video_path),
                "-an",
                "-c:v", "copy",
                str(tmp_out),
            ]
            try:
                strip_res = subprocess.run(strip_cmd, capture_output=True, text=True, timeout=30)
            except Exception as exc:
                if tmp_out.exists():
                    tmp_out.unlink(missing_ok=True)
                raise FlowGenerationError(
                    f"ffmpeg strip execution failed for {video_path}: {exc}",
                    failure_class=FlowFailureClass.UNKNOWN,
                ) from exc

            if strip_res.returncode != 0 or not tmp_out.exists() or tmp_out.stat().st_size == 0:
                if tmp_out.exists():
                    tmp_out.unlink(missing_ok=True)
                raise FlowGenerationError(
                    f"Audio stripping failed for {video_path}: {strip_res.stderr.strip() if strip_res else 'empty output'}",
                    failure_class=FlowFailureClass.UNKNOWN,
                )

            os.replace(tmp_out, video_path)

            # Re-verify that audio streams were completely removed (fail-closed)
            try:
                reprobe_res = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=15)
                if reprobe_res.returncode != 0:
                    raise FlowGenerationError(
                        f"ffprobe post-strip verification failed for {video_path}: {reprobe_res.stderr.strip()}",
                        failure_class=FlowFailureClass.UNKNOWN,
                    )
                reprobe_data = json.loads(reprobe_res.stdout)
                if reprobe_data.get("streams"):
                    raise FlowGenerationError(
                        f"Audio stripping failed to remove audio streams from {video_path}.",
                        failure_class=FlowFailureClass.UNKNOWN,
                    )
            except Exception as exc:
                if isinstance(exc, FlowProviderError):
                    raise
                raise FlowGenerationError(
                    f"Post-strip audio verification failed for {video_path}: {exc}",
                    failure_class=FlowFailureClass.UNKNOWN,
                ) from exc

    def create(self, scene: Scene, output: Path) -> Path:
        """
        Generate visual media for a single scene via FlowController.
        Fails closed on unhealthy provider, safety pause, or generation error.
        Preserves video extension (.mp4) so downstream renderer applies video filters.
        """
        # 1. Health gate: fail-closed if provider reports unhealthy
        health = self.controller.health()
        if not health.healthy:
            details_str = json.dumps(health.details, ensure_ascii=False) if health.details else "unhealthy"
            raise FlowProviderError(
                f"Flow provider is unhealthy: authenticated={health.authenticated}, browser_ready={health.browser_ready} (details: {details_str})",
                failure_class=FlowFailureClass.AUTH if not health.authenticated else FlowFailureClass.UNKNOWN,
            )

        # 2. Safety pause gate: fail-closed if USER_INTERACTION_REQUIRED is active
        if self.controller._is_safety_paused():
            raise FlowUserInteractionRequiredError(
                "Flow provider is paused due to unresolved USER_INTERACTION_REQUIRED. Halting automation."
            )

        output.parent.mkdir(parents=True, exist_ok=True)

        # 3. Build request with deterministic hash-based job_id
        prompt_hash = hashlib.sha256(scene.visual_prompt.encode("utf-8")).hexdigest()[:8]
        job_id = f"scene_{scene.index:02d}_{prompt_hash}"
        req = FlowGenerationRequest(
            job_id=job_id,
            prompt=scene.visual_prompt,
            model=self.model,
            aspect_ratio=self.aspect_ratio,
            count=self.count,
            output_dir=output.parent,
        )

        # 4. Submit to controller
        submitted_job_id = self.controller.submit(req)

        # 5. Process specifically the submitted job ID
        result = self.controller.process_job(submitted_job_id)
        if result is None:
            raise FlowProviderError(f"No job record found for job_id '{submitted_job_id}'.")

        if result.job_id != submitted_job_id:
            raise FlowGenerationError(
                f"Job ID mismatch: expected {submitted_job_id}, got {result.job_id}",
                failure_class=FlowFailureClass.UNKNOWN,
            )

        # 6. Check result status
        if result.status == FlowJobStatus.USER_INTERACTION_REQUIRED:
            raise FlowUserInteractionRequiredError(
                f"USER_INTERACTION_REQUIRED during Flow generation for scene {scene.index}: {result.failure_message}"
            )
        elif result.status == FlowJobStatus.FAILED:
            raise FlowGenerationError(
                f"Flow video generation failed ({result.failure_class}): {result.failure_message}",
                failure_class=result.failure_class,
            )
        elif result.status != FlowJobStatus.COMPLETED:
            raise FlowGenerationError(
                f"Flow job ended in non-completed status: {result.status}",
                failure_class=result.failure_class,
            )

        # 7. Validate output artifact
        if not result.output_files or not result.output_files[0].exists() or result.output_files[0].stat().st_size == 0:
            raise FlowGenerationError(
                f"Flow generation completed but output artifact is missing or empty for scene {scene.index}.",
                failure_class=FlowFailureClass.DOWNLOAD_FAILED,
            )

        primary_artifact = result.output_files[0]
        target_output = output.with_suffix(primary_artifact.suffix)
        if primary_artifact.resolve() != target_output.resolve():
            shutil.copy2(primary_artifact, target_output)

        if target_output.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm"):
            self._strip_audio_if_present(target_output)

        if not target_output.exists() or target_output.stat().st_size == 0:
            raise FlowGenerationError(
                f"Final staged artifact {target_output} is missing or empty.",
                failure_class=FlowFailureClass.DOWNLOAD_FAILED,
            )

        return target_output

    def resume_pending(self) -> list[FlowJobResult]:
        """
        Resume any in-flight / pending jobs in the controller.
        Monitors existing provider_job_id to completion without re-invoking generate_video().
        """
        return self.controller.resume_pending_jobs()
