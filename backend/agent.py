from __future__ import annotations

import json
import sys
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from backend import llm
from backend.adjudicator import adjudicate
from backend.config import settings
from backend.extraction import PROMPT_VERSION, extract_action_items, heuristic_extract
from backend.schema import ActionItem, ReviewItem
from backend.tracker_server import (
    LEXICAL_DUPLICATE,
    LEXICAL_REVIEW,
    SEMANTIC_DUPLICATE,
    SEMANTIC_REVIEW,
    create_task,
    update_task,
    get_embeddings,
    list_open_tasks,
    rank_similar_tasks,
    warm_task_embeddings,
)

LOG_DIR = Path(__file__).resolve().parents[1] / "logs"


def _decision_for_item(item: ActionItem, similar: list[dict]) -> tuple[str, str]:
    """Threshold router: the fallback when the LLM double-check is off or unavailable."""
    vague = item.confidence < 0.6
    vague_reason = "The transcript is vague about this task or who owns it, so a human should decide."
    if not similar:
        if vague:
            return "needs_review", vague_reason
        return "new", "No sufficiently similar open tracker tasks were found."

    best = similar[0]
    lexical = best.get("lexical") or 0.0
    semantic = best.get("semantic") or 0.0
    basis = f"wording overlap {lexical:.2f}" + (f", meaning match {semantic:.2f}" if best.get("semantic") is not None else "")
    if lexical >= LEXICAL_DUPLICATE or semantic >= SEMANTIC_DUPLICATE:
        return "duplicate", f"Closest open task is '{best['title']}' ({basis}), likely the same work."
    if vague:
        return "needs_review", vague_reason
    if lexical >= LEXICAL_REVIEW or semantic >= SEMANTIC_REVIEW:
        return (
            "needs_review",
            f"Closest open task is '{best['title']}' ({basis}); related but may not be the same work — verify before creating.",
        )
    return "new", f"Closest open task is '{best['title']}' ({basis}), so this appears to be distinct work."


Route = tuple[str, str, dict | None]

# Every true duplicate the judge confirmed on the dev split had cosine >= 0.773; below this
# floor a "same" verdict is treated as "possible duplicate" (see evals/README.md).
JUDGE_SAME_MIN_SEMANTIC = 0.75
JUDGE_SAME_MIN_LEXICAL = 0.5


def _retrieval_agrees(task: dict) -> bool:
    semantic = task.get("semantic")
    if semantic is not None and semantic >= JUDGE_SAME_MIN_SEMANTIC:
        return True
    return (task.get("lexical") or 0.0) >= JUDGE_SAME_MIN_LEXICAL


def route_items(items: list[ActionItem], similar_lists: list[list[dict]], warnings: list[str] | None = None) -> list[Route]:
    """Decide new / needs_review / duplicate for each item from its ranked tracker matches.

    Retrieval (wording + embeddings) proposes candidates; an LLM judge then decides whether
    each candidate is the same work. Thresholds are the fallback. Reorders each similar list
    in place so the task the judge matched comes first.
    """
    fallback: list[Route] = [(*_decision_for_item(item, similar), None) for item, similar in zip(items, similar_lists)]
    if not (settings.dedup_judge and settings.has_gemini):
        return fallback
    left = llm.remaining()
    if left is not None and left < 15 and any(similar_lists):
        if warnings is not None:
            warnings.append("The model was slow, so the duplicate double-check was skipped and similarity scores alone were used.")
        return fallback
    try:
        verdicts = adjudicate(items, similar_lists)
    except Exception as exc:
        if warnings is not None:
            warnings.append(f"The duplicate double-check was unavailable, so similarity scores alone were used. ({_short(exc)})")
        return fallback

    routes: list[Route] = []
    for index, (item, similar) in enumerate(zip(items, similar_lists)):
        verdict = verdicts.get(index)
        if verdict is None:
            decision, reasoning, _ = fallback[index]
            if decision == "duplicate":
                # The judge ran but gave no answer for this item; similarity alone may not drop work.
                decision, reasoning = "needs_review", reasoning.replace("likely the same work", "possibly the same work")
            routes.append((decision, reasoning, None))
            continue
        match = next((task for task in similar if task["id"] == verdict["task_id"]), None)
        if match is not None:
            similar.remove(match)
            similar.insert(0, match)
        reason = verdict["reason"].rstrip(".")
        if verdict["verdict"] == "same" and match is not None:
            if _retrieval_agrees(match):
                routes.append(("duplicate", f"Already tracked as '{match['title']}': {reason}.", verdict))
            else:
                # The judge alone may not drop work: a "same" verdict on a weak retrieval match
                # (evals: "GDPR data export" vs "Fix broken CSV export") goes to a human instead.
                routes.append(("needs_review", f"Possibly already tracked as '{match['title']}': {reason}.", verdict))
        elif item.confidence < 0.6:
            routes.append(("needs_review", "The transcript is vague about this task or who owns it, so a human should decide.", verdict))
        elif verdict["verdict"] == "related" and match is not None:
            routes.append(("needs_review", f"Related to '{match['title']}' but may be different work: {reason}.", verdict))
        else:
            routes.append(("new", f"No open task covers this work. {reason}.", verdict))
    return routes


