"""
Abstract FlowProvider contract definition.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from .models import (
    FlowCredits,
    FlowGenerationRequest,
    FlowHealthStatus,
    FlowJobResult,
    FlowModelInfo,
)


class FlowProvider(ABC):
    """
    Abstract interface for Google Flow video generation providers.
    Both live browser automation and mock/test providers implement this contract.
    """

    @abstractmethod
    def health(self) -> FlowHealthStatus:
        """Check provider operational status and browser/session availability."""
        pass

    @abstractmethod
    def get_credits(self) -> FlowCredits:
        """Fetch current available and consumed credits balance."""
        pass

    @abstractmethod
    def get_models(self) -> list[FlowModelInfo]:
        """List supported Veo models, costs, and capabilities."""
        pass

    @abstractmethod
    def generate_video(self, request: FlowGenerationRequest) -> FlowJobResult:
        """
        Submit a text-to-video or image-to-video generation request.
        Returns a FlowJobResult with status (e.g. SUBMITTED or PENDING).
        """
        pass

    @abstractmethod
    def poll(self, provider_job_id: str) -> FlowJobResult:
        """Poll the generation status of an asynchronous Flow video job."""
        pass

    @abstractmethod
    def download(self, provider_job_id: str, output_dir: Path) -> list[Path]:
        """Download rendered media files for a completed video job."""
        pass

    def reconcile_by_client_id(self, client_request_id: str) -> Optional[FlowJobResult]:
        """
        Attempt to look up an in-flight or completed provider job by client request ID.
        Default implementation returns None if upstream does not support reconciliation.
        """
        return None
