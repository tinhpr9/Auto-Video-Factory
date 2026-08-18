"""
MoneyPrinterTurbo adapter layer.

This module is the integration boundary between Auto-Video-Factory's
GitHub Actions workflow and the upstream MoneyPrinterTurbo CLI.

Upstream:    https://github.com/harry0703/MoneyPrinterTurbo
Pinned SHA:  b42e945b497176c823579f9b1895d9323446de23

Integration strategy: OPTION A — orchestration/adapter.
  Auto-Video-Factory remains a thin adapter; MPT is cloned at the pinned
  SHA inside GitHub Actions and driven through its own cli.py.
  This repo does NOT vendor upstream source.

To update upstream:
  1. Create a new branch/worktree (never commit directly to main).
  2. Bump MONEYPRINTERTURBO_REF to the new tested SHA.
  3. Run the full test suite and a real render smoke test.
  4. Open a PR, get review, then merge.
  5. Trigger workflow_dispatch on main to verify production artifact.
"""
from __future__ import annotations

import glob
import json
import os
import re
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Upstream pinning — single source of truth
# ---------------------------------------------------------------------------

MONEYPRINTERTURBO_REPO: str = "https://github.com/harry0703/MoneyPrinterTurbo"
MONEYPRINTERTURBO_REF: str = "b42e945b497176c823579f9b1895d9323446de23"

# ---------------------------------------------------------------------------
# Duration → paragraph count mapping
# ---------------------------------------------------------------------------

_DURATION_TO_PARAGRAPHS: dict[str, int] = {
    "45": 3,
    "60": 4,
    "90": 6,
}


def duration_to_paragraphs(duration: str) -> int:
    """
    Map a duration string (seconds) to a MoneyPrinterTurbo paragraph count.

    Args:
        duration: One of "45", "60", "90".

    Returns:
        Integer paragraph count (3, 4, or 6).

    Raises:
        ValueError: If duration is not one of the accepted values.
    """
    try:
        result = _DURATION_TO_PARAGRAPHS.get(str(duration).strip())
    except (TypeError, AttributeError):
        result = None
    if result is None:
        accepted = ", ".join(sorted(_DURATION_TO_PARAGRAPHS))
        raise ValueError(
            f"Invalid duration {duration!r}; accepted values: {accepted}"
        )
    return result


def duration_to_word_budget(duration: str) -> int:
    """
    Calculate the target word count for a given duration.
    Empirical evidence from GitHub Actions runtime:
    112 words of Vietnamese script generated 32 seconds of Edge TTS audio.
    Speed = 112 / 32 = 3.5 words per second.

    Args:
        duration: One of "45", "60", "90".

    Returns:
        Integer target word count.
    """
    try:
        dur_int = int(str(duration).strip())
    except ValueError:
        dur_int = 45

    # 3.5 words per second based on vi-VN Edge TTS
    return int(dur_int * 3.5)


# ---------------------------------------------------------------------------
# Voice mapping — workflow voice names → MPT Edge TTS identifiers
# ---------------------------------------------------------------------------

# All entries use Edge TTS (free, no API key required).
# vi-VN voices are chosen for Vietnamese content.
_VOICE_MAP: dict[str, str] = {
    "marin": "vi-VN-HoaiMyNeural",   # female Vietnamese narrator
    "onyx": "vi-VN-NamMinhNeural",   # male Vietnamese narrator
}


def map_voice(voice_name: str) -> str:
    """
    Map a workflow voice choice to a MoneyPrinterTurbo voice identifier.

    Args:
        voice_name: Workflow voice choice (e.g. "marin", "onyx").

    Returns:
        MPT-compatible Edge TTS voice identifier string.

    Raises:
        ValueError: If voice_name is not recognised.
    """
    result = _VOICE_MAP.get(voice_name.strip().lower() if voice_name else "")
    if result is None:
        accepted = ", ".join(sorted(_VOICE_MAP))
        raise ValueError(
            f"Unknown voice {voice_name!r}; accepted values: {accepted}"
        )
    return result


# ---------------------------------------------------------------------------
# CLI argument builder
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Font configuration — Vietnamese-capable Unicode font
# ---------------------------------------------------------------------------

DEFAULT_FONT_NAME: str = "NotoSans-Bold.ttf"


def build_cli_args(
    *,
    topic: str,
    duration: str,
    voice: str,
    video_source: str = "pexels",
    font_name: str = DEFAULT_FONT_NAME,
) -> list[str]:
    """
    Build the list of CLI arguments for ``python cli.py``.

    This function never executes a subprocess; it only constructs the argument
    list.  Shell-metacharacter injection is impossible when the list is passed
    to ``subprocess.run(..., shell=False)``.

    Args:
        topic:        Raw topic string from workflow_dispatch input.
        duration:     Duration string ("45", "60", "90").
        voice:        Voice name ("marin", "onyx").
        video_source: Material source ("pexels", "pixabay", "coverr", "local").
        font_name:    Subtitle font filename in resource/fonts (default: NotoSans-Bold.ttf).

    Returns:
        Argument list ready for ``subprocess.run([python, "cli.py", *args])``.

    Raises:
        ValueError: On invalid duration or voice.
    """
    paragraphs = duration_to_paragraphs(duration)
    word_budget = duration_to_word_budget(duration)
    mpt_voice = map_voice(voice)
    
    script_prompt = (
        f"Hãy viết kịch bản dẫn chuyện tiếng Việt hoàn chỉnh, hấp dẫn, gồm đúng {paragraphs} đoạn văn chi tiết (tuyệt đối không bỏ dở câu). "
        f"Tổng độ dài khoảng {word_budget} từ để thời lượng đọc vừa vặn {duration} giây."
    )

    return [
        "--video-subject", topic,            # verbatim — safe via subprocess list
        "--video-aspect", "9:16",            # portrait 1080×1920
        "--paragraph-number", str(paragraphs),
        "--voice-name", mpt_voice,
        "--video-source", video_source,
        "--subtitle-enabled",
        "--subtitle-position", "bottom",
        "--font-name", font_name,
        "--video-language", "vi-VN",
        "--bgm-type", "random",
        "--match-materials-to-script",
        "--video-script-prompt", script_prompt,
    ]


