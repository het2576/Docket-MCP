from __future__ import annotations

import json

import tracker_server as ts
from config import settings

# Two pairs that are semantically related but share almost no words, plus one
# unrelated distractor task.
TEST_TASKS = [
    {"id": "t1", "title": "Fix login bug", "owner": None, "due_date": None, "status": "Todo", "url": None},
    {"id": "t2", "title": "Authentication is broken", "owner": None, "due_date": None, "status": "Todo", "url": None},
    {"id": "t3", "title": "Update quarterly budget spreadsheet", "owner": None, "due_date": None, "status": "Todo", "url": None},
    {"id": "t4", "title": "Redo the Q3 finance numbers", "owner": None, "due_date": None, "status": "Todo", "url": None},
    {"id": "t5", "title": "Order new office chairs", "owner": None, "due_date": None, "status": "Todo", "url": None},
]

CASES = [
    ("Authentication is broken", "t1"),
    ("Redo the Q3 finance numbers", "t3"),
]


def main() -> None:
    if not settings.has_gemini:
        print("GEMINI_API_KEY not set - semantic scoring will be skipped, only lexical fallback runs.\n")

    ts.list_open_tasks = lambda: TEST_TASKS  # avoid hitting Notion/MOCK_TASKS for this smoke test

    for query, expected_id in CASES:
        expected_title = next(t["title"] for t in TEST_TASKS if t["id"] == expected_id)
        lexical_only = ts.similarity_score(query, expected_title)
        results = ts.search_similar_tasks(query)
        matched_ids = {task["id"] for task in results}

        caught_by_upgrade = expected_id in matched_ids
        missed_by_old_lexical = lexical_only < 0.28
        status = "PASS" if caught_by_upgrade else "FAIL"

        print(f"[{status}] query={query!r} -> expected match: {expected_title!r}")
        print(f"    lexical-only score: {round(lexical_only, 3)} (old version would catch it: {not missed_by_old_lexical})")
        print(f"    upgraded search results: {json.dumps(results, indent=2)}\n")


if __name__ == "__main__":
    main()
