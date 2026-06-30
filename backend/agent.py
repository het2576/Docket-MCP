from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from backend.extraction import extract_action_items
from backend.schema import ActionItem, ReviewItem, TrackerTask
from backend.tracker_server import create_task, list_open_tasks, search_similar_tasks, similarity_score

LOG_DIR = Path(__file__).resolve().parents[1] / "logs"


def _decision_for_item(item: ActionItem, similar: list[TrackerTask]) -> tuple[str, str]:
    if item.confidence < 0.6:
        return (
            "needs_review",
            "Extraction confidence is below 0.6, so this needs a human decision before creation.",
        )
    if not similar:
        return "new", "No sufficiently similar open tracker tasks were found."

    best = similar[0]
    score = similarity_score(item.task, best.title)
    if score >= 0.78:
        return (
            "duplicate",
            f"Closest open task is '{best.title}' with similarity {score:.2f}, likely the same work.",
        )
    if score >= 0.52:
        return (
            "needs_review",
            f"Closest open task is '{best.title}' with similarity {score:.2f}; related but may not be the same work — verify before creating.",
        )
    return "new", f"Closest task similarity is {score:.2f} against '{best.title}', so this appears to be distinct work."


def run_agent(transcript: str, source_meeting: str | None = None) -> dict:
    extraction = extract_action_items(transcript)
    open_tasks = [TrackerTask(**task) for task in list_open_tasks()]
    review_items: list[ReviewItem] = []

    trace_steps = [
        {"step": "extraction", "result": extraction.to_dict()},
        {"step": "list_open_tasks", "result": [task.to_dict() for task in open_tasks]},
    ]

    for item in extraction.action_items:
        similar = [TrackerTask(**{k: v for k, v in task.items() if k in TrackerTask.__annotations__}) for task in search_similar_tasks(item.task)]
        decision, reasoning = _decision_for_item(item, similar)
        review = ReviewItem(item, decision, reasoning, similar)
        review_items.append(review)
        trace_steps.append(
            {
                "step": "duplicate_check",
                "action_item": item.to_dict(),
                "similar_tasks": [task.to_dict() for task in similar],
                "decision": decision,
                "reasoning": reasoning,
            }
        )

    payload = {
        "source_meeting": source_meeting,
        "summary": extraction.summary,
        "review_items": [item.to_dict() for item in review_items],
        "trace": trace_steps,
    }
    log_path = _write_trace(payload)
    payload["log_path"] = str(log_path)
    return payload


def create_approved_tasks(items: list[dict], source_meeting: str | None = None) -> dict:
    created = []
    skipped = []
    for item in items:
        action = item.get("action_item", item)
        if not action.get("task"):
            skipped.append({"item": item, "reason": "Missing task title."})
            continue
        created.append(
            create_task(
                title=action["task"],
                owner=action.get("owner"),
                due_date=action.get("deadline"),
                source_meeting=source_meeting,
            )
        )
    return {"created": created, "skipped": skipped}


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
