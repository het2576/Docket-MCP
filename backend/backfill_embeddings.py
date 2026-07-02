from __future__ import annotations

import tracker_server as ts
from config import settings


def main() -> None:
    if not settings.has_gemini:
        print("GEMINI_API_KEY not set - nothing to backfill.")
        return

    tasks = ts.list_open_tasks()
    print(f"Warming embedding cache for {len(tasks)} open task(s)...")
    for i, task in enumerate(tasks, start=1):
        embedding = ts._get_task_embedding(task["id"], task["title"])
        status = "ok" if embedding is not None else "FAILED"
        print(f"  [{i}/{len(tasks)}] {status}: {task['title']!r}")


if __name__ == "__main__":
    main()
