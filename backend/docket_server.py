"""Docket as an MCP server: the whole review pipeline as one tool, plus the tracker tools.

    python backend/docket_server.py        # stdio MCP server

Register it in Claude Desktop / Claude Code / any MCP host to let an assistant say "review
this transcript". `review_transcript` never writes to the tracker. Filing stays a separate,
explicit `create_task` / `update_task` call, so the host's own approval prompt is the human
sign-off.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from backend.agent import run_agent  # noqa: E402
from backend.dates import parse_iso_date  # noqa: E402
from backend.tracker_server import FastMCP, create_task, list_open_tasks, search_similar_tasks, update_task  # noqa: E402


def review_transcript(transcript: str, meeting_date: str | None = None, source_meeting: str | None = None) -> dict:
    """Find the action items in a meeting transcript and check each against the tracker. Read-only.

    Returns one entry per action item: task, owner, deadline (ISO, resolved against
    `meeting_date`, YYYY-MM-DD, default today), confidence, the quoted evidence line, and a
    decision: "new" (safe to create), "needs_review" (ask the user), or "duplicate" (already
    tracked; prefer update_task on `existing_task_id`). Nothing is filed.
    """
    result = run_agent(transcript, source_meeting=source_meeting, meeting_date=parse_iso_date(meeting_date))
    items = []
    for review in result["review_items"]:
        match = (review["similar_tasks"] or [None])[0]
        items.append(
            review["action_item"]
            | {
                "decision": review["decision"],
                "why": review["reasoning"],
                "existing_task_id": match["id"] if match and review["decision"] != "new" else None,
                "existing_task_title": match["title"] if match and review["decision"] != "new" else None,
            }
        )
    return {
        "summary": result["summary"],
        "meeting_date": result["meeting_date"],
        "action_items": items,
        "warnings": result["warnings"],
        "engine": result["engine"],
    }


if FastMCP is not None:
    mcp = FastMCP("docket")
    mcp.tool()(review_transcript)
    mcp.tool()(list_open_tasks)
    mcp.tool()(search_similar_tasks)
    mcp.tool()(create_task)
    mcp.tool()(update_task)
else:  # pragma: no cover
    mcp = None


if __name__ == "__main__":
    if mcp is None:
        raise SystemExit("Install the `mcp` package to run the MCP server.")
    mcp.run()
