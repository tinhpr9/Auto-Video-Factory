"""
Data models, enums, and schemas for Flow Provider Foundation.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class FlowFailureClass(str, Enum):
    """Deterministic failure classification for Flow automation."""
    AUTH = "AUTH"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    USER_INTERACTION_REQUIRED = "USER_INTERACTION_REQUIRED"
    SELECTOR_CHANGED = "SELECTOR_CHANGED"
    UI_CHANGED = "UI_CHANGED"
    NETWORK = "NETWORK"
    RATE_LIMIT = "RATE_LIMIT"
    QUOTA = "QUOTA"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    UNKNOWN = "UNKNOWN"


class FlowJobStatus(str, Enum):
    """Lifecycle status of a Flow generation job."""
    QUEUED = "QUEUED"
    RETRY_WAIT = "RETRY_WAIT"
    SUBMITTED = "SUBMITTED"
    PENDING = "PENDING"
    GENERATING = "GENERATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    USER_INTERACTION_REQUIRED = "USER_INTERACTION_REQUIRED"
    SUBMISSION_AMBIGUOUS = "SUBMISSION_AMBIGUOUS"
    CANCELLED = "CANCELLED"


class FlowAspectRatio(str, Enum):
    """Aspect ratios supported by Google Flow."""
    PORTRAIT_9_16 = "9:16"
    LANDSCAPE_16_9 = "16:9"
    SQUARE_1_1 = "1:1"


class FlowModel(str, Enum):
    """Supported Google Flow models."""
    OMNI_FLASH = "omni_flash"
    VEO_3_1_FAST = "veo_3_1_t2v_fast"
    VEO_3_1_QUALITY = "veo_3_1_t2v"
    VEO_2_1_FAST = "veo_2_1_fast_d_15_t2v"


FLOW_MODE_TO_MODEL: dict[str, FlowModel] = {
    # Pre-existing Veo mode routes — preserved (do NOT change)
    "flow_balanced": FlowModel.VEO_3_1_FAST,
    "flow_economy": FlowModel.VEO_2_1_FAST,
    "flow_quality": FlowModel.VEO_3_1_QUALITY,
    # Phone one-tap direct key: Omni Flash (7 credits, 4s, fast)
    "omni_flash": FlowModel.OMNI_FLASH,
}

FLOW_MODEL_MAP: dict[str, FlowModel] = {
    "omni-flash": FlowModel.OMNI_FLASH,
    "omni_flash": FlowModel.OMNI_FLASH,
    "Omni Flash": FlowModel.OMNI_FLASH,
    "gemini-omni-flash": FlowModel.OMNI_FLASH,
    "veo-3.1-fast": FlowModel.VEO_3_1_FAST,
    "veo-3.1-quality": FlowModel.VEO_3_1_QUALITY,
    "veo-2.1-fast": FlowModel.VEO_2_1_FAST,
    FlowModel.OMNI_FLASH.value: FlowModel.OMNI_FLASH,
    FlowModel.VEO_3_1_FAST.value: FlowModel.VEO_3_1_FAST,
    FlowModel.VEO_3_1_QUALITY.value: FlowModel.VEO_3_1_QUALITY,
    FlowModel.VEO_2_1_FAST.value: FlowModel.VEO_2_1_FAST,
}


def resolve_flow_model(model: FlowModel | str) -> FlowModel:
    """
    Authoritatively resolve and validate a FlowModel from an enum, value, member name, or alias.
    Fails closed (raises ValueError) if model cannot be deterministically resolved.
    """
    if isinstance(model, FlowModel):
        return model
    if isinstance(model, str):
        # 1. Check exact enum value
        try:
            return FlowModel(model)
        except ValueError:
            pass

        # 2. Check FLOW_MODEL_MAP aliases (e.g. 'omni-flash', 'veo-3.1-fast')
        if model in FLOW_MODEL_MAP:
            return FLOW_MODEL_MAP[model]

        # 3. Check FLOW_MODE_TO_MODEL modes (e.g. 'flow_balanced', 'flow_economy')
        if model in FLOW_MODE_TO_MODEL:
            return FLOW_MODE_TO_MODEL[model]

        # 4. Check enum member names (e.g. 'OMNI_FLASH', 'VEO_3_1_FAST')
        upper = model.strip().upper()
        if upper in FlowModel.__members__:
            return FlowModel[upper]

        raise ValueError(
            f"Invalid or unsupported Flow model: {model!r}. "
            f"Must be a valid FlowModel enum, value, member name, or supported alias in {list(FLOW_MODEL_MAP.keys())}."
        )
    raise ValueError(f"Invalid model type: {type(model).__name__}. Expected FlowModel or str.")



@dataclass
class FlowModelInfo:
    """Metadata describing a Flow model and its resource cost."""
    model_id: str
    name: str
    capabilities: list[str]
    cost_credits: int
    default_aspect_ratio: FlowAspectRatio = FlowAspectRatio.PORTRAIT_9_16


@dataclass
class FlowCredits:
    """Credit balance state from Google Flow."""
    total_credits: int
    available_credits: int
    consumed_credits: int
    timestamp: float = field(default_factory=time.time)


@dataclass
class FlowHealthStatus:
    """Operational health status of the provider."""
    healthy: bool
    authenticated: bool
    profile_exists: bool
    browser_ready: bool
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class FlowGenerationRequest:
    """Request payload for generating video via FlowProvider."""
    job_id: str
    prompt: str
    model: FlowModel = FlowModel.VEO_3_1_FAST
    aspect_ratio: FlowAspectRatio = FlowAspectRatio.PORTRAIT_9_16
    count: int = 1
    output_dir: Path = field(default_factory=lambda: Path("./output"))
    start_image_path: Optional[Path] = None
    priority: int = 5  # Higher number = higher priority
    client_request_id: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    prompt_hash: str = field(init=False)

    def __post_init__(self):
        self.output_dir = Path(self.output_dir)
        if self.start_image_path:
            self.start_image_path = Path(self.start_image_path)
        if not self.client_request_id:
            self.client_request_id = self.job_id

        self.model = resolve_flow_model(self.model)
        if isinstance(self.aspect_ratio, str):
            if self.aspect_ratio in ("9:16", "PORTRAIT", "portrait"):
                self.aspect_ratio = FlowAspectRatio.PORTRAIT_9_16
            elif self.aspect_ratio in ("16:9", "LANDSCAPE", "landscape"):
                self.aspect_ratio = FlowAspectRatio.LANDSCAPE_16_9
            elif self.aspect_ratio in ("1:1", "SQUARE", "square"):
                self.aspect_ratio = FlowAspectRatio.SQUARE_1_1
            else:
                self.aspect_ratio = FlowAspectRatio(self.aspect_ratio)

        # Compute deterministic prompt hash including all generation-affecting fields
        normalized_prompt = self.prompt.strip()
        hasher = hashlib.sha256()
        hasher.update(normalized_prompt.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(self.model.value.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(self.aspect_ratio.value.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(str(self.count).encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(str(self.start_image_path or "").encode("utf-8"))
        self.prompt_hash = hasher.hexdigest()


@dataclass
class FlowJobResult:
    """Result payload returned by FlowProvider operations."""
    job_id: str
    provider_job_id: str
    status: FlowJobStatus
    output_files: list[Path] = field(default_factory=list)
    credit_before: int = 0
    credit_after: int = 0
    failure_class: Optional[FlowFailureClass] = None
    failure_message: Optional[str] = None
    runtime_evidence: dict[str, Any] = field(default_factory=dict)
    elapsed_seconds: float = 0.0


@dataclass
class FlowJobRecord:
    """Persistent job record for durable store and crash recovery."""
    job_id: str
    prompt: str
    prompt_hash: str
    provider: str
    model: str
    aspect_ratio: str
    priority: int
    count: int = 1
    start_image_path: Optional[Path] = None
    client_request_id: Optional[str] = None
    provider_job_id: Optional[str] = None
    project_id: Optional[str] = None
    status: FlowJobStatus = FlowJobStatus.QUEUED
    attempt_count: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    output_paths: list[Path] = field(default_factory=list)
    failure_class: Optional[FlowFailureClass] = None
    failure_message: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_request(cls, req: FlowGenerationRequest, provider: str = "flow") -> FlowJobRecord:
        return cls(
            job_id=req.job_id,
            prompt=req.prompt,
            prompt_hash=req.prompt_hash,
            provider=provider,
            model=req.model.value,
            aspect_ratio=req.aspect_ratio.value,
            priority=req.priority,
            count=req.count,
            start_image_path=req.start_image_path,
            client_request_id=req.client_request_id or req.job_id,
            created_at=req.created_at,
            updated_at=time.time(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "prompt": self.prompt,
            "prompt_hash": self.prompt_hash,
            "provider": self.provider,
            "model": self.model,
            "aspect_ratio": self.aspect_ratio,
            "priority": self.priority,
            "count": self.count,
            "start_image_path": str(self.start_image_path) if self.start_image_path else None,
            "client_request_id": self.client_request_id,
            "provider_job_id": self.provider_job_id,
            "project_id": self.project_id,
            "status": self.status.value,
            "attempt_count": self.attempt_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "output_paths": [str(p) for p in self.output_paths],
            "failure_class": self.failure_class.value if self.failure_class else None,
            "failure_message": self.failure_message,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FlowJobRecord:
        return cls(
            job_id=data["job_id"],
            prompt=data["prompt"],
            prompt_hash=data["prompt_hash"],
            provider=data["provider"],
            model=data["model"],
            aspect_ratio=data["aspect_ratio"],
            priority=data.get("priority", 5),
            count=data.get("count", 1),
            start_image_path=Path(data["start_image_path"]) if data.get("start_image_path") else None,
            client_request_id=data.get("client_request_id"),
            provider_job_id=data.get("provider_job_id"),
            project_id=data.get("project_id"),
            status=FlowJobStatus(data.get("status", "QUEUED")),
            attempt_count=data.get("attempt_count", 0),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            output_paths=[Path(p) for p in data.get("output_paths", [])],
            failure_class=FlowFailureClass(data["failure_class"]) if data.get("failure_class") else None,
            failure_message=data.get("failure_message"),
            metadata=data.get("metadata", {}),
        )
