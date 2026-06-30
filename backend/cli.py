from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from backend.agent import create_approved_tasks, run_agent


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python3 backend/cli.py process|create", file=sys.stderr)
        raise SystemExit(2)

    command = sys.argv[1]
    payload = json.loads(sys.stdin.read() or "{}")

    if command == "process":
        result = run_agent(
            payload.get("transcript", ""),
            source_meeting=payload.get("source_meeting") or "Manual transcript",
        )
    elif command == "create":
        result = create_approved_tasks(
            payload.get("items", []),
            source_meeting=payload.get("source_meeting") or "Manual transcript",
        )
    else:
        raise SystemExit(f"Unknown command: {command}")

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
