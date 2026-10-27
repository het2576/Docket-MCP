from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from backend.config import settings
from backend.extraction import extract_action_items, heuristic_extract
from backend.schema import ActionItem, ReviewItem
from backend.tracker_server import (
    LEXICAL_DUPLICATE,
    LEXICAL_REVIEW,
    SEMANTIC_DUPLICATE,
    SEMANTIC_REVIEW,
    create_task,
    get_embeddings,
    list_open_tasks,
    rank_similar_tasks,
    warm_task_embeddings,
)

LOG_DIR = Path(__file__).resolve().parents[1] / "logs"


def _decision_for_item(item: ActionItem, similar: list[dict]) -> tuple[str, str]:
    if item.confidence < 0.6:
        return (
            "needs_review",
            "Extraction confidence is below 0.6, so this needs a human decision before creation.",
        )
    if not similar:
        return "new", "No sufficiently similar open tracker tasks were found."

    best = similar[0]
    lexical = best.get("lexical") or 0.0
    semantic = best.get("semantic") or 0.0
    basis = f"wording overlap {lexical:.2f}" + (f", meaning match {semantic:.2f}" if best.get("semantic") is not None else "")
    if lexical >= LEXICAL_DUPLICATE or semantic >= SEMANTIC_DUPLICATE:
        return "duplicate", f"Closest open task is '{best['title']}' ({basis}), likely the same work."
    if lexical >= LEXICAL_REVIEW or semantic >= SEMANTIC_REVIEW:
        return (
            "needs_review",
            f"Closest open task is '{best['title']}' ({basis}); related but may not be the same work — verify before creating.",
        )
    return "new", f"Closest open task is '{best['title']}' ({basis}), so this appears to be distinct work."


def _engine_info(extraction_engine: str) -> dict:
    return {
        "extraction": extraction_engine,
        "model": settings.gemini_model if extraction_engine == "gemini" else None,
        "tracker": "notion" if settings.has_notion else "demo",
        "semantic_search": settings.has_gemini,
    }


def run_agent(transcript: str, source_meeting: str | None = None) -> dict:
    warnings: list[str] = []

    extraction_engine = "gemini" if settings.has_gemini else "heuristic"
    try:
        extraction = extract_action_items(transcript)
    except Exception as exc:
        warnings.append(f"Gemini extraction failed, so the rule-based extractor was used instead. ({_short(exc)})")
        extraction_engine = "heuristic"
        extraction = heuristic_extract(transcript)

    tracker_ok = True
    try:
        open_tasks = list_open_tasks()
    except Exception as exc:
        tracker_ok = False
        open_tasks = []
        warnings.append(f"The tracker couldn't be read, so duplicates weren't checked. ({_short(exc)})")

    trace_steps = [
        {"step": "extraction", "engine": extraction_engine, "result": extraction.to_dict()},
        {"step": "list_open_tasks", "result": open_tasks},
    ]
    review_items: list[ReviewItem] = []

    query_embeddings: list[list[float] | None] = [None] * len(extraction.action_items)
    if tracker_ok and open_tasks and extraction.action_items and settings.has_gemini:
        try:
            query_embeddings = list(get_embeddings([item.task for item in extraction.action_items]))
            warm_task_embeddings(open_tasks)
        except Exception as exc:
            warnings.append(f"Meaning-based matching was unavailable, so only wording was compared. ({_short(exc)})")

    for item, query_embedding in zip(extraction.action_items, query_embeddings):
        if tracker_ok:
            similar = rank_similar_tasks(item.task, open_tasks, query_embedding=query_embedding)
            decision, reasoning = _decision_for_item(item, similar)
        else:
            similar = []
            decision, reasoning = "needs_review", "The tracker was unreachable, so this wasn't checked for duplicates."
        review_items.append(ReviewItem(item, decision, reasoning, similar))
        trace_steps.append(
            {
                "step": "duplicate_check",
                "action_item": item.to_dict(),
                "similar_tasks": similar,
                "decision": decision,
                "reasoning": reasoning,
            }
        )

    payload = {
        "source_meeting": source_meeting,
        "summary": extraction.summary,
        "review_items": [item.to_dict() for item in review_items],
        "open_task_count": len(open_tasks),
        "engine": _engine_info(extraction_engine),
        "warnings": warnings,
        "trace": trace_steps,
    }
    log_path = _write_trace(payload)
    payload["log_path"] = str(log_path.relative_to(LOG_DIR.parent))
    return payload


def create_approved_tasks(items: list[dict], source_meeting: str | None = None) -> dict:
    created = []
    skipped = []
    failed = []
    for index, item in enumerate(items):
        action = item.get("action_item", item)
        title = (action.get("task") or "").strip()
        if not title:
            skipped.append({"index": index, "reason": "Missing task title."})
            continue
        try:
            task = create_task(
                title=title,
                owner=(action.get("owner") or "").strip() or None,
                due_date=(action.get("deadline") or "").strip() or None,
                source_meeting=source_meeting,
            )
        except Exception as exc:
            failed.append({"index": index, "title": title, "reason": _short(exc)})
            continue
        created.append(task | {"index": index})
    return {"created": created, "skipped": skipped, "failed": failed}


def _short(exc: Exception, limit: int = 240) -> str:
    text = " ".join(str(exc).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _write_trace(payload: dict) -> Path:
    LOG_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = LOG_DIR / f"agent_trace_{stamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python3 backend/agent.py <transcript-file>", file=sys.stderr)
        raise SystemExit(2)
    transcript = Path(sys.argv[1]).read_text(encoding="utf-8")
    print(json.dumps(run_agent(transcript, source_meeting=Path(sys.argv[1]).stem), indent=2))


if __name__ == "__main__":
    _main()
