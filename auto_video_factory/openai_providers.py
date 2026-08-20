from __future__ import annotations

import base64
import io
import json
import os
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
try:
    from PIL import Image, ImageOps
except ImportError:
    Image = None
    ImageOps = None

from .models import Scene, StoryPlan
from .prompts import build_story_prompt


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, code: str, retryable: bool = False, status_code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


class ProviderConfigurationError(ProviderError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="PROVIDER_CONFIGURATION", retryable=False)


class ProviderResponseError(ProviderError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="PROVIDER_RESPONSE", retryable=False)


class ProviderRequestError(ProviderError):
    pass


class OpenAIHTTPClient:
    """Small stdlib HTTP client so cloud providers remain optional dependencies.

    Secrets are read from the environment and never embedded in errors or logs.
    Transient failures are retried; auth/configuration failures are not.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = 90.0,
        max_attempts: int = 3,
        retry_base_seconds: float = 0.35,
    ) -> None:
        resolved_key = (api_key or os.getenv("OPENAI_API_KEY", "")).strip()
        if not resolved_key:
            raise ProviderConfigurationError("OPENAI_API_KEY is required for --provider openai")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._api_key = resolved_key
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds

    def _request(self, path: str, payload: dict[str, Any], *, expect_json: bool) -> dict[str, Any] | bytes:
        url = f"{self.base_url}/{path.lstrip('/')}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )

        last_error: ProviderRequestError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                if expect_json:
                    try:
                        decoded = json.loads(raw.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise ProviderResponseError("provider returned invalid JSON") from exc
                    if not isinstance(decoded, dict):
                        raise ProviderResponseError("provider returned unexpected JSON shape")
                    return decoded
                return raw
            except urllib.error.HTTPError as exc:
                status = int(exc.code)
                retryable = status in {408, 409, 429} or 500 <= status <= 599
                if status in {401, 403}:
                    raise ProviderRequestError(
                        "provider authentication failed; check OPENAI_API_KEY",
                        code="AUTH_FAILED",
                        retryable=False,
                        status_code=status,
                    ) from exc
                last_error = ProviderRequestError(
                    f"provider request failed with HTTP {status}",
                    code="HTTP_ERROR",
                    retryable=retryable,
                    status_code=status,
                )
                if not retryable or attempt >= self.max_attempts:
                    raise last_error from exc
            except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
                last_error = ProviderRequestError(
                    "provider network request failed",
                    code="NETWORK_ERROR",
                    retryable=True,
                )
                if attempt >= self.max_attempts:
                    raise last_error from exc

            time.sleep(self.retry_base_seconds * (2 ** (attempt - 1)))

        if last_error is not None:  # defensive; loop normally raises/returns first
            raise last_error
        raise ProviderRequestError("provider request failed", code="UNKNOWN", retryable=False)

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = self._request(path, payload, expect_json=True)
        assert isinstance(result, dict)
        return result

    def post_bytes(self, path: str, payload: dict[str, Any]) -> bytes:
        result = self._request(path, payload, expect_json=False)
        assert isinstance(result, bytes)
        return result


def _extract_response_text(response: dict[str, Any]) -> str:
    convenience = response.get("output_text")
    if isinstance(convenience, str) and convenience.strip():
        return convenience.strip()
    for item in response.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
    raise ProviderResponseError("text model response did not contain output text")


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ProviderResponseError("story model returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise ProviderResponseError("story model returned non-object JSON")
    return data


class OpenAIStoryPlanner:
    def __init__(
        self,
        *,
        client: OpenAIHTTPClient,
        scene_count: int = 8,
        model: str = "gpt-5-mini",
        visual_style: str | None = None,
    ) -> None:
        if not 2 <= scene_count <= 12:
            raise ValueError("scene_count must be between 2 and 12")
        self.client = client
        self.scene_count = scene_count
        self.model = model
        self.visual_style = visual_style

    def plan(self, topic: str) -> StoryPlan:
        prompt = build_story_prompt(topic, self.scene_count, visual_style=self.visual_style)
        response = self.client.post_json(
            "/responses",
            {
                "model": self.model,
                "input": prompt,
            },
        )
        data = _parse_json_object(_extract_response_text(response))
        title = data.get("title")
        anchor = data.get("character_anchor")
        raw_scenes = data.get("scenes")
        if not isinstance(title, str) or not title.strip():
            raise ProviderResponseError("story response is missing title")
        if not isinstance(anchor, str) or not anchor.strip():
            raise ProviderResponseError("story response is missing character_anchor")
        if not isinstance(raw_scenes, list) or len(raw_scenes) != self.scene_count:
            raise ProviderResponseError(
                f"story response scene count mismatch: expected {self.scene_count}"
            )

        scenes: list[Scene] = []
        clean_anchor = " ".join(anchor.split()).strip()
        for index, raw_scene in enumerate(raw_scenes, start=1):
            if not isinstance(raw_scene, dict):
                raise ProviderResponseError(f"scene {index} is not an object")
            narration = raw_scene.get("narration")
            visual_prompt = raw_scene.get("visual_prompt")
            if not isinstance(narration, str) or not narration.strip():
                raise ProviderResponseError(f"scene {index} is missing narration")
            if not isinstance(visual_prompt, str) or not visual_prompt.strip():
                raise ProviderResponseError(f"scene {index} is missing visual_prompt")
            scenes.append(
                Scene(
                    index=index,
                    narration=" ".join(narration.split()).strip(),
                    visual_prompt=f"{clean_anchor}. {' '.join(visual_prompt.split()).strip()}",
                )
            )
        return StoryPlan(title=title.strip(), scenes=scenes)


class OpenAIImageProvider:
    def __init__(
        self,
        *,
        client: OpenAIHTTPClient,
        width: int = 720,
        height: int = 1280,
        model: str = "gpt-image-1-mini",
        quality: str = "low",
        style_prompt: str | None = None,
    ) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("width and height must be positive")
        self.client = client
        self.width = width
        self.height = height
        self.model = model
        self.quality = quality
        self.style_prompt = style_prompt or (
            "Original cinematic xianxia-inspired fantasy illustration, cold cyan and slate palette, "
            "misty mountains, dramatic soft light, detailed painterly realism, vertical short-video frame, "
            "no text, no logo, no watermark, no recognizable copyrighted character"
        )

    def create(self, scene: Scene, output: Path) -> Path:
        response = self.client.post_json(
            "/images/generations",
            {
                "model": self.model,
                "prompt": f"{self.style_prompt}. Scene: {scene.visual_prompt}",
                "size": "1024x1536",
                "quality": self.quality,
                "output_format": "png",
            },
        )
        data = response.get("data")
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            raise ProviderResponseError("image provider response is missing image data")
        encoded = data[0].get("b64_json")
        if not isinstance(encoded, str) or not encoded:
            raise ProviderResponseError("image provider response is missing b64_json")
        if Image is None or ImageOps is None:
            raise ProviderError("Pillow is required for OpenAIImageProvider.", code="DEPENDENCY_MISSING")
        try:
            raw = base64.b64decode(encoded, validate=True)
            with Image.open(io.BytesIO(raw)) as source:
                image = ImageOps.fit(
                    source.convert("RGB"),
                    (self.width, self.height),
                    method=Image.Resampling.LANCZOS,
                    centering=(0.5, 0.5),
                )
        except Exception as exc:
            if isinstance(exc, ProviderError):
                raise
            raise ProviderResponseError("image provider returned invalid image bytes") from exc
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, format="PNG", optimize=True)
        return output


class OpenAITTS:
    def __init__(
        self,
        *,
        client: OpenAIHTTPClient,
        model: str = "gpt-4o-mini-tts",
        voice: str = "marin",
        speed: float = 1.0,
        instructions: str | None = None,
    ) -> None:
        if not 0.25 <= speed <= 4.0:
            raise ValueError("speed must be between 0.25 and 4.0")
        self.client = client
        self.model = model
        self.voice = voice
        self.speed = speed
        self.instructions = instructions or (
            "Speak in natural Vietnamese as a calm cinematic storyteller. "
            "Use clear diction, restrained drama, and brief pauses at sentence boundaries."
        )

    def synthesize(self, text: str, output: Path) -> Path:
        clean = " ".join(text.split()).strip()
        if not clean:
            raise ValueError("text must not be empty")
        audio = self.client.post_bytes(
            "/audio/speech",
            {
                "model": self.model,
                "voice": self.voice,
                "input": clean,
                "instructions": self.instructions,
                "response_format": "wav",
                "speed": self.speed,
            },
        )
        if len(audio) < 16:
            raise ProviderResponseError("speech provider returned empty audio")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(audio)
        return output
