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
    "15": 1,
    "20": 2,
    "30": 3,
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


def validate_script_duration_budget(
    script: str,
    duration: str,
    tolerance_ratio: float = 0.25,
) -> tuple[bool, int, int, str]:
    """
    Validate that a candidate Vietnamese script satisfies the duration word budget.

    Args:
        script: The candidate script string.
        duration: Duration string ("45", "60", "90").
        tolerance_ratio: Allowable relative deviation from target word count (default 0.25).

    Returns:
        tuple (is_valid, word_count, target_words, message)
    """
    cleaned = (script or "").strip()
    target_words = duration_to_word_budget(duration)
    if not cleaned:
        return False, 0, target_words, "Script is empty."

    words = [w for w in cleaned.split() if w]
    word_count = len(words)

    # Check terminal punctuation (complete sentence)
    if not cleaned.endswith((".", "!", "?", "...", "”", '"', "’", "'")):
        return False, word_count, target_words, "Script does not end with terminal punctuation (incomplete sentence)."

    min_words = int(target_words * (1.0 - tolerance_ratio))
    max_words = int(target_words * (1.0 + tolerance_ratio))

    if word_count < min_words:
        return False, word_count, target_words, f"Script is too short ({word_count} words < {min_words} words minimum for {duration}s)."
    if word_count > max_words:
        return False, word_count, target_words, f"Script is too long ({word_count} words > {max_words} words maximum for {duration}s)."

    return True, word_count, target_words, "OK"


def extract_script_from_output(raw_output: str) -> str:
    """
    Extract script string from CLI JSON stdout (e.g. {"task_id": "...", "result": {"script": "..."}}).
    """
    if not raw_output:
        return ""
    for line in raw_output.splitlines():
        line = line.strip()
        if "result" in line and ("script" in line or "video_script" in line):
            try:
                # Find outermost JSON object
                start = line.find("{")
                end = line.rfind("}")
                if start != -1 and end != -1 and end > start:
                    data = json.loads(line[start : end + 1])
                    if isinstance(data, dict):
                        res = data.get("result", {})
                        if isinstance(res, dict):
                            s = res.get("script") or res.get("video_script") or res.get("content")
                            if s and isinstance(s, str):
                                return s.strip()
            except Exception:
                continue
    return ""


def extract_script_from_task(
    mpt_root: str = ".",
    task_id: str | None = None,
    raw_output: str = "",
) -> str:
    """
    Extract generated script from CLI output or an MPT task directory.
    """
    if raw_output:
        extracted = extract_script_from_output(raw_output)
        if extracted:
            return extracted

    storage_tasks = Path(mpt_root) / "storage" / "tasks"
    if not storage_tasks.exists():
        raise FileNotFoundError(f"Storage tasks directory not found: {storage_tasks}")

    if task_id:
        task_dir = storage_tasks / task_id
    else:
        dirs = [d for d in storage_tasks.iterdir() if d.is_dir()]
        if not dirs:
            raise FileNotFoundError(f"No task directory found in {storage_tasks}")
        dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
        task_dir = dirs[0]

    for filename in ["script.json", "config.json", "task.json", "video.json"]:
        candidate = task_dir / filename
        if candidate.exists():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    script = data.get("video_script") or data.get("script") or data.get("content")
                    if script and isinstance(script, str):
                        return script.strip()
            except Exception:
                continue

    for candidate in task_dir.glob("*.json"):
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                script = data.get("video_script") or data.get("script") or data.get("content")
                if script and isinstance(script, str):
                    return script.strip()
        except Exception:
            continue

    raise FileNotFoundError(f"No valid script found in task directory: {task_dir}")


