"""
Real product media ingestion, staging, and 9:16 vertical clip conversion for V5 Auto Affiliate Factory.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Sequence

from .models import (
    AffiliateScript,
    ProductInput,
)


class InsufficientMediaError(RuntimeError):
    """Raised when product media is missing or insufficient to render video scenes."""
    pass


def probe_clip_duration(clip_path: Path) -> float:
    """Probe duration of a media file via ffprobe."""
    if not clip_path.exists() or clip_path.stat().st_size == 0:
        return 0.0
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        str(clip_path),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if res.returncode != 0:
            return 0.0
        data = json.loads(res.stdout)
        return float(data.get("format", {}).get("duration", 0.0))
    except Exception:
        return 0.0


def convert_image_to_vertical_video(
    image_path: Path,
    output_clip: Path,
    *,
    duration: float = 5.0,
    width: int = 1080,
    height: int = 1920,
) -> Path:
    """
    Convert a 2D product image into a 9:16 vertical video clip with letterbox padding.
    Guarantees no audio track (-an).
    """
    output_clip.parent.mkdir(parents=True, exist_ok=True)
    # ffmpeg command to scale and pad to 1080x1920
    vf_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
    )
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(image_path),
        "-c:v", "libx264",
        "-t", str(duration),
        "-pix_fmt", "yuv420p",
        "-vf", vf_filter,
        "-an",
        str(output_clip),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not output_clip.exists() or output_clip.stat().st_size == 0:
        if output_clip.exists():
            output_clip.unlink(missing_ok=True)
        err = res.stderr.strip() if res.stderr else f"Exit code {res.returncode}"
        raise RuntimeError(f"Failed to convert image {image_path} to vertical video clip: {err}")
    return output_clip


class AffiliateMediaStager:
    """
    Ingests and stages real product media (videos & images) into sequential 9:16 clips.
    """

    def stage_media_for_variant(
        self,
        product: ProductInput,
        variant: AffiliateScript,
        output_dir: Path,
    ) -> list[Path]:
        """
        Stage media files for all scenes in the given affiliate variant.
        Deterministic order: product videos first, then converted product images.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        media_pool: list[Path] = []

        # 1. Collect existing video files
        for v in product.videos:
            p = Path(v)
            if p.exists() and p.stat().st_size > 0:
                media_pool.append(p)

        # 2. Collect existing image files
        image_pool: list[Path] = []
        for img in product.images:
            p = Path(img)
            if p.exists() and p.stat().st_size > 0:
                image_pool.append(p)

        if not media_pool and not image_pool:
            raise InsufficientMediaError(
                f"No usable product images or videos provided for product '{product.product_id}'."
            )

        scenes = variant.scenes or []
        scene_count = max(len(scenes), 3)
        staged_clips: list[Path] = []

        # Generate each scene clip
        for idx in range(1, scene_count + 1):
            out_clip = output_dir / f"scene{idx:02d}.mp4"
            target_dur = scenes[idx - 1].duration_seconds if (idx - 1) < len(scenes) else 5.0

            if media_pool:
                # Cycle through real product videos and normalize to 9:16 vertical video
                source_video = media_pool[(idx - 1) % len(media_pool)]
                vf_filter = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black"
                cmd = [
                    "ffmpeg", "-y",
                    "-i", str(source_video),
                    "-t", str(target_dur),
                    "-vf", vf_filter,
                    "-c:v", "libx264",
                    "-pix_fmt", "yuv420p",
                    "-an",
                    str(out_clip),
                ]
                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode != 0 or not out_clip.exists() or out_clip.stat().st_size == 0:
                    err = res.stderr.strip() if res.stderr else f"Exit code {res.returncode}"
                    raise RuntimeError(f"Failed to process product video {source_video}: {err}")
            elif image_pool:
                # Convert image to 9:16 vertical video
                source_img = image_pool[(idx - 1) % len(image_pool)]
                convert_image_to_vertical_video(source_img, out_clip, duration=target_dur)

            staged_clips.append(out_clip)

        return staged_clips


def prepare_product_media_clips(
    product: ProductInput,
    variant: AffiliateScript,
    output_dir: Path,
) -> list[Path]:
    """Convenience helper to stage media for a variant."""
    stager = AffiliateMediaStager()
    return stager.stage_media_for_variant(product, variant, output_dir)