def _engine_info(extraction_engine: str) -> dict:
    return {
        "extraction": extraction_engine,
        "model": settings.gemini_model if extraction_engine == "gemini" else None,
        "prompt_version": PROMPT_VERSION if extraction_engine == "gemini" else None,
        "tracker": "notion" if settings.has_notion else "demo",
        "semantic_search": settings.has_gemini,
        "dedup_judge": settings.has_gemini and settings.dedup_judge,
    }


def run_agent(transcript: str, source_meeting: str | None = None, meeting_date: date | None = None) -> dict:
    warnings: list[str] = []
    meeting_date = meeting_date or date.today()
    started = time.perf_counter()
    calls = llm.start_meter()
    llm.set_deadline(settings.run_budget_s or None)

    extraction_engine = "gemini" if settings.has_gemini else "heuristic"
    fallback_reason = None
    try:
        with llm.stage("extraction"):
            extraction = extract_action_items(transcript, meeting_date)
    except Exception as exc:
        fallback_reason = _short(exc)
        warnings.append(f"Gemini extraction failed, so the rule-based extractor was used instead. ({_short(exc)})")
        extraction_engine = "heuristic"
        extraction = heuristic_extract(transcript, meeting_date)

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
            with llm.stage("embeddings"):
                query_embeddings = list(get_embeddings([item.task for item in extraction.action_items]))
                warm_task_embeddings(open_tasks)
        except Exception as exc:
            warnings.append(f"Meaning-based matching was unavailable, so only wording was compared. ({_short(exc)})")

    similar_lists = [
        rank_similar_tasks(item.task, open_tasks, query_embedding=query_embedding) if tracker_ok else []
        for item, query_embedding in zip(extraction.action_items, query_embeddings)
    ]
    if tracker_ok:
        with llm.stage("duplicate_judge"):
            routes = route_items(extraction.action_items, similar_lists, warnings)
    else:
        routes = [("needs_review", "The tracker was unreachable, so this wasn't checked for duplicates.", None)] * len(
            extraction.action_items
        )
    llm.set_deadline(None)
    for item, similar, (decision, reasoning, judge) in zip(extraction.action_items, similar_lists, routes):
        review_items.append(ReviewItem(item, decision, reasoning, similar, judge))
        trace_steps.append(
            {
                "step": "duplicate_check",
                "action_item": item.to_dict(),
                "similar_tasks": similar,
                "decision": decision,
                "reasoning": reasoning,
                "judge": judge,
            }
        )

    payload = {
        "source_meeting": source_meeting,
        "meeting_date": meeting_date.isoformat(),
        "summary": extraction.summary,
        "review_items": [item.to_dict() for item in review_items],
        "open_task_count": len(open_tasks),
        "engine": _engine_info(extraction_engine) | {"fallback_reason": fallback_reason},
        "warnings": warnings,
        "usage": llm.summarize_meter(calls) | {"wall_ms": round((time.perf_counter() - started) * 1000)},
        "trace": trace_steps,
    }
    log_path = _write_trace(payload)
    payload["log_path"] = str(log_path.relative_to(LOG_DIR.parent))
    return payload


def _update_note(action: dict, source_meeting: str | None, meeting_date: str | None) -> str:
    when = f" on {meeting_date}" if meeting_date else ""
    where = f"“{source_meeting}”" if source_meeting else "a meeting"
    details = [f"owner {action['owner']}" if action.get("owner") else None,
               f"due {action['deadline']}" if action.get("deadline") else None]
    extra = "; ".join(d for d in details if d)
    quote = (action.get("source_quote") or "").strip()
    return (
        f"Mentioned in {where}{when}: {action.get('task', '').strip()}" + (f" ({extra})" if extra else "") + "."
        + (f" Quote: “{quote}”" if quote else "")
    )


def create_approved_tasks(
    items: list[dict], source_meeting: str | None = None, meeting_date: str | None = None
) -> dict:
    """File approved items. `mode: "update"` + `task_id` adds a note to an existing task instead."""
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
            if item.get("mode") == "update" and item.get("task_id"):
                task = update_task(
                    task_id=item["task_id"],
                    note=_update_note(action, source_meeting, meeting_date),
                    due_date=(action.get("deadline") or "").strip() or None,
                )
                task |= {"mode": "update"}
            else:
                task = create_task(
                    title=title,
                    owner=(action.get("owner") or "").strip() or None,
                    due_date=(action.get("deadline") or "").strip() or None,
                    source_meeting=source_meeting,
                ) | {"mode": "create"}
        except Exception as exc:
            failed.append({"index": index, "title": title, "reason": _short(exc)})
            continue
        created.append(task | {"index": index})
    return {"created": created, "skipped": skipped, "failed": failed}


def _short(exc: Exception, limit: int = 240) -> str:
    text = " ".join(str(exc).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _write_trace(payload: dict) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # Unique per run: concurrent requests in the same second must not overwrite each other's audit trail.
    path = LOG_DIR / f"agent_trace_{stamp}_{uuid.uuid4().hex[:6]}.json"
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
