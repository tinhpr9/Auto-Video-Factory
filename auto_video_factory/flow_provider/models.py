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
    UNKNOWN = "UNKNOWN"


class FlowJobStatus(str, Enum):
    """Lifecycle status of a Flow generation job."""
    QUEUED = "QUEUED"
    SUBMITTED = "SUBMITTED"
    PENDING = "PENDING"
    GENERATING = "GENERATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    USER_INTERACTION_REQUIRED = "USER_INTERACTION_REQUIRED"
    CANCELLED = "CANCELLED"


class FlowAspectRatio(str, Enum):
    """Aspect ratios supported by Google Flow."""
    PORTRAIT_9_16 = "9:16"
    LANDSCAPE_16_9 = "16:9"
    SQUARE_1_1 = "1:1"


class FlowModel(str, Enum):
    """Supported Google Flow Veo models."""
    VEO_3_1_FAST = "veo_3_1_t2v_fast"
    VEO_3_1_QUALITY = "veo_3_1_t2v"
    VEO_2_1_FAST = "veo_2_1_fast_d_15_t2v"


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
    created_at: float = field(default_factory=time.time)
    prompt_hash: str = field(init=False)

    def __post_init__(self):
        self.output_dir = Path(self.output_dir)
        if self.start_image_path:
            self.start_image_path = Path(self.start_image_path)
        # Compute deterministic prompt hash
        normalized_prompt = self.prompt.strip()
        hasher = hashlib.sha256()
        hasher.update(normalized_prompt.encode("utf-8"))
        hasher.update(self.model.value.encode("utf-8"))
        hasher.update(self.aspect_ratio.value.encode("utf-8"))
        if self.start_image_path:
            hasher.update(str(self.start_image_path).encode("utf-8"))
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
    provider_job_id: Optional[str] = None
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
            "provider_job_id": self.provider_job_id,
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
            provider_job_id=data.get("provider_job_id"),
            status=FlowJobStatus(data.get("status", "QUEUED")),
            attempt_count=data.get("attempt_count", 0),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            output_paths=[Path(p) for p in data.get("output_paths", [])],
            failure_class=FlowFailureClass(data["failure_class"]) if data.get("failure_class") else None,
            failure_message=data.get("failure_message"),
            metadata=data.get("metadata", {}),
        )