# ---------------------------------------------------------------------------
# config.toml builder
# ---------------------------------------------------------------------------

def build_config_toml(
    *,
    pexels_api_key: str,
    llm_provider: str,
    llm_api_key: str,
    pixabay_api_key: str = "",
) -> str:
    """
    Generate a minimal config.toml for MoneyPrinterTurbo.

    Secrets are placed inside the config file (written transiently in the
    Actions runner's workspace), never passed via CLI args where they could
    appear in process listing or logs.

    Args:
        pexels_api_key:  Pexels API key (from GitHub Secret PEXELS_API_KEY).
        llm_provider:    LLM provider name, e.g. "openai".
        llm_api_key:     LLM API key (from GitHub Secret LLM_API_KEY).
        pixabay_api_key: Optional Pixabay key.

    Returns:
        TOML string ready to write as ``config.toml`` inside the MPT directory.
    """
    pexels_list = f"[{json.dumps(pexels_api_key)}]" if pexels_api_key else "[]"
    pixabay_list = f"[{json.dumps(pixabay_api_key)}]" if pixabay_api_key else "[]"

    # LLM provider section — default to free-first (Gemini)
    if llm_provider == "gemini":
        llm_section = f'gemini_api_key = {json.dumps(llm_api_key)}\ngemini_model_name = "gemini-3.6-flash"\nllm_provider = "gemini"'
    elif llm_provider == "openai":
        llm_section = f'openai_api_key = {json.dumps(llm_api_key)}\nopenai_model_name = "gpt-4o-mini"\nllm_provider = "openai"'
    else:
        # Fallback: treat as gemini
        llm_section = f'gemini_api_key = {json.dumps(llm_api_key)}\ngemini_model_name = "gemini-3.6-flash"\nllm_provider = "gemini"'

    return f"""# Auto-generated by Auto-Video-Factory adapter — do not commit
log_level = "INFO"
listen_host = "127.0.0.1"
listen_port = 8080

[app]
tls_verify = true
video_source = "pexels"
pexels_api_keys = {pexels_list}
pixabay_api_keys = {pixabay_list}
match_materials_to_script = true

{llm_section}

subtitle_provider = "edge"

[proxy]

[azure]
speech_key = ""
speech_region = ""

[whisper]
model_size = "base"
device = "cpu"
compute_type = "int8"

[ui]
hide_log = false
open_task_folder_on_completion = false
"""


# ---------------------------------------------------------------------------
# Output video locator
# ---------------------------------------------------------------------------

def locate_output_video(mpt_root: str, task_id: str) -> str:
    """
    Find the rendered MP4 inside the MPT task output directory.

    Args:
        mpt_root: Absolute path to the cloned MPT repo root.
        task_id:  The task_id returned by cli.py in its JSON stdout.

    Returns:
        Absolute path to the (non-empty) MP4 file.

    Raises:
        FileNotFoundError: If no .mp4 is found in the task directory.
        ValueError:        If the found .mp4 is empty (0 bytes).
    """
    task_dir = os.path.join(mpt_root, "storage", "tasks", task_id)
    pattern = os.path.join(task_dir, "**", "*.mp4")
    candidates = glob.glob(pattern, recursive=True)

    if not candidates:
        raise FileNotFoundError(
            f"No mp4 found in task directory: {task_dir}"
        )

    # Prefer the largest file if multiple exist
    candidates.sort(key=lambda p: os.path.getsize(p), reverse=True)
    video_path = candidates[0]

    if os.path.getsize(video_path) == 0:
        raise ValueError(
            f"Output mp4 is empty (0 bytes): {video_path}"
        )

    return video_path


# ---------------------------------------------------------------------------
# Result metadata sanitiser
# ---------------------------------------------------------------------------

_SECRET_KEY_PATTERNS = re.compile(
    r"(api[_-]?key|secret|token|password|passwd|credential|auth)",
    re.IGNORECASE,
)


def sanitize_result_metadata(raw: Any) -> Any:
    """
    Return a copy of the metadata dict with secret-bearing keys removed.

    Recursively strips any key whose name matches a secret pattern
    (api_key, secret, token, password, credential, auth).

    Args:
        raw: Raw metadata dict or list (possibly from cli.py JSON output or config).

    Returns:
        New dict/list safe to upload as a workflow artifact.
    """
    if isinstance(raw, dict):
        cleaned: dict[str, Any] = {}
        for key, value in raw.items():
            if _SECRET_KEY_PATTERNS.search(str(key)):
                continue  # drop secret-bearing key
            cleaned[key] = sanitize_result_metadata(value)
        return cleaned
    elif isinstance(raw, list):
        return [sanitize_result_metadata(item) for item in raw]
    return raw
