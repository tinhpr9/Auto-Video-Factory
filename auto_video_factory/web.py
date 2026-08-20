from __future__ import annotations

import hmac
import logging
import os
import secrets
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Callable, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field, field_validator

from .media import CardImageProvider, EspeakTTS, FFmpegRenderer
from .openai_providers import OpenAIHTTPClient, OpenAIImageProvider, OpenAIStoryPlanner, OpenAITTS
from .pipeline import VideoFactory
from .story import TemplateStoryPlanner

logger = logging.getLogger("auto_video_factory.web")

from .presets import DURATION_TO_SCENES, STYLE_OPTIONS, VOICE_OPTIONS



@dataclass(frozen=True)
class WebSettings:
    provider: Literal["offline", "openai", "flow"] = "offline"
    output_root: Path = Path("output/web")
    max_workers: int = 1
    host: str = "0.0.0.0"
    port: int = 8000
    access_code: str | None = None
    session_ttl_seconds: int = 12 * 60 * 60
    auth_attempts_per_minute: int = 5
    max_jobs_per_hour: int = 12
    flow_mock: bool = False

    @classmethod
    def from_env(cls) -> "WebSettings":
        provider = os.getenv("AVF_PROVIDER", "offline").strip().lower()
        if provider not in {"offline", "openai", "flow"}:
            raise ValueError("AVF_PROVIDER must be offline, openai, or flow")
        max_workers = int(os.getenv("AVF_MAX_WORKERS", "1"))
        if not 1 <= max_workers <= 4:
            raise ValueError("AVF_MAX_WORKERS must be between 1 and 4")
        port = int(os.getenv("AVF_PORT") or os.getenv("PORT") or "8000")
        if not 1 <= port <= 65535:
            raise ValueError("AVF_PORT/PORT must be between 1 and 65535")
        access_code = os.getenv("AVF_ACCESS_CODE")
        if access_code is not None:
            access_code = access_code.strip()
            if len(access_code) < 12:
                raise ValueError("AVF_ACCESS_CODE must be at least 12 characters")
        flow_mock = os.getenv("AVF_FLOW_MOCK", "").strip().lower() in ("1", "true", "yes")
        if (provider == "openai" or (provider == "flow" and not flow_mock)) and not access_code:
            raise ValueError(f"AVF_ACCESS_CODE is required when AVF_PROVIDER={provider}")
        session_ttl_seconds = int(os.getenv("AVF_SESSION_TTL_SECONDS", str(12 * 60 * 60)))
        auth_attempts_per_minute = int(os.getenv("AVF_AUTH_ATTEMPTS_PER_MINUTE", "5"))
        max_jobs_per_hour = int(os.getenv("AVF_MAX_JOBS_PER_HOUR", "12"))
        if not 300 <= session_ttl_seconds <= 7 * 24 * 60 * 60:
            raise ValueError("AVF_SESSION_TTL_SECONDS must be between 300 and 604800")
        if not 1 <= auth_attempts_per_minute <= 30:
            raise ValueError("AVF_AUTH_ATTEMPTS_PER_MINUTE must be between 1 and 30")
        if not 1 <= max_jobs_per_hour <= 100:
            raise ValueError("AVF_MAX_JOBS_PER_HOUR must be between 1 and 100")
        return cls(
            provider=provider,
            output_root=Path(os.getenv("AVF_OUTPUT_ROOT", "output/web")),
            max_workers=max_workers,
            host=os.getenv("AVF_HOST", "0.0.0.0"),
            port=port,
            access_code=access_code,
            session_ttl_seconds=session_ttl_seconds,
            auth_attempts_per_minute=auth_attempts_per_minute,
            max_jobs_per_hour=max_jobs_per_hour,
            flow_mock=flow_mock,
        )




class AccessCodeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=256)


class SessionCreated(BaseModel):
    token: str
    expires_in_seconds: int


class SessionStatus(BaseModel):
    auth_required: bool
    authenticated: bool


@dataclass
class _SessionRecord:
    expires_at: float
    job_timestamps: deque[float]


