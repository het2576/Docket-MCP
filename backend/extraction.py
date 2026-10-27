from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
import ssl
import time
from pathlib import Path
from typing import Any

try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:  # system trust store; never skip verification
    SSL_CONTEXT = ssl.create_default_context()

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from backend.config import settings
from backend.schema import ActionItem, ExtractionResult

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "owner": {"type": "string", "nullable": True},
                    "deadline": {"type": "string", "nullable": True},
                    "confidence": {"type": "number"},
                    "source_quote": {"type": "string"},
                },
                "required": ["task", "confidence", "source_quote"],
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["action_items", "summary"],
}


def _gemini_extract(transcript: str) -> ExtractionResult:
    import datetime
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    prompt = f"""
Extract meeting action items from this transcript as strict JSON.

Rules:
- Include task, owner, deadline, confidence, and source_quote.
- "task" must be a short, specific, imperative description of the actual work
  (e.g. "Tighten duplicate-detection scoring thresholds"), written in your own
  words. Never copy a full conversational line verbatim into "task" — strip
  names, questions, filler ("can you", "please", "I will"), and deadline
  phrases. Two tasks should only read as similar if they are genuinely the
  same piece of work; do not generalize specific tasks into vague ones that
  collide with unrelated work (e.g. keep "Tighten duplicate-detection scoring"
  distinct from "Review onboarding copy", even though both could loosely be
  called "review").
- owner is the person who is actually responsible for doing the work — the
  person who says "I will/I'll", who is directly addressed ("Name, can you
  ..."), or who is explicitly assigned. If multiple names are mentioned in a
  line, pick the one actually committing to or being assigned the task, not
  every name that appears. If owner is not explicit or strongly implied, use
  null — never guess from context alone.
- If a deadline is mentioned, output it as an ISO-8601 date string (YYYY-MM-DD). If it is a relative day like "Friday", infer the exact date assuming today is {today_str}. Use null for missing deadlines.
- confidence must reflect how explicit the task, owner, and deadline are in
  the transcript: 0.85-1.0 for an explicit task with explicit owner, 0.6-0.84
  for explicit task but inferred/implicit owner, below 0.6 for vague or
  ambiguous items (e.g. "someone should look into...").
- source_quote must be copied verbatim from the transcript — this is the only
  field that should contain the raw conversational line.
- Do not invent tasks. Do not merge two distinct commitments mentioned in
  different lines into one task, and do not split one commitment into two.

Transcript:
{transcript}
"""
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": EXTRACTION_SCHEMA,
            "temperature": 0.1,
            **_thinking_config(),
        },
    }
    data = gemini_post(f"models/{settings.gemini_model}:generateContent", payload, timeout=90)
    candidates = data.get("candidates") or []
    parts = (candidates[0].get("content") or {}).get("parts") if candidates else None
    if not parts:
        reason = (candidates[0].get("finishReason") if candidates else None) or (
            data.get("promptFeedback", {}).get("blockReason") or "empty response"
        )
        raise RuntimeError(f"Gemini returned no content ({reason}).")
    text = "".join(part.get("text", "") for part in parts)
    return _normalize_extraction(json.loads(text))


def _thinking_config() -> dict[str, Any]:
    model = settings.gemini_model
    if model.startswith("gemini-3") and settings.gemini_thinking_level:
        return {"thinkingConfig": {"thinkingLevel": settings.gemini_thinking_level}}
    if model.startswith("gemini-2.5-flash"):
        return {"thinkingConfig": {"thinkingBudget": 0}}
    return {}


def gemini_post(path: str, payload: dict[str, Any], timeout: int = 30) -> dict:
    """POST to the Gemini API, retrying once on rate limits and transient server errors."""
    request_body = json.dumps(payload).encode("utf-8")
    for attempt in range(2):
        request = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/{path}",
            data=request_body,
            method="POST",
            headers={"Content-Type": "application/json", "x-goog-api-key": settings.gemini_api_key or ""},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=SSL_CONTEXT) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8")
            if attempt == 0 and exc.code in (429, 500, 503):
                time.sleep(1.5)
                continue
            try:
                detail = json.loads(detail)["error"]["message"]
            except (ValueError, KeyError, TypeError):
                pass
            raise RuntimeError(f"Gemini API error {exc.code}: {detail}") from exc
    raise RuntimeError("Gemini API request failed.")


def _name_from_line(line: str) -> str | None:
    match = re.match(r"^\s*([A-Z][A-Za-z .'-]{1,40}):", line)
    return match.group(1).strip() if match else None


