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

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


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


def convert_video_to_vertical_video(
    video_path: Path,
    output_clip: Path,
    *,
    target_duration: float = 5.0,
    width: int = 1080,
    height: int = 1920,
) -> Path:
    """
    Normalize product video to 9:16 vertical video (1080x1920).
    Loops video if shorter than target_duration and strips audio (-an).
    """
    output_clip.parent.mkdir(parents=True, exist_ok=True)
    src_dur = probe_clip_duration(video_path)

    vf_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
    )

    if src_dur > 0 and src_dur < target_duration:
        # Loop short video so scene visual does not truncate before audio
        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", str(video_path),
            "-t", str(target_duration),
            "-vf", vf_filter,
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-an",
            str(output_clip),
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-t", str(target_duration),
            "-vf", vf_filter,
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-an",
            str(output_clip),
        ]

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not output_clip.exists() or output_clip.stat().st_size == 0:
        if output_clip.exists():
            output_clip.unlink(missing_ok=True)
        err = res.stderr.strip() if res.stderr else f"Exit code {res.returncode}"
        raise RuntimeError(f"Failed to process product video {video_path}: {err}")
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
        Deterministic unified pool: consumes videos followed by images in unified order.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        unified_media_pool: list[Path] = []

        # 1. Collect valid video files
        for v in product.videos:
            p = Path(v)
            if p.exists() and p.stat().st_size > 0:
                unified_media_pool.append(p)

        # 2. Collect valid image files
        for img in product.images:
            p = Path(img)
            if p.exists() and p.stat().st_size > 0:
                unified_media_pool.append(p)

        if not unified_media_pool:
            raise InsufficientMediaError(
                f"No usable product images or videos provided for product '{product.product_id}'."
            )

        scenes = variant.scenes or []
        scene_count = max(len(scenes), 3)
        staged_clips: list[Path] = []

        # Process each scene clip
        for idx in range(1, scene_count + 1):
            out_clip = output_dir / f"scene{idx:02d}.mp4"
            target_dur = scenes[idx - 1].duration_seconds if (idx - 1) < len(scenes) else 5.0
            source_item = unified_media_pool[(idx - 1) % len(unified_media_pool)]

            if source_item.suffix.lower() in VIDEO_EXTENSIONS:
                convert_video_to_vertical_video(source_item, out_clip, target_duration=target_dur)
            else:
                convert_image_to_vertical_video(source_item, out_clip, duration=target_dur)

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