class SessionManager:
    def __init__(self, settings: WebSettings) -> None:
        self.settings = settings
        self._sessions: dict[str, _SessionRecord] = {}
        self._failed_logins: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    @property
    def auth_required(self) -> bool:
        return bool(self.settings.access_code)

    def _purge_attempts(self, client_key: str, now: float) -> deque[float]:
        attempts = self._failed_logins.setdefault(client_key, deque())
        while attempts and now - attempts[0] >= 60:
            attempts.popleft()
        return attempts

    def login(self, code: str, client_key: str) -> str:
        now = time.monotonic()
        with self._lock:
            attempts = self._purge_attempts(client_key, now)
            if len(attempts) >= self.settings.auth_attempts_per_minute:
                raise HTTPException(status_code=429, detail="Too many login attempts")
            expected = self.settings.access_code
            if expected is not None and not hmac.compare_digest(code, expected):
                attempts.append(now)
                raise HTTPException(status_code=401, detail="Invalid access code")
            attempts.clear()
            token = secrets.token_urlsafe(32)
            self._sessions[token] = _SessionRecord(
                expires_at=now + self.settings.session_ttl_seconds,
                job_timestamps=deque(),
            )
            return token

    def validate(self, token: str | None) -> str | None:
        if not self.auth_required:
            return "public"
        if not token:
            return None
        now = time.monotonic()
        with self._lock:
            record = self._sessions.get(token)
            if record is None:
                return None
            if record.expires_at <= now:
                self._sessions.pop(token, None)
                return None
            return token

    def logout(self, token: str | None) -> None:
        if not token or token == "public":
            return
        with self._lock:
            self._sessions.pop(token, None)

    def consume_job(self, token: str) -> None:
        if token == "public":
            return
        now = time.monotonic()
        with self._lock:
            record = self._sessions.get(token)
            if record is None or record.expires_at <= now:
                self._sessions.pop(token, None)
                raise HTTPException(status_code=401, detail="Authentication required")
            while record.job_timestamps and now - record.job_timestamps[0] >= 60 * 60:
                record.job_timestamps.popleft()
            if len(record.job_timestamps) >= self.settings.max_jobs_per_hour:
                raise HTTPException(status_code=429, detail="Generation quota reached")
            record.job_timestamps.append(now)


class WebJobRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=600)
    duration_seconds: Literal[45, 60, 90] = 60
    voice: Literal["marin", "onyx"] = "marin"
    style: Literal["xianxia-cinematic", "ink-wash", "dark-fantasy"] = "xianxia-cinematic"

    @field_validator("topic")
    @classmethod
    def normalize_topic(cls, value: str) -> str:
        clean = " ".join(value.split()).strip()
        if not clean:
            raise ValueError("topic must not be empty")
        return clean


class JobCreated(BaseModel):
    job_id: str
    status: Literal["queued", "running", "completed", "failed"]
    status_url: str


class JobStatus(BaseModel):
    job_id: str
    status: Literal["queued", "running", "completed", "failed"]
    progress: int = Field(ge=0, le=100)
    message: str
    title: str | None = None
    video_url: str | None = None
    error_code: str | None = None


class ConfigOption(BaseModel):
    id: str
    label: str


class WebConfig(BaseModel):
    provider: Literal["offline", "openai", "flow"]
    durations: list[int]
    voices: list[ConfigOption]
    styles: list[ConfigOption]


@dataclass
class _JobRecord:
    request: WebJobRequest
    status: str = "queued"
    progress: int = 0
    message: str = "Đang xếp hàng"
    title: str | None = None
    video: Path | None = None
    error_code: str | None = None


FactoryBuilder = Callable[[WebJobRequest], VideoFactory]


