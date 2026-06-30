from __future__ import annotations

import json
from pathlib import Path

from extraction import extract_action_items


def main() -> None:
    for path in sorted((Path(__file__).resolve().parents[1] / "samples").glob("*.txt")):
        print(f"\n=== {path.name} ===")
        result = extract_action_items(path.read_text(encoding="utf-8"))
        print(json.dumps(result.to_dict(), indent=2))


if __name__ == "__main__":
    main()