def _owner_from_line(line: str) -> str | None:
    speaker = _name_from_line(line)
    body = re.sub(r"^[A-Z][A-Za-z .'-]{1,40}:\s*", "", line).strip()
    direct_request = re.match(r"^([A-Z][A-Za-z .'-]{1,40}),\s+(can you|could you|please)\b", body)
    if direct_request:
        return direct_request.group(1).strip()
    if "someone" in body.lower():
        return None
    return speaker


def _deadline_from_line(line: str) -> str | None:
    when = r"((?:next|this)\s+[a-z]+day|[a-z]+day|tomorrow|today|EOD|end of (?:the )?(?:week|day|month)|next week)"
    match = re.search(rf"\b(?:by|due|before|on)\s+{when}\b", line, re.IGNORECASE)
    if not match:
        return None
    return match.group(1).replace("end of the ", "end of ")


def _clean_task_text(body: str) -> str:
    text = body.strip()
    # Strip a leading direct address ("Het, can you ...") down to the request verb.
    text = re.sub(r"^[A-Z][A-Za-z .'-]{1,40},\s+(can you|could you|please)\s+", "", text)
    # Strip leading affirmation fillers ("Yes,", "Sure,", "Ok,") before the commitment phrase.
    text = re.sub(r"^(yes|sure|okay|ok|alright|yep|yeah|absolutely)[,.]?\s+", "", text, flags=re.IGNORECASE)
    # Strip leading commitment phrasing ("I will", "I'll", "I need to", ...).
    text = re.sub(r"^(I will|I'll|I need to|I'm going to|I am going to)\s+", "", text, flags=re.IGNORECASE)
    # Strip a trailing deadline clause so the task reads as the work, not the schedule.
    text = re.sub(
        r"\s*[,]?\s*(by|due|before)\s+((next|this)\s+)?([A-Z]?[a-z]+day|tomorrow|today|EOD|end of (the )?(week|day|month)|next week)\b[.?!]*\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = text.rstrip("?.! ").strip()
    if text:
        text = text[0].upper() + text[1:]
    return text or body.strip()


def heuristic_extract(transcript: str) -> ExtractionResult:
    action_items: list[ActionItem] = []
    action_markers = (
        "will ",
        "i'll ",
        "i will ",
        "please ",
        "can you ",
        "need to ",
        "needs to ",
        "should ",
        "todo",
        "action item",
    )
    for raw_line in transcript.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.lower()
        if not any(marker in lowered for marker in action_markers):
            continue
        owner = _owner_from_line(line)
        task_text = re.sub(r"^[A-Z][A-Za-z .'-]{1,40}:\s*", "", line)
        task_text = re.sub(r"^(action item|todo)[: -]*", "", task_text, flags=re.IGNORECASE)
        task_text = _clean_task_text(task_text)
        confidence = 0.72 if owner else 0.52
        if "someone" in lowered or ("should" in lowered and owner is None):
            owner = None
            confidence = min(confidence, 0.55)
        action_items.append(
            ActionItem(
                task=task_text[:220],
                owner=owner,
                deadline=_deadline_from_line(line),
                confidence=confidence,
                source_quote=line,
            )
        )

    summary = (
        "Heuristic extraction completed without Gemini. Add GEMINI_API_KEY for production extraction."
        if action_items
        else "No action items found by the local heuristic extractor."
    )
    return ExtractionResult(action_items=action_items, summary=summary)


def _normalize_extraction(parsed: dict[str, Any]) -> ExtractionResult:
    items = []
    for item in parsed.get("action_items", []):
        task = str(item.get("task") or "").strip()
        if not task:
            continue
        try:
            confidence = float(item.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0
        items.append(
            ActionItem(
                task=task,
                owner=(str(item.get("owner") or "").strip() or None),
                deadline=(str(item.get("deadline") or "").strip() or None),
                confidence=min(max(confidence, 0.0), 1.0),
                source_quote=str(item.get("source_quote", "")).strip(),
            )
        )
    return ExtractionResult(action_items=items, summary=str(parsed.get("summary", "")).strip())


def extract_action_items(transcript: str) -> ExtractionResult:
    if settings.has_gemini:
        return _gemini_extract(transcript)
    return heuristic_extract(transcript)


if __name__ == "__main__":
    transcript = Path(sys.argv[1]).read_text() if len(sys.argv) > 1 else sys.stdin.read()
    print(json.dumps(extract_action_items(transcript).to_dict(), indent=2))
