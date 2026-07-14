from __future__ import annotations

import email.utils
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, TypeVar

from backend.core.config import AppSettings

T = TypeVar("T")


class RequestPolicyError(Exception):
    """Base error for exhausted or non-retryable upstream requests."""


class NetworkTimeoutError(RequestPolicyError):
    pass


class RateLimitError(RequestPolicyError):
    pass


class UpstreamError(RequestPolicyError):
    pass


@dataclass(frozen=True)
class RequestPolicy:
    total_budget: float
    max_retries: int
    backoff_base: float
    backoff_max: float

    @classmethod
    def from_settings(cls, settings: AppSettings) -> "RequestPolicy":
        return cls(settings.request_total_budget, settings.request_max_retries,
                   settings.request_backoff_base, settings.request_backoff_max)

    def call(self, operation: Callable[[], T]) -> T:
        started = time.monotonic()
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return operation()
            except Exception as exc:
                last = exc
                retryable, kind = classify_exception(exc)
                if not retryable or attempt >= self.max_retries:
                    raise kind(safe_upstream_message(kind)) from exc
                delay = retry_after_seconds(exc)
                if delay is None:
                    delay = min(self.backoff_max, self.backoff_base * (2**attempt))
                    delay += random.uniform(0, min(0.25, delay * 0.1))
                remaining = self.total_budget - (time.monotonic() - started)
                if remaining <= 0 or delay >= remaining:
                    raise kind(safe_upstream_message(kind)) from exc
                time.sleep(delay)
        raise UpstreamError("Upstream service request failed.") from last


def classify_exception(exc: Exception) -> tuple[bool, type[RequestPolicyError]]:
    response = getattr(exc, "response", None)
    status = (
        getattr(exc, "status_code", None)
        or getattr(exc, "code", None)
        or getattr(response, "status_code", None)
        or getattr(response, "status", None)
    )
    name = type(exc).__name__.lower()
    if status == 429:
        return True, RateLimitError
    if isinstance(status, int):
        if status >= 500:
            return True, UpstreamError
        return False, UpstreamError
    reason_name = type(getattr(exc, "reason", None)).__name__.lower()
    combined_name = f"{name} {reason_name}"
    if "timeout" in combined_name:
        return True, NetworkTimeoutError
    if any(token in combined_name for token in (
        "connection", "connecterror", "networkerror", "gaierror", "sslerror"
    )):
        return True, UpstreamError
    return False, UpstreamError


def retry_after_seconds(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or getattr(exc, "headers", None)
    value = headers.get("retry-after") if headers else None
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            parsed = email.utils.parsedate_to_datetime(str(value))
            return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def safe_upstream_message(kind: type[RequestPolicyError]) -> str:
    if kind is NetworkTimeoutError:
        return "The upstream service timed out. Please try again."
    if kind is RateLimitError:
        return "The upstream service is busy. Please try again later."
    return "The upstream service request failed."