def build_factory_for_request(
    request: WebJobRequest,
    settings: WebSettings,
    controller: Optional["FlowController"] = None,
) -> VideoFactory:
    scenes = DURATION_TO_SCENES[request.duration_seconds]
    renderer = FFmpegRenderer(width=720, height=1280, fps=30)
    if settings.provider == "offline":
        return VideoFactory(
            planner=TemplateStoryPlanner(scene_count=scenes),
            tts=EspeakTTS(language="vi", speed=175),
            image_provider=CardImageProvider(width=720, height=1280),
            renderer=renderer,
        )

    if settings.provider == "flow":
        from .flow_provider import (
            FlowAspectRatio,
            FlowController,
            FlowModel,
            FlowVisualProvider,
            MockFlowProvider,
            ProductionFlowProvider,
            FLOW_MODEL_MAP,
        )
        if controller is None:
            storage_path = settings.output_root / "flow_jobs.json"
            scenes_dir = settings.output_root / "scenes"
            if settings.flow_mock:
                prov = MockFlowProvider(initial_credits=200)
            else:
                session_token = os.getenv("FLOW_SESSION_TOKEN")
                profile_path = os.getenv("FLOW_BROWSER_PROFILE")
                prov = ProductionFlowProvider(
                    auth_token=session_token,
                    profile_path=Path(profile_path) if profile_path else None,
                )
            controller = FlowController(
                provider=prov,
                storage_path=storage_path,
                output_dir=scenes_dir,
            )
        flow_mode = os.getenv("AVF_FLOW_MODE", "flow_balanced")
        flow_model_str = os.getenv("AVF_FLOW_MODEL")
        if flow_model_str:
            if flow_model_str not in FLOW_MODEL_MAP:
                raise ValueError(
                    f"Unknown AVF_FLOW_MODEL '{flow_model_str}'. Must be one of {list(FLOW_MODEL_MAP.keys())}"
                )
            explicit_model = FLOW_MODEL_MAP[flow_model_str]
        else:
            explicit_model = None
        visual_provider = FlowVisualProvider(
            controller=controller,
            model=explicit_model,
            aspect_ratio=FlowAspectRatio.PORTRAIT_9_16,
            flow_mode=flow_mode,
            width=720,
            height=1280,
        )
        return VideoFactory(
            planner=TemplateStoryPlanner(scene_count=scenes),
            tts=EspeakTTS(language="vi", speed=175),
            image_provider=visual_provider,
            renderer=renderer,
        )

    client = OpenAIHTTPClient()
    style_prompt = STYLE_OPTIONS[request.style]["prompt"]
    return VideoFactory(
        planner=OpenAIStoryPlanner(
            client=client,
            scene_count=scenes,
            model=os.getenv("AVF_SCRIPT_MODEL", "gpt-5-mini"),
            visual_style=style_prompt,
        ),
        tts=OpenAITTS(
            client=client,
            model=os.getenv("AVF_TTS_MODEL", "gpt-4o-mini-tts"),
            voice=request.voice,
            speed=1.0,
        ),
        image_provider=OpenAIImageProvider(
            client=client,
            width=720,
            height=1280,
            model=os.getenv("AVF_IMAGE_MODEL", "gpt-image-1-mini"),
            quality=os.getenv("AVF_IMAGE_QUALITY", "low"),
            style_prompt=style_prompt,
        ),
        renderer=renderer,
    )


class JobManager:
    def __init__(self, settings: WebSettings, factory_builder: FactoryBuilder) -> None:
        self.settings = settings
        self.factory_builder = factory_builder
        self._executor = ThreadPoolExecutor(max_workers=settings.max_workers, thread_name_prefix="avf-web")
        self._jobs: dict[str, _JobRecord] = {}
        self._lock = threading.Lock()
        settings.output_root.mkdir(parents=True, exist_ok=True)

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)

    def submit(self, request: WebJobRequest) -> tuple[str, str]:
        job_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[job_id] = _JobRecord(request=request)
        self._executor.submit(self._run, job_id)
        return job_id, self.get(job_id).status

    def _update(self, job_id: str, **changes: object) -> None:
        with self._lock:
            record = self._jobs[job_id]
            for key, value in changes.items():
                setattr(record, key, value)

    def _run(self, job_id: str) -> None:
        with self._lock:
            request = self._jobs[job_id].request
        self._update(job_id, status="running", progress=2, message="Đang bắt đầu")

        def progress(percent: int, message: str) -> None:
            with self._lock:
                record = self._jobs[job_id]
                if record.status in {"completed", "failed"}:
                    return
                record.progress = max(record.progress, min(99, int(percent)))
                record.message = message

        try:
            factory = self.factory_builder(request)
            result = factory.generate(
                request.topic,
                self.settings.output_root / job_id,
                on_progress=progress,
            )
        except Exception as exc:
            # Do not log raw exception text: third-party exceptions can embed
            # credentials or response bodies. Keep a safe type-only breadcrumb.
            logger.error("Video generation failed for job %s (%s)", job_id, exc.__class__.__name__)
            self._update(
                job_id,
                status="failed",
                message="Không thể tạo video. Hãy kiểm tra cấu hình server rồi thử lại.",
                error_code="GENERATION_FAILED",
            )
            return

        self._update(
            job_id,
            status="completed",
            progress=100,
            message="Hoàn tất",
            title=result.plan.title,
            video=result.video,
        )

    def get(self, job_id: str) -> _JobRecord:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                raise KeyError(job_id)
            return _JobRecord(**record.__dict__)


def _status_payload(job_id: str, record: _JobRecord) -> JobStatus:
    return JobStatus(
        job_id=job_id,
        status=record.status,  # type: ignore[arg-type]
        progress=record.progress,
        message=record.message,
        title=record.title,
        video_url=f"/api/jobs/{job_id}/video" if record.status == "completed" else None,
        error_code=record.error_code,
    )