def build_cli_args(
    *,
    topic: str,
    duration: str,
    voice: str,
    video_source: str = "pexels",
    font_name: str = DEFAULT_FONT_NAME,
    video_materials: str = "",
    video_clip_duration: int | float | None = None,
    video_script: str = "",
    video_script_prompt: str = "",
    stop_at: str = "",
) -> list[str]:
    """
    Build the list of CLI arguments for ``python cli.py``.

    This function never executes a subprocess; it only constructs the argument
    list.  Shell-metacharacter injection is impossible when the list is passed
    to ``subprocess.run(..., shell=False)``.

    Args:
        topic:               Raw topic string from workflow_dispatch input.
        duration:            Duration string ("45", "60", "90").
        voice:               Voice name ("marin", "onyx").
        video_source:        Material source ("pexels", "pixabay", "coverr", "local").
        font_name:           Subtitle font filename in resource/fonts (default: NotoSans-Bold.ttf).
        video_materials:     Comma-separated list or directory of local video clips when video_source is 'local'.
        video_clip_duration: Duration of each video clip segment in seconds.
        video_script:        Pre-locked complete Vietnamese video script.
        video_script_prompt: Prompt for LLM script generation when video_script is not pre-locked.
        stop_at:             Stop stage ("script", etc.)

    Returns:
        Argument list ready for ``subprocess.run([python, "cli.py", *args])``.

    Raises:
        ValueError: On invalid duration or voice.
    """
    paragraphs = duration_to_paragraphs(duration)
    word_budget = duration_to_word_budget(duration)
    mpt_voice = map_voice(voice)
    
    if not video_script_prompt:
        video_script_prompt = (
            f"Hãy viết kịch bản dẫn chuyện tiếng Việt hoàn chỉnh, hấp dẫn, gồm đúng {paragraphs} đoạn văn chi tiết (tuyệt đối không bỏ dở câu). "
            f"Tổng độ dài khoảng {word_budget} từ để thời lượng đọc vừa vặn {duration} giây."
        )

    args = [
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
    ]

    if video_script:
        args.extend(["--video-script", video_script])
    else:
        args.extend(["--video-script-prompt", video_script_prompt])

    if video_clip_duration is not None:
        args.extend(["--video-clip-duration", str(int(round(float(video_clip_duration))))])

    if stop_at:
        args.extend(["--stop-at", stop_at])

    if video_source == "local" and video_materials:
        args.extend(["--video-materials", video_materials])

    return args


# ---------------------------------------------------------------------------
# config.toml builder
# ---------------------------------------------------------------------------

def build_config_toml(
    *,
    pexels_api_key: str = "",
    llm_provider: str = "gemini",
    llm_api_key: str = "",
    pixabay_api_key: str = "",
    font_name: str = "NotoSans-Bold.ttf",
) -> str:
    """
    Generate a minimal config.toml for MoneyPrinterTurbo.

    Secrets are placed inside the config file (written transiently in the
    Actions runner's workspace), never passed via CLI args where they could
    appear in process listing or logs.

    Args:
        pexels_api_key:  Optional Pexels API key.
        llm_provider:    LLM provider name, e.g. "gemini" or "openai".
        llm_api_key:     Optional LLM API key.
        pixabay_api_key: Optional Pixabay key.
        font_name:       Subtitle font name.

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
font_name = {json.dumps(font_name)}

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
font_name = {json.dumps(font_name)}
"""


# ---------------------------------------------------------------------------
# Output video locator
# ---------------------------------------------------------------------------

def locate_output_video(mpt_root: str, task_id: str | None = None) -> str:
    """
    Find the rendered MP4 inside the MPT task output directory.

    Args:
        mpt_root: Absolute path to the cloned MPT repo root.
        task_id:  Optional task_id returned by cli.py in its JSON stdout.
                  If omitted, searches for the latest rendered mp4 in storage.

    Returns:
        Absolute path to the (non-empty) MP4 file.

    Raises:
        FileNotFoundError: If no .mp4 is found in the task directory.
        ValueError:        If the found .mp4 is empty (0 bytes).
    """
    if task_id:
        task_dir = os.path.join(mpt_root, "storage", "tasks", task_id)
        pattern = os.path.join(task_dir, "**", "*.mp4")
        candidates = glob.glob(pattern, recursive=True)
    else:
        pattern = os.path.join(mpt_root, "storage", "tasks", "**", "*.mp4")
        candidates = glob.glob(pattern, recursive=True)
        if not candidates:
            pattern_storage = os.path.join(mpt_root, "storage", "**", "*.mp4")
            candidates = glob.glob(pattern_storage, recursive=True)

    if not candidates:
        search_target = task_dir if task_id else os.path.join(mpt_root, "storage")
        raise FileNotFoundError(
            f"No mp4 found in output directory: {search_target}"
        )

    # Prefer the newest / largest file if multiple exist
    candidates.sort(key=lambda p: (os.path.getmtime(p), os.path.getsize(p)), reverse=True)
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
