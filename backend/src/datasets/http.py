"""Retrying, cache-aware HTTP helper with an injectable requests session."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from time import sleep, monotonic
from typing import Any, Protocol

import requests

from src.utils.file_utils import ensure_directory


class HttpResponse(Protocol):
    content: bytes
    status_code: int
    headers: dict[str, str]

    def raise_for_status(self) -> None: ...


class HttpSession(Protocol):
    def get(self, url: str, *, timeout: float, headers: dict[str, str] | None = None) -> HttpResponse: ...


class CachedHttpClient:
    """Fetch public resources slowly, retry safely and retain response provenance."""

    def __init__(
        self,
        cache_dir: str | Path,
        *,
        request_interval: float = 0.34,
        retries: int = 3,
        timeout: float = 30.0,
        session: HttpSession | None = None,
        sleep_fn: Any = sleep,
        clock: Any = monotonic,
    ) -> None:
        self.cache_dir = ensure_directory(Path(cache_dir).expanduser().resolve())
        self.request_interval = max(0.0, float(request_interval))
        self.retries = max(0, int(retries))
        self.timeout = float(timeout)
        self.session = session or requests.Session()
        self._sleep = sleep_fn
        self._clock = clock
        self._last_request_at: float | None = None

    def get_bytes(self, url: str, *, use_cache: bool = True) -> tuple[bytes, dict[str, Any]]:
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        body_path = self.cache_dir / f"{key}.bin"
        metadata_path = self.cache_dir / f"{key}.json"
        if use_cache and body_path.is_file() and metadata_path.is_file():
            return body_path.read_bytes(), json.loads(metadata_path.read_text(encoding="utf-8"))

        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            self._wait_for_interval()
            try:
                response = self.session.get(url, timeout=self.timeout, headers={"User-Agent": "auditable-ocsr-dataset/1.0"})
                response.raise_for_status()
                payload = bytes(response.content)
                metadata = {
                    "url": url,
                    "status_code": int(response.status_code),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "headers": dict(response.headers or {}),
                    "attempt": attempt + 1,
                }
                body_path.write_bytes(payload)
                metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
                return payload, metadata
            except Exception as exc:  # Requests and fake sessions share this path.
                last_error = exc
                if attempt < self.retries:
                    self._sleep(min(2.0, 0.25 * (2**attempt)))
        raise RuntimeError(f"Failed to download {url}: {last_error}") from last_error

    def _wait_for_interval(self) -> None:
        now = float(self._clock())
        if self._last_request_at is not None:
            remaining = self.request_interval - (now - self._last_request_at)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_at = float(self._clock())
