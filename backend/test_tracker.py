from __future__ import annotations

import json

from tracker_server import create_task, list_open_tasks, search_similar_tasks


def main() -> None:
    print("Open tasks")
    print(json.dumps(list_open_tasks(), indent=2))
    print("\nSimilar tasks")
    print(json.dumps(search_similar_tasks("Review duplicate detection thresholds"), indent=2))
    print("\nCreate task")
    print(json.dumps(create_task("Test task from Docket", "Het", None, "Local smoke test"), indent=2))


if __name__ == "__main__":
    main()
