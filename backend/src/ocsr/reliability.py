"""Failure taxonomy and one-retry isolation policy for OCSR backends."""

from __future__ import annotations

import gc
import re
from dataclasses import dataclass
from typing import Any, Callable, Literal

from .base import OCSRResult
from .decimer_adapter import DECIMERAdapter
from .molscribe_adapter import MolScribeAdapter


FailureCategory = Literal[
    "model_inference_failure", "subprocess_failure", "timeout", "cuda_failure",
    "image_decode_failure", "input_preprocessing_failure", "output_parse_failure",
    "dependency_failure", "unknown_backend_failure",
]

RETRIABLE_FAILURES: frozenset[str] = frozenset({
    "model_inference_failure", "subprocess_failure", "timeout", "cuda_failure",
    "unknown_backend_failure",
})


@dataclass(frozen=True)
class BackendReliabilityConfig:
    molscribe_timeout_seconds: float = 180.0
    decimer_timeout_seconds: float = 300.0
    maximum_retries: int = 1


DEFAULT_BACKEND_RELIABILITY_CONFIG = BackendReliabilityConfig()


def sanitize_exception_summary(value: object, limit: int = 500) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    text = re.sub(r"(?:[A-Za-z]:)?[/\\](?:[^\s:/\\]+[/\\])+[^\s]+", "<path>", text)
    return text[:limit]


def classify_backend_failure(result: OCSRResult | None = None, exception: BaseException | None = None) -> FailureCategory:
    if result is not None and result.failure_category in {
        "model_inference_failure", "subprocess_failure", "timeout", "cuda_failure",
        "image_decode_failure", "input_preprocessing_failure", "output_parse_failure",
        "dependency_failure", "unknown_backend_failure",
    }:
        return result.failure_category  # type: ignore[return-value]
    text = " ".join(filter(None, [
        type(exception).__name__ if exception else "",
        str(exception or ""),
        str(result.message if result else ""),
    ])).lower()
    if result is not None and result.status != "success" and (
        result.raw_output
        or any(marker in text for marker in ("unparsable", "cannot parse", "could not parse", "invalid smiles", "无法解析"))
    ):
        return "output_parse_failure"
    if any(marker in text for marker in ("timeout", "timed out", "超时")):
        return "timeout"
    if any(marker in text for marker in ("cuda", "cudnn", "cublas", "gpu", "out of memory", "oom")):
        return "cuda_failure"
    if any(marker in text for marker in ("no such file", "cannot identify image", "decode", "truncated image")):
        return "image_decode_failure"
    if any(marker in text for marker in ("preprocess", "normalize", "unsupported image", "array dimensions")):
        return "input_preprocessing_failure"
    if any(marker in text for marker in ("dependency", "modulenotfound", "importerror", "not installed")):
        return "dependency_failure"
    if any(marker in text for marker in ("subprocess", "child process", "return code", "worker", "子进程")):
        return "subprocess_failure"
    if any(marker in text for marker in ("inference", "predict", "model", "推理")):
        return "model_inference_failure"
    return "unknown_backend_failure"


def _isolated_retry(
    backend: str,
    image: Any,
    config: BackendReliabilityConfig = DEFAULT_BACKEND_RELIABILITY_CONFIG,
) -> OCSRResult:
    if backend == "molscribe":
        return MolScribeAdapter(
            isolated_subprocess=True, timeout_seconds=config.molscribe_timeout_seconds,
        ).recognize(image)
    if backend == "decimer":
        return DECIMERAdapter(
            isolated_subprocess=True, timeout_seconds=config.decimer_timeout_seconds,
        ).recognize(image)
    raise ValueError(f"Reliability retry supports molscribe/decimer, not {backend}")


def cleanup_after_failure(category: FailureCategory) -> None:
    """Release safe caches; isolated retry owns and tears down the rebuilt model process."""
    gc.collect()
    if category in {"timeout", "cuda_failure", "model_inference_failure"}:
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.synchronize()
                torch.cuda.empty_cache()
        except Exception:
            pass


def run_with_single_retry(
    backend: str,
    image: Any,
    primary: Callable[[Any], OCSRResult],
    retry: Callable[[str, Any], OCSRResult] | None = None,
    config: BackendReliabilityConfig = DEFAULT_BACKEND_RELIABILITY_CONFIG,
) -> OCSRResult:
    """Run once, retry one failure in a fresh model process, and retain both attempts."""
    try:
        first = primary(image)
        first_exception: BaseException | None = None
    except BaseException as exc:  # benchmark must retain a row for every input
        first_exception = exc
        first = OCSRResult(None, None, backend, "failed", sanitize_exception_summary(exc))
    first_category = classify_backend_failure(first, first_exception)
    first_payload = {
        "status": first.status, "message": sanitize_exception_summary(first.message),
        "failure_category": first_category if first.status != "success" else "",
        "exception_type": type(first_exception).__name__ if first_exception else "",
        "raw_output": str(first.raw_output or "")[:2000],
        "inference_time_ms": first.inference_time_ms,
    }
    if first.status == "success" and first.smiles:
        first.failure_category = None
        first.attempt_count = 1
        first.first_attempt = first_payload
        first.retry_attempt = None
        return first
    # A returned but unparsable structure string is a deterministic model
    # output, not evidence that the process is contaminated. Do not waste a
    # rebuilt GPU subprocess retry on this class of failure.
    if first_category not in RETRIABLE_FAILURES:
        first.failure_category = first_category
        first.exception_type = type(first_exception).__name__ if first_exception else None
        first.exception_summary = sanitize_exception_summary(first_exception or first.message)
        first.attempt_count = 1
        first.first_attempt = first_payload
        first.retry_attempt = None
        return first
    if config.maximum_retries < 1:
        first.failure_category = first_category
        first.attempt_count = 1
        first.first_attempt = first_payload
        return first
    cleanup_after_failure(first_category)
    retry_function = retry or (lambda retry_backend, retry_image: _isolated_retry(retry_backend, retry_image, config))
    try:
        second = retry_function(backend, image)
        second_exception: BaseException | None = None
    except BaseException as exc:
        second_exception = exc
        second = OCSRResult(None, None, backend, "failed", sanitize_exception_summary(exc))
    second_category = classify_backend_failure(second, second_exception)
    second.failure_category = second_category if second.status != "success" else None
    second.exception_type = type(second_exception).__name__ if second_exception else None
    second.exception_summary = sanitize_exception_summary(second_exception or second.message)
    second.attempt_count = 2
    second.first_attempt = first_payload
    second.retry_attempt = {
        "status": second.status, "message": sanitize_exception_summary(second.message),
        "failure_category": second.failure_category or "", "exception_type": second.exception_type or "",
        "raw_output": str(second.raw_output or "")[:2000],
        "inference_time_ms": second.inference_time_ms,
    }
    return second
