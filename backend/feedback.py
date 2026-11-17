"""Production feedback: what Docket proposed vs what the user actually filed.

Every review that ends in "File" appends one JSON line to logs/feedback.jsonl. Each item records
the model's original proposal, the user's final version, the default selection, and what the user
did. `python -m evals.feedback` turns this into online metrics (acceptance, edit rates) and
candidate eval cases, so real usage feeds the offline eval set.

Stored locally only. Transcripts are not stored, just the quoted evidence line per item.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

FEEDBACK_PATH = Path(__file__).resolve().parents[1] / "logs" / "feedback.jsonl"
_FIELDS = ("task", "owner", "deadline")
_lock = threading.Lock()


def _norm(value) -> str:
    return " ".join(str(value or "").split()).strip().lower()


def edited_fields(original: dict, final: dict) -> list[str]:
    return [field for field in _FIELDS if _norm(original.get(field)) != _norm(final.get(field))]


def record_feedback(payload: dict, result: dict) -> None:
    filed = {entry["index"]: entry for entry in result.get("created", [])}
    failed = {entry["index"] for entry in result.get("failed", [])} | {e["index"] for e in result.get("skipped", [])}
    # Indices in `result` refer to positions in payload["items"]; map them back to review positions.
    item_positions = [item.get("review_index") for item in payload.get("items", [])]
    filed_review = {item_positions[i]: entry for i, entry in filed.items() if i < len(item_positions)}
    failed_review = {item_positions[i] for i in failed if i < len(item_positions)}

    items = []
    for row in payload.get("feedback", []):
        original, final = row.get("original") or {}, row.get("final") or {}
        index = row.get("index")
        default_selected = row.get("decision") == "new"
        items.append(
            {
                "index": index,
                "decision": row.get("decision"),
                "judge_verdict": row.get("judge_verdict"),
                "default_selected": default_selected,
                "selected": bool(row.get("selected")),
                "mode": row.get("mode"),
                "filed": index in filed_review,
                "failed": index in failed_review,
                "edited": edited_fields(original, final) if row.get("selected") else [],
                "confidence": original.get("confidence"),
                "original": {k: original.get(k) for k in (*_FIELDS, "source_quote")},
                "final": {k: final.get(k) for k in _FIELDS},
            }
        )
    record = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_meeting": payload.get("source_meeting"),
        "meeting_date": payload.get("meeting_date"),
        "engine": payload.get("engine"),
        "trace": payload.get("trace_path"),
        "items": items,
    }
    FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock, FEEDBACK_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
