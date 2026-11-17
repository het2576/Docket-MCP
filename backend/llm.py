"""The one place Docket talks to the Gemini API.

Every model call (extraction, embeddings, duplicate adjudication) goes through
`gemini_post`, so retries, timeouts and token accounting live here, and the
eval harness can wrap a single function to cache and meter all LLM traffic.
"""
from __future__ import annotations

import contextvars
import json
import random
import socket
import ssl
import time
import urllib.error
import urllib.request
from typing import Any

from backend.config import settings

try:
    import certifi

    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:  # system trust store; never skip verification
    SSL_CONTEXT = ssl.create_default_context()

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"

# Per-run telemetry. run_agent opens a meter; every call made inside it is recorded with the
# stage that made it, so each trace says what a run cost and where its time went.
_meter: contextvars.ContextVar[list[dict] | None] = contextvars.ContextVar("docket_llm_meter", default=None)
_stage: contextvars.ContextVar[str] = contextvars.ContextVar("docket_llm_stage", default="other")
# Monotonic time by which the current run must finish. Calls and retry waits never run past it,
# so a slow model yields a fallback result instead of the web request timing out with a 500.
_deadline: contextvars.ContextVar[float | None] = contextvars.ContextVar("docket_llm_deadline", default=None)


class DeadlineExceeded(RuntimeError):
    pass


def set_deadline(seconds: float | None) -> None:
    _deadline.set(time.monotonic() + seconds if seconds else None)


def remaining() -> float | None:
    deadline = _deadline.get()
    return None if deadline is None else deadline - time.monotonic()


def _budget(timeout: float) -> float:
    left = remaining()
    if left is None:
        return timeout
    if left < 2:
        raise DeadlineExceeded("The run's time budget is used up.")
    return min(timeout, left)


class stage:
    """`with llm.stage("extraction"): ...` labels the model calls made inside the block."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self) -> None:
        self._token = _stage.set(self.name)

    def __exit__(self, *_exc) -> None:
        _stage.reset(self._token)


def start_meter() -> list[dict]:
    calls: list[dict] = []
    _meter.set(calls)
    return calls


def _record(path: str, started: float, usage: dict | None, retries: int, ok: bool) -> None:
    calls = _meter.get()
    if calls is None:
        return
    usage = usage or {}
    calls.append(
        {
            "stage": _stage.get(),
            "method": path.rsplit(":", 1)[-1],
            "model": path.split("/")[1].split(":")[0] if "/" in path else path,
            "ms": round((time.perf_counter() - started) * 1000),
            "input_tokens": int(usage.get("promptTokenCount") or 0),
            "output_tokens": int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0),
            "retries": retries,
            "ok": ok,
        }
    )


def summarize_meter(calls: list[dict]) -> dict:
    stages: dict[str, dict] = {}
    for call in calls:
        entry = stages.setdefault(call["stage"], {"calls": 0, "ms": 0, "input_tokens": 0, "output_tokens": 0})
        entry["calls"] += 1
        entry["ms"] += call["ms"]
        entry["input_tokens"] += call["input_tokens"]
        entry["output_tokens"] += call["output_tokens"]
    return {
        "calls": len(calls),
        "failed_calls": sum(not c["ok"] for c in calls),
        "retries": sum(c["retries"] for c in calls),
        "model_ms": sum(c["ms"] for c in calls),
        "input_tokens": sum(c["input_tokens"] for c in calls),
        "output_tokens": sum(c["output_tokens"] for c in calls),
        "by_stage": stages,
    }
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 5


def gemini_post(path: str, payload: dict[str, Any], timeout: int = 30) -> dict:
    """POST to the Gemini API with exponential backoff on rate limits and transient failures."""
    if not settings.gemini_api_key:
        raise RuntimeError("Missing GEMINI_API_KEY.")
    started = time.perf_counter()
    try:
        data, retries = _post_with_retries(path, payload, timeout)
    except Exception:
        _record(path, started, None, _MAX_ATTEMPTS - 1, ok=False)
        raise
    _record(path, started, data.get("usageMetadata"), retries, ok=True)
    return data


def _post_with_retries(path: str, payload: dict[str, Any], timeout: int) -> tuple[dict, int]:
    body = json.dumps(payload).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        request = urllib.request.Request(
            f"{API_ROOT}/{path}",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "x-goog-api-key": settings.gemini_api_key},
        )
        try:
            with urllib.request.urlopen(request, timeout=_budget(timeout), context=SSL_CONTEXT) as response:
                return json.loads(response.read().decode("utf-8")), attempt
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            detail, retry_after = _error_detail(raw)
            last_error = RuntimeError(f"Gemini API error {exc.code}: {detail}")
            # A daily quota won't reset in seconds; retrying just delays the fallback.
            if exc.code not in _RETRYABLE_STATUS or "PerDay" in raw:
                raise last_error from exc
            # Per-minute limits say how long to wait; honour that rather than guessing.
            if retry_after is not None and retry_after <= 65 and attempt < _MAX_ATTEMPTS - 1:
                _sleep(retry_after + random.uniform(0.2, 1.0), last_error)
                continue
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            last_error = RuntimeError(f"Gemini API unreachable: {exc}")
        if attempt < _MAX_ATTEMPTS - 1:
            _sleep(min(1.5 * 2**attempt, 12) + random.uniform(0, 0.5), last_error)
    raise last_error or RuntimeError("Gemini API request failed.")


def _sleep(seconds: float, last_error: Exception | None) -> None:
    left = remaining()
    if left is not None and seconds > left - 2:
        raise last_error or DeadlineExceeded("No time left to retry.")
    time.sleep(seconds)


def _error_detail(raw: str) -> tuple[str, float | None]:
    """The error message and, when the API supplies one, its suggested retry delay in seconds."""
    try:
        error = json.loads(raw)["error"]
    except (ValueError, KeyError, TypeError):
        return raw[:300], None
    delay = None
    for item in error.get("details") or []:
        value = item.get("retryDelay")
        if isinstance(value, str) and value.endswith("s"):
            try:
                delay = float(value[:-1])
            except ValueError:
                pass
    return str(error.get("message", raw[:300])), delay


def thinking_config(model: str | None = None) -> dict[str, Any]:
    """Keep latency predictable: Gemini 3 models think at "high" by default, which can take minutes."""
    model = model or settings.gemini_model
    if model.startswith("gemini-3") and settings.gemini_thinking_level:
        return {"thinkingConfig": {"thinkingLevel": settings.gemini_thinking_level}}
    if model.startswith("gemini-2.5-flash"):
        return {"thinkingConfig": {"thinkingBudget": 0}}
    return {}


def response_json(data: dict) -> dict:
    """Parse the JSON body of a structured-output generateContent response."""
    candidates = data.get("candidates") or []
    parts = (candidates[0].get("content") or {}).get("parts") if candidates else None
    if not parts:
        reason = (candidates[0].get("finishReason") if candidates else None) or (
            data.get("promptFeedback", {}).get("blockReason") or "empty response"
        )
        raise RuntimeError(f"Gemini returned no content ({reason}).")
    return json.loads("".join(part.get("text", "") for part in parts if not part.get("thought")))
