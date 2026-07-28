"""Common interfaces for interchangeable OCSR backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any, Literal


@dataclass
class OCSRResult:
    """Normalized output returned by every OCSR adapter."""

    smiles: str | None
    confidence: float | None
    backend: str
    status: Literal["success", "failed"]
    message: str
    inference_time_ms: float | None = None
    model_name: str | None = None
    model_version: str | None = None
    model_sha256: str | None = None
    device: str | None = None
    package_version: str | None = None
    git_commit: str | None = None
    dependency_versions: dict[str, str | None] | None = None
    result_origin: str | None = None
    candidates: list[dict[str, Any]] | None = None
    consensus: dict[str, Any] | None = None
    similarity_analysis: list[dict[str, Any]] | None = None
    decision: Literal["accepted", "accepted_with_warning", "review_needed", "rejected"] | None = None
    risk_level: Literal["low", "medium", "high"] | None = None
    manual_review_recommended: bool | None = None
    raw_output: str | None = None
    failure_category: str | None = None
    exception_type: str | None = None
    exception_summary: str | None = None
    attempt_count: int = 1
    first_attempt: dict[str, Any] | None = None
    retry_attempt: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert this result to a JSON-serializable dictionary."""
        return asdict(self)


class BaseOCSRAdapter(ABC):
    """Abstract interface implemented by all recognition adapters."""

    backend_name = "base"
    preferred_image_stage = "preprocessed"

    @property
    def is_available(self) -> bool:
        """Return whether this backend is ready for inference."""
        return True

    @property
    def availability_message(self) -> str:
        """Return a short human-readable backend readiness message."""
        return f"{self.backend_name} 后端可用。"

    def status(self) -> dict[str, Any]:
        """Return JSON-friendly backend availability information."""
        return {
            "backend": self.backend_name,
            "available": self.is_available,
            "message": self.availability_message,
            "model_name": None,
            "model_version": None,
            "device": None,
            "package_version": None,
            "last_inference_time_ms": None,
        }

    @abstractmethod
    def recognize(self, image_path_or_array: Any) -> OCSRResult:
        """Recognize a molecular image and return an OCSRResult."""
        raise NotImplementedError