def create_app(
    *,
    settings: WebSettings | None = None,
    factory_builder: FactoryBuilder | None = None,
) -> FastAPI:
    resolved = settings or WebSettings.from_env()
    if resolved.access_code is not None and len(resolved.access_code) < 12:
        raise ValueError("AVF_ACCESS_CODE must be at least 12 characters")
    if (resolved.provider == "openai" or (resolved.provider == "flow" and not resolved.flow_mock)) and not resolved.access_code:
        raise ValueError(f"AVF_ACCESS_CODE is required when AVF_PROVIDER={resolved.provider}")

    shared_flow_controller = None
    if resolved.provider == "flow":
        from .flow_provider import (
            FlowController,
            MockFlowProvider,
            ProductionFlowProvider,
        )
        storage_path = resolved.output_root / "flow_jobs.json"
        scenes_dir = resolved.output_root / "scenes"
        if resolved.flow_mock:
            prov = MockFlowProvider(initial_credits=200)
        else:
            session_token = os.getenv("FLOW_SESSION_TOKEN")
            profile_path = os.getenv("FLOW_BROWSER_PROFILE")
            prov = ProductionFlowProvider(
                auth_token=session_token,
                profile_path=Path(profile_path) if profile_path else None,
            )
        shared_flow_controller = FlowController(
            provider=prov,
            storage_path=storage_path,
            output_dir=scenes_dir,
        )

    builder = factory_builder or (lambda request: build_factory_for_request(request, resolved, controller=shared_flow_controller))
    manager = JobManager(resolved, builder)
    sessions = SessionManager(resolved)
    bearer_scheme = HTTPBearer(auto_error=False)

    def token_from_credentials(credentials: HTTPAuthorizationCredentials | None) -> str | None:
        if credentials is None or credentials.scheme.lower() != "bearer":
            return None
        return credentials.credentials

    def optional_session(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    ) -> str | None:
        return sessions.validate(token_from_credentials(credentials))

    def required_session(session_token: str | None = Depends(optional_session)) -> str:
        if session_token is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return session_token

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.job_manager = manager
        yield
        manager.close()

    app = FastAPI(
        title="Auto Video Factory Web API",
        version="0.3.1",
        description="Private mobile-first job API for generating narrated vertical videos.",
        lifespan=lifespan,
    )
    app.state.job_manager = manager

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home() -> HTMLResponse:
        html = files("auto_video_factory").joinpath("webui/index.html").read_text(encoding="utf-8")
        return HTMLResponse(html)

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/session", response_model=SessionStatus)
    def session_status(session_token: str | None = Depends(optional_session)) -> SessionStatus:
        return SessionStatus(
            auth_required=sessions.auth_required,
            authenticated=(session_token is not None),
        )

    @app.post("/api/session", response_model=SessionCreated, responses={401: {}, 429: {}})
    def create_session(payload: AccessCodeRequest, request: Request) -> SessionCreated:
        client_key = request.client.host if request.client else "unknown"
        token = sessions.login(payload.code, client_key)
        return SessionCreated(token=token, expires_in_seconds=resolved.session_ttl_seconds)

    @app.delete("/api/session", status_code=204)
    def delete_session(
        response: Response,
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
        session_token: str = Depends(required_session),
    ) -> Response:
        sessions.logout(token_from_credentials(credentials) or session_token)
        return Response(status_code=204)

    @app.get("/api/config", response_model=WebConfig)
    def config(_session: str = Depends(required_session)) -> WebConfig:
        return WebConfig(
            provider=resolved.provider,
            durations=list(DURATION_TO_SCENES),
            voices=[ConfigOption(id=key, label=value) for key, value in VOICE_OPTIONS.items()],
            styles=[
                ConfigOption(id=key, label=value["label"])
                for key, value in STYLE_OPTIONS.items()
            ],
        )

    @app.post("/api/jobs", response_model=JobCreated, status_code=status.HTTP_202_ACCEPTED)
    def create_job(request: WebJobRequest, session_token: str = Depends(required_session)) -> JobCreated:
        sessions.consume_job(session_token)
        job_id, state = manager.submit(request)
        return JobCreated(job_id=job_id, status=state, status_url=f"/api/jobs/{job_id}")

    @app.get("/api/jobs/{job_id}", response_model=JobStatus)
    def job_status(job_id: str, _session: str = Depends(required_session)) -> JobStatus:
        try:
            record = manager.get(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        return _status_payload(job_id, record)

    @app.get("/api/jobs/{job_id}/video", response_class=FileResponse)
    def job_video(job_id: str, _session: str = Depends(required_session)) -> FileResponse:
        try:
            record = manager.get(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        if record.status != "completed" or record.video is None:
            raise HTTPException(status_code=409, detail="Video is not ready")
        if not record.video.exists():
            raise HTTPException(status_code=410, detail="Video file is no longer available")
        return FileResponse(
            record.video,
            media_type="video/mp4",
        )

    return app


def main() -> None:
    import uvicorn

    settings = WebSettings.from_env()
    uvicorn.run(create_app(settings=settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
