"""Online metrics from real use, and new eval cases harvested from user corrections.

    python -m evals.feedback                 # summary -> evals/results/feedback.json
    python -m evals.feedback --harvest       # also write evals/data/candidates.jsonl for labelling

Offline evals say how Docket does on cases we wrote; this says how it does on meetings people
actually ran. Every disagreement between Docket's default and the user's choice is a candidate
eval case, which keeps the eval set growing in the direction real usage pulls it.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FEEDBACK = ROOT / "logs" / "feedback.jsonl"
OUT = ROOT / "evals" / "results" / "feedback.json"
CANDIDATES = ROOT / "evals" / "data" / "candidates.jsonl"


def load(path: Path = FEEDBACK) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def summarize(records: list[dict]) -> dict:
    items = [item for record in records for item in record["items"]]
    selected = [i for i in items if i["selected"]]
    by_decision = {}
    for decision in ("new", "needs_review", "duplicate"):
        sub = [i for i in items if i["decision"] == decision]
        by_decision[decision] = {"shown": len(sub), "accepted": rate(sum(i["selected"] for i in sub), len(sub))}
    edits = Counter(field for i in selected for field in i["edited"])
    meetings_untouched = sum(
        all(i["selected"] == i["default_selected"] and not i["edited"] for i in record["items"])
        for record in records
        if record["items"]
    )
    dup_selected = [i for i in selected if i["decision"] == "duplicate"]
    return {
        "meetings": len(records),
        "items_shown": len(items),
        "acceptance_rate": rate(len(selected), len(items)),
        "default_kept_rate": rate(sum(i["selected"] == i["default_selected"] for i in items), len(items)),
        "one_click_meetings": rate(meetings_untouched, len([r for r in records if r["items"]])),
        "edit_rate": {field: rate(edits[field], len(selected)) for field in ("task", "owner", "deadline")},
        "by_decision": by_decision,
        "duplicates_updated_not_created": rate(sum(i["mode"] == "update" for i in dup_selected), len(dup_selected)),
        "filing_failures": sum(i["failed"] for i in items),
    }


def harvest(records: list[dict]) -> list[dict]:
    """Items where the user disagreed with Docket: the most informative new eval cases."""
    out = []
    for record in records:
        for item in record["items"]:
            reasons = []
            if item["selected"] != item["default_selected"]:
                reasons.append("selection overridden")
            reasons += [f"{field} edited" for field in item["edited"]]
            if item["decision"] == "duplicate" and item["selected"] and item["mode"] == "create":
                reasons.append("filed as new despite duplicate flag")
            if reasons:
                out.append(
                    {
                        "status": "needs_label",
                        "source_meeting": record.get("source_meeting"),
                        "meeting_date": record.get("meeting_date"),
                        "source_quote": item["original"].get("source_quote"),
                        "model": item["original"],
                        "user": item["final"] if item["selected"] else None,
                        "decision": item["decision"],
                        "why": reasons,
                    }
                )
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harvest", action="store_true")
    args = parser.parse_args()
    records = load()
    summary = summarize(records)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    if args.harvest:
        cases = harvest(records)
        CANDIDATES.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases))
        print(f"\n{len(cases)} candidate eval case(s) -> {CANDIDATES.relative_to(ROOT)} (label them before adding to the suite)")


if __name__ == "__main__":
    main()
