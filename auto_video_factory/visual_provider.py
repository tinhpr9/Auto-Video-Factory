"""
Visual Provider Architecture for Auto-Video-Factory.

Supports:
- PexelsVisualProvider: Stock footage via Pexels API (free tier / baseline).
- GeminiVideoProvider: 100% Automated AI video generation via Google Gemini Omni Flash / Veo.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("auto_video_factory.visual_provider")

# ---------------------------------------------------------------------------
# Single Source of Truth for Models
# ---------------------------------------------------------------------------

GEMINI_VIDEO_MODEL: str = "gemini-omni-flash-preview"
GEMINI_VIDEO_FALLBACK_MODEL: str = "veo-2.0-generate-001"
DEFAULT_QUALITY_MODE: str = "economy"
DEFAULT_MAX_GENERATION_SECONDS: int = 60

# ---------------------------------------------------------------------------
# Character Bible for Xianxia / Fantasy Visual Consistency
# ---------------------------------------------------------------------------

CHARACTER_BIBLE: dict[str, Any] = {
    "protagonist": {
        "identity": "Exiled disciple turning into ancient master",
        "age": "Early 20s",
        "features": "Determined sharp eyes, raven black long hair tied in a high ponytail, weathered yet noble face",
        "attire": "Torn disciple robes transforming into flowing dark azure and charcoal cultivation robes with subtle gold embroidery",
        "aura": "Subtle radiant ancient azure and golden spiritual qi swirling faintly",
    },
    "world_style": {
        "genre": "Cinematic cultivation xianxia fantasy",
        "environment": "Towering misty sacred mountain peaks, ancient stone temple pavilions, deep shadowy caverns, snow-swept mountain paths",
        "lighting": "Dramatic cinematic volumetric rim lighting, mystical twilight, glowing ancient runes",
        "palette": "Deep jade, ethereal azure, misty slate grey, warm golden spirit highlights",
        "camera": "Dynamic cinematic tracking shots, sweeping wide environmental angles, close-up dramatic character expressions",
    },
    "negative_constraints": [
        "modern clothing",
        "modern buildings",
        "cars",
        "technology",
        "astronaut",
        "desert dunes",
        "greenhouse",
        "distorted anatomy",
        "text overlays",
        "watermarks",
    ],
}


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class ScenePlan:
    scene_id: int
    narrative_role: str
    duration_target: int
    prompt: str
    environment: str
    action: str
    camera: str
    lighting: str
    character_details: str
    negative_constraints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BillingBlockedError(Exception):
    """Raised when Gemini Video API is not available or billing is not enabled."""
    pass


class CostCeilingExceededError(Exception):
    """Raised when planned generation exceeds configured budget/cost ceiling."""
    pass


# ---------------------------------------------------------------------------
# Base Provider Interface
# ---------------------------------------------------------------------------

class VisualProvider(ABC):
    """Abstract base class for video visual providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier string."""
        pass

    @abstractmethod
    def preflight_check(self, api_key: str) -> dict[str, Any]:
        """Verify API key and provider availability before running tasks."""
        pass


# ---------------------------------------------------------------------------
# Pexels Visual Provider (Baseline)
# ---------------------------------------------------------------------------

class PexelsVisualProvider(VisualProvider):
    """Stock footage provider via Pexels API."""

    @property
    def name(self) -> str:
        return "pexels"

    def preflight_check(self, api_key: str) -> dict[str, Any]:
        if not api_key or not api_key.strip():
            return {
                "status": "error",
                "provider": self.name,
                "reason": "missing_api_key",
                "message": "PEXELS_API_KEY is missing or empty.",
            }
        return {
            "status": "ok",
            "provider": self.name,
            "message": "Pexels API key provided.",
        }


# ---------------------------------------------------------------------------
# Gemini Video Provider (100% Automated AI Video)
# ---------------------------------------------------------------------------

class GeminiVideoProvider(VisualProvider):
    """
    100% Automated Visual Provider powered by Google Gemini Omni Flash / Veo.
    Generates structured scene plans and video clips in 9:16 portrait format.
    """

    def __init__(
        self,
        model_name: str = GEMINI_VIDEO_MODEL,
        fallback_model: str = GEMINI_VIDEO_FALLBACK_MODEL,
    ) -> None:
        self.model_name = model_name
        self.fallback_model = fallback_model

    @property
    def name(self) -> str:
        return "gemini_video"

    def preflight_check(self, api_key: str) -> dict[str, Any]:
        """
        Validate GEMINI_API_KEY and check if Gemini API is accessible.
        """
        if not api_key or not api_key.strip():
            return {
                "status": "error",
                "provider": self.name,
                "reason": "missing_api_key",
                "message": "GEMINI_API_KEY is missing or empty.",
            }

        # Check API key by querying Gemini model metadata endpoint
        url = "https://generativelanguage.googleapis.com/v1beta/models"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Auto-Video-Factory/4.2",
                "x-goog-api-key": api_key.strip(),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name", "") for m in data.get("models", [])]
                return {
                    "status": "ok",
                    "provider": self.name,
                    "model": self.model_name,
                    "available_models_count": len(models),
                    "message": "Gemini API authenticated successfully.",
                }
        except urllib.error.HTTPError as e:
            code = e.code
            body = e.read().decode("utf-8", errors="ignore")
            if code == 400:
                reason = "invalid_api_key"
            elif code in (401, 403):
                reason = "unauthorized_or_billing_required"
            elif code == 429:
                reason = "rate_limit_exceeded"
            else:
                reason = f"http_{code}"
            return {
                "status": "error",
                "provider": self.name,
                "reason": reason,
                "http_code": code,
                "message": f"Gemini API check failed ({code}): {body[:200]}",
            }
        except Exception as e:
            return {
                "status": "error",
                "provider": self.name,
                "reason": "network_error",
                "message": f"Gemini API connection error: {e}",
            }

    def plan_scenes(
        self,
        *,
        topic: str,
        duration_seconds: int = 45,
        quality_mode: str = DEFAULT_QUALITY_MODE,
        max_seconds: int = DEFAULT_MAX_GENERATION_SECONDS,
    ) -> list[ScenePlan]:
        """
        Create a structured ScenePlan with global Character Bible consistency.
        For a 45s video, plans ~5 scenes (each 8-9s).
        """
        # Calculate planned scenes
        if quality_mode == "smoke":
            target_scene_duration = 4  # 3-5s smoke test clip
            scene_count = 1
        else:
            target_scene_duration = 8  # standard Omni/Veo clip target
            scene_count = max(3, min(7, duration_seconds // target_scene_duration))

        planned_total_seconds = scene_count * target_scene_duration

        if planned_total_seconds > max_seconds:
            raise CostCeilingExceededError(
                f"Planned generation ({planned_total_seconds}s) exceeds cost ceiling ({max_seconds}s)."
            )

        narrative_roles = [
            ("Establishing Sect", "Grand towering mountain sect with floating mist and ancient pavilions"),
            ("Unjust Betrayal", "Disciples standing cold in snow while protagonist is cast out and stripped of status"),
            ("Cavern Awakening", "Dark ancient cavern illuminated by glowing runes and swirling golden azure spiritual qi"),
            ("Return in Power", "Protagonist walking back up sect stone stairs surrounded by majestic aura"),
            ("Final Confrontation", "Overwhelming spiritual pressure descending, enemies kneeling in awe"),
        ]

        # Adjust for required scene count
        selected_roles = narrative_roles[:scene_count]
        while len(selected_roles) < scene_count:
            idx = len(selected_roles) + 1
            selected_roles.append((f"Journey Phase {idx}", "Cinematic mountain path under celestial storm clouds"))

        protag = CHARACTER_BIBLE["protagonist"]
        world = CHARACTER_BIBLE["world_style"]
        negatives = CHARACTER_BIBLE["negative_constraints"]

        scenes: list[ScenePlan] = []
        for i, (role, env_desc) in enumerate(selected_roles, start=1):
            prompt = (
                f"9:16 vertical cinematic shot of a young Vietnamese cultivation fantasy protagonist ({protag['features']}), "
                f"wearing {protag['attire']}. Scene {i}: {role}. "
                f"Setting: {env_desc}, {world['environment']}. "
                f"Visual style: {world['genre']}, {world['lighting']}, palette of {world['palette']}. "
                f"Camera: {world['camera']}."
            )
            scenes.append(
                ScenePlan(
                    scene_id=i,
                    narrative_role=role,
                    duration_target=target_scene_duration,
                    prompt=prompt,
                    environment=env_desc,
                    action=f"Character performing {role.lower()}",
                    camera="Cinematic slow tracking portrait camera",
                    lighting=world["lighting"],
                    character_details=f"{protag['identity']}, {protag['features']}, {protag['attire']}",
                    negative_constraints=negatives,
                )
            )

        return scenes

    def generate_scene_clip(
        self,
        scene: ScenePlan,
        output_path: Path,
        api_key: str,
    ) -> Path:
        """
        Generate a video clip for the given scene using Google Gemini Video API.
        Fail-closed: If billing or permissions are not enabled, raises BillingBlockedError.
        """
        if not api_key:
            raise BillingBlockedError("Missing GEMINI_API_KEY for video generation.")

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateVideos"
        prompt_text = scene.prompt
        if scene.negative_constraints:
            prompt_text += f" Negative constraints: {', '.join(scene.negative_constraints)}."
        payload = {
            "prompt": prompt_text,
            "aspectRatio": "9:16",
            "durationSeconds": scene.duration_target,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Auto-Video-Factory/4.2",
                "x-goog-api-key": api_key.strip(),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))
                output_path.parent.mkdir(parents=True, exist_ok=True)
                video_bytes = res_data.get("video", {}).get("bytesBase64Encoded")
                if not video_bytes:
                    raise RuntimeError("Gemini Video API returned no base64 video data.")

                import base64
                raw_tmp = output_path.with_suffix(".tmp.mp4")
                try:
                    raw_tmp.write_bytes(base64.b64decode(video_bytes))
                    self.strip_audio(raw_tmp, output_path)
                finally:
                    raw_tmp.unlink(missing_ok=True)

                if not self.validate_scene_clip(output_path):
                    if output_path.exists():
                        output_path.unlink(missing_ok=True)
                    raise RuntimeError(f"Generated scene clip failed validation: {output_path}")

                return output_path
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            if e.code in (401, 403, 404) or "billing" in err_body.lower() or "permission" in err_body.lower() or "not supported" in err_body.lower():
                raise BillingBlockedError(f"Gemini Video API blocked (HTTP {e.code}): {err_body}") from e
            elif e.code == 429:
                raise RuntimeError(f"Gemini API rate limit exceeded: {err_body}") from e
            raise RuntimeError(f"Gemini Video generation failed (HTTP {e.code}): {err_body}") from e
        except Exception as e:
            if isinstance(e, BillingBlockedError):
                raise
            raise RuntimeError(f"Gemini Video generation error: {e}") from e

    def strip_audio(self, input_video: Path, output_video: Path) -> Path:
        """
        Ensure scene clip has no native audio so Vietnamese Edge TTS + BGM
        remain the sole audio authority.
        MUST FAIL CLOSED: Never copy original audio-bearing clip if stripping fails.
        """
        output_video.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_video),
            "-an",
            "-c:v", "copy",
            str(output_video),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not output_video.exists() or output_video.stat().st_size == 0:
            if output_video.exists():
                output_video.unlink(missing_ok=True)
            err_msg = result.stderr.strip() if result.stderr else f"empty output or exit code {result.returncode}"
            raise RuntimeError(
                f"Fail-closed: Audio stripping failed for {input_video} ({err_msg}). "
                "Native audio must not leak into production."
            )

        # Verify no audio streams remain — MUST be fail-closed
        cmd_probe = [
            "ffprobe", "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=codec_type",
            "-of", "json",
            str(output_video),
        ]
        try:
            probe_res = subprocess.run(cmd_probe, capture_output=True, text=True, timeout=10)
        except (FileNotFoundError, OSError) as exc:
            output_video.unlink(missing_ok=True)
            raise RuntimeError(
                f"Fail-closed: ffprobe unavailable for audio verification of {output_video}: {exc}. "
                "Cannot confirm zero-audio state."
            ) from exc
        except subprocess.TimeoutExpired:
            output_video.unlink(missing_ok=True)
            raise RuntimeError(
                f"Fail-closed: ffprobe timed out verifying {output_video}. "
                "Cannot confirm zero-audio state."
            )

        if probe_res.returncode != 0:
            output_video.unlink(missing_ok=True)
            raise RuntimeError(
                f"Fail-closed: ffprobe failed verifying {output_video}: {probe_res.stderr.strip()}"
            )

        try:
            probe_data = json.loads(probe_res.stdout)
            audio_streams = [s for s in probe_data.get("streams", []) if s.get("codec_type") == "audio"]
            if audio_streams:
                output_video.unlink(missing_ok=True)
                raise RuntimeError(
                    f"Fail-closed: {output_video} still contains {len(audio_streams)} audio stream(s) after stripping."
                )
        except json.JSONDecodeError as exc:
            output_video.unlink(missing_ok=True)
            raise RuntimeError(
                f"Fail-closed: Could not parse ffprobe output for {output_video}: {exc}"
            ) from exc

        return output_video

    def validate_scene_clip(self, clip_path: Path) -> bool:
        """
        Validate generated scene clip using ffprobe.
        Checks: non-zero, video stream exists, valid duration.
        """
        if not clip_path.exists() or clip_path.stat().st_size == 0:
            return False

        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "stream=codec_type,width,height:format=duration",
            "-of", "json",
            str(clip_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                return False
            data = json.loads(result.stdout)
            streams = data.get("streams", [])
            has_video = any(s.get("codec_type") == "video" for s in streams)
            duration = float(data.get("format", {}).get("duration", 0))
            return has_video and duration > 0
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Factory Registry
# ---------------------------------------------------------------------------

_PROVIDERS: dict[str, type[VisualProvider]] = {
    "pexels": PexelsVisualProvider,
    "gemini_video": GeminiVideoProvider,
}


def get_visual_provider(provider_name: str = "pexels") -> VisualProvider:
    """
    Get a visual provider instance by name.

    Args:
        provider_name: 'pexels' or 'gemini_video'.

    Returns:
        VisualProvider instance.

    Raises:
        ValueError: If provider_name is unknown.
    """
    key = str(provider_name).strip().lower()
    provider_cls = _PROVIDERS.get(key)
    if provider_cls is None:
        accepted = ", ".join(sorted(_PROVIDERS))
        raise ValueError(
            f"Unknown visual provider {provider_name!r}; accepted values: {accepted}"
        )
    return provider_cls()
