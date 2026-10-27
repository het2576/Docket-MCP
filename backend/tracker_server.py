from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
import urllib.error
import urllib.request
import ssl
from datetime import date, timedelta
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:  # system trust store; never skip verification
    SSL_CONTEXT = ssl.create_default_context()

try:  # mcp 2.x renamed FastMCP to MCPServer; same tool()/run() API.
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:  # pragma: no cover - lets direct scripts run before mcp install.
        FastMCP = None  # type: ignore[assignment,misc]

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from backend.config import settings
from backend.extraction import gemini_post
from backend.schema import TrackerTask

MOCK_TASKS = [
    TrackerTask(
        id="mock-1",
        title="Finish onboarding copy for MeetingPilot",
        owner="Priya",
        due_date="Friday",
        status="In progress",
    ),
    TrackerTask(
        id="mock-2",
        title="Review duplicate detection thresholds",
        owner="Het",
        due_date=None,
        status="Todo",
    ),
]


def _notion_request(path: str, payload: dict[str, Any] | None = None, method: str = "POST") -> dict:
    if not settings.has_notion:
        raise RuntimeError("Missing NOTION_API_KEY or NOTION_DATABASE_ID.")

    body = json.dumps(payload or {}).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"https://api.notion.com/v1/{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {settings.notion_api_key}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30, context=SSL_CONTEXT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8")
        try:
            detail = json.loads(detail)["message"]
        except (ValueError, KeyError, TypeError):
            pass
        raise RuntimeError(f"Notion API error {exc.code}: {detail}") from exc


def _plain_text(prop: dict[str, Any] | None) -> str | None:
    if not prop:
        return None
    values = prop.get("title") or prop.get("rich_text") or []
    text = "".join(part.get("plain_text", "") for part in values)
    return text or None


def _person_text(prop: dict[str, Any] | None) -> str | None:
    if not prop:
        return None
    people = prop.get("people") or []
    names = [person.get("name") for person in people if person.get("name")]
    if names:
        return ", ".join(names)
    return _plain_text(prop)


def _date_text(prop: dict[str, Any] | None) -> str | None:
    date = (prop or {}).get("date")
    return date.get("start") if date else None


def _status_text(prop: dict[str, Any] | None) -> str | None:
    if not prop:
        return None
    for key in ("status", "select"):
        if prop.get(key):
            return prop[key].get("name")
    return _plain_text(prop)


_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _resolve_due_date(due_date: str | None, today: date | None = None) -> str | None:
    """Normalize a deadline to an ISO-8601 date so it lands in Notion's actual date property.

    Gemini extraction already resolves relative deadlines to ISO dates, but the
    no-API-key heuristic extractor only returns the matched word ("Friday",
    "tomorrow", "EOD"), so this catches whatever extraction didn't normalize.
    """
    if not due_date:
        return None
    text = due_date.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", text):
        return text[:10]

    today = today or date.today()
    lowered = text.lower()

    if lowered in ("today", "eod", "end of day"):
        return today.isoformat()
    if lowered == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    if lowered in ("end of week", "eow"):
        return (today + timedelta(days=(4 - today.weekday()) % 7)).isoformat()
    if lowered == "next week":
        return (today + timedelta(days=7 - today.weekday())).isoformat()
    if lowered in ("end of month", "eom"):
        next_month = date(today.year + today.month // 12, today.month % 12 + 1, 1)
        return (next_month - timedelta(days=1)).isoformat()

    prefix, _, day = lowered.rpartition(" ")
    if day in _WEEKDAYS and prefix in ("", "this", "on", "next"):
        days_ahead = (_WEEKDAYS[day] - today.weekday()) % 7
        if prefix == "next" and days_ahead == 0:
            days_ahead = 7
        return (today + timedelta(days=days_ahead)).isoformat()

    return None


def _page_to_task(page: dict[str, Any]) -> TrackerTask:
    props = page.get("properties", {})
    return TrackerTask(
        id=page.get("id", ""),
        title=_plain_text(props.get(settings.notion_title_property)) or "Untitled",
        owner=_person_text(props.get(settings.notion_owner_property)),
        due_date=_date_text(props.get(settings.notion_due_date_property)),
        status=_status_text(props.get(settings.notion_status_property)),
        url=page.get("url"),
    )


_STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "by", "with",
    "from", "into", "before", "after", "this", "that", "will", "should", "need",
    "needs", "please", "can", "you", "review", "update", "fix", "add", "create",
    "task", "item", "due", "asap", "today", "tomorrow", "someone", "yes", "okay",
}


def _tokenize(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return {token for token in tokens if len(token) > 2 and token not in _STOPWORDS}


def similarity_score(left: str, right: str) -> float:
    left_tokens = _tokenize(left)
    right_tokens = _tokenize(right)
    if not left_tokens or not right_tokens:
        return 0.0
    token_score = len(left_tokens & right_tokens) / max(len(left_tokens | right_tokens), 1)
    ratio_score = SequenceMatcher(None, left.lower(), right.lower()).ratio()
    return max(token_score, ratio_score * 0.85)


_EMBEDDING_MODEL = "gemini-embedding-001"

# There's no SQL table backing tasks (storage is Notion, or MOCK_TASKS in
# no-Notion mode), so this file stands in for the "embedding column" -
# keyed by task id, written on create and lazily backfilled on first compare.
_EMBEDDING_CACHE_PATH = Path(__file__).resolve().parent / "embedding_cache.json"
_embedding_cache: dict[str, dict[str, Any]] | None = None


def _load_embedding_cache() -> dict[str, dict[str, Any]]:
    global _embedding_cache
    if _embedding_cache is None:
        try:
            _embedding_cache = json.loads(_EMBEDDING_CACHE_PATH.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            _embedding_cache = {}
    return _embedding_cache


def _save_embedding_cache() -> None:
    if _embedding_cache is not None:
        _EMBEDDING_CACHE_PATH.write_text(json.dumps(_embedding_cache))


def get_embedding(text: str) -> list[float]:
    if not settings.has_gemini:
        raise RuntimeError("Missing GEMINI_API_KEY.")

    data = gemini_post(f"models/{_EMBEDDING_MODEL}:embedContent", {"content": {"parts": [{"text": text}]}})
    return data["embedding"]["values"]


def get_embeddings(texts: list[str]) -> list[list[float]]:
    """Embed several texts in one round trip."""
    if not texts:
        return []
    if not settings.has_gemini:
        raise RuntimeError("Missing GEMINI_API_KEY.")
    model = f"models/{_EMBEDDING_MODEL}"
    data = gemini_post(
        f"{model}:batchEmbedContents",
        {"requests": [{"model": model, "content": {"parts": [{"text": text}]}} for text in texts]},
    )
    return [item["values"] for item in data["embeddings"]]


def warm_task_embeddings(tasks: list[dict]) -> None:
    """Fill the embedding cache for every task that's missing or renamed, in one batch."""
    cache = _load_embedding_cache()
    missing = [t for t in tasks if (cache.get(t["id"]) or {}).get("title") != t["title"]]
    if not missing or not settings.has_gemini:
        return
    try:
        vectors = get_embeddings([t["title"] for t in missing])
    except Exception as exc:
        logger.warning("Could not batch-embed tracker tasks: %s", exc)
        return
    for task, vector in zip(missing, vectors):
        cache[task["id"]] = {"title": task["title"], "embedding": vector}
    _save_embedding_cache()


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _get_task_embedding(task_id: str, title: str) -> list[float] | None:
    """Return the stored embedding for a task, computing and caching it on first use.

    Covers both a freshly created task (cache miss written immediately by
    create_task) and an older, pre-upgrade task with no cache entry yet -
    the latter gets backfilled here instead of needing a migration script.
    """
    cache = _load_embedding_cache()
    entry = cache.get(task_id)
    if entry and entry.get("title") == title:
        return entry.get("embedding")

    try:
        embedding = get_embedding(title)
    except Exception as exc:
        logger.warning("Falling back to lexical-only scoring for task %s: %s", task_id, exc)
        return None

    cache[task_id] = {"title": title, "embedding": embedding}
    _save_embedding_cache()
    return embedding


@lru_cache(maxsize=1)
def _database_properties() -> dict[str, dict[str, Any]]:
    """Fetch the Notion database schema once per process, keyed by property name."""
    data = _notion_request(f"databases/{settings.notion_database_id}", method="GET")
    return data.get("properties", {})


def _property_types() -> dict[str, str]:
    """Map each Notion database property name to its type ("status", "select", "people", ...)."""
    return {name: prop.get("type", "") for name, prop in _database_properties().items()}


def _status_filter() -> dict[str, Any] | None:
    prop = _database_properties().get(settings.notion_status_property) or {}
    prop_type = prop.get("type")
    if prop_type not in ("status", "select"):
        return None
    # Notion rejects filters that name an option the column doesn't have.
    options = {option.get("name", "").lower() for option in prop.get(prop_type, {}).get("options", [])}
    done = [value for value in settings.notion_done_values if value.lower() in options]
    if not done:
        return None
    return {
        "and": [
            {"property": settings.notion_status_property, prop_type: {"does_not_equal": value}}
            for value in done
        ]
    }


def list_open_tasks() -> list[dict]:
    """Return every open task in the tracker, skipping ones whose status is a "done" value."""
    if not settings.has_notion:
        return [task.to_dict() for task in MOCK_TASKS]

    payload: dict[str, Any] = {"page_size": 100}
    status_filter = _status_filter()
    if status_filter:
        payload["filter"] = status_filter

    tasks: list[dict] = []
    while True:
        data = _notion_request(f"databases/{settings.notion_database_id}/query", payload)
        tasks.extend(_page_to_task(page).to_dict() for page in data.get("results", []))
        if not data.get("has_more") or not data.get("next_cursor"):
            return tasks
        payload["start_cursor"] = data["next_cursor"]


_LEXICAL_THRESHOLD = 0.28

# gemini-embedding-001 cosine similarities run high even between unrelated
# titles (~0.45-0.6 baseline), unlike lexical scores which sit near 0 for
# unrelated text. Reusing the lexical threshold here would surface nearly
# every task on every query, so semantic matches need their own, higher bar.
_SEMANTIC_THRESHOLD = 0.65

# Decision bands, calibrated on gemini-embedding-001: unrelated titles sit at
# ~0.54-0.56 cosine, related-but-different work ~0.71-0.76, same work ~0.85+.
LEXICAL_DUPLICATE = 0.78
LEXICAL_REVIEW = 0.52
SEMANTIC_DUPLICATE = 0.84
SEMANTIC_REVIEW = 0.68


_NO_EMBEDDING: Any = object()


def rank_similar_tasks(
    query: str, tasks: list[dict], limit: int = 5, query_embedding: list[float] | None = _NO_EMBEDDING
) -> list[dict]:
    """Score `query` against already-fetched tasks; lets the agent reuse one tracker read.

    Pass `query_embedding` when it was computed in a batch (None means semantic search is off).
    """
    if query_embedding is _NO_EMBEDDING:
        try:
            query_embedding = get_embedding(query)
        except Exception as exc:
            logger.warning("Semantic search unavailable, falling back to lexical-only: %s", exc)
            query_embedding = None

    ranked = []
    for raw in tasks:
        task = TrackerTask(**{k: v for k, v in raw.items() if k in TrackerTask.__annotations__})
        lexical_score = similarity_score(query, task.title)
        semantic_score = 0.0
        if query_embedding is not None:
            task_embedding = _get_task_embedding(task.id, task.title)
            if task_embedding is not None:
                semantic_score = cosine_similarity(query_embedding, task_embedding)
        if lexical_score >= _LEXICAL_THRESHOLD or semantic_score >= _SEMANTIC_THRESHOLD:
            ranked.append((max(lexical_score, semantic_score), lexical_score, semantic_score, task))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return [
        task.to_dict()
        | {
            "similarity": round(score, 3),
            "lexical": round(lexical, 3),
            "semantic": round(semantic, 3) if query_embedding is not None else None,
        }
        for score, lexical, semantic, task in ranked[:limit]
    ]


def search_similar_tasks(query: str) -> list[dict]:
    """Find up to 5 open tasks that look like the same work as `query`, best match first."""
    return rank_similar_tasks(query, list_open_tasks())


def _text_property(prop_type: str | None, value: str) -> dict[str, Any] | None:
    """Build a property value for free text, or None when the column can't hold it as text."""
    if prop_type == "rich_text":
        return {"rich_text": [{"text": {"content": value}}]}
    if prop_type == "select":
        return {"select": {"name": value[:100].replace(",", " ")}}
    return None


def create_task(
    title: str,
    owner: str | None = None,
    due_date: str | None = None,
    source_meeting: str | None = None,
) -> dict:
    """Create a task in the tracker. Relative deadlines ("Friday", "EOD") become ISO dates."""
    resolved_due_date = _resolve_due_date(due_date)

    if not settings.has_notion:
        result = {
            "id": f"mock-created-{abs(hash((title, owner, due_date))) % 100000}",
            "title": title,
            "owner": owner,
            "due_date": resolved_due_date or due_date,
            "source_meeting": source_meeting,
            "status": "mock-created",
            "url": None,
            "mock": True,
        }
        _get_task_embedding(result["id"], result["title"])
        return result

    types = _property_types()
    properties: dict[str, Any] = {}
    if owner:
        owner_prop = _text_property(types.get(settings.notion_owner_property), owner)
        if owner_prop:
            properties[settings.notion_owner_property] = owner_prop
        else:
            title = f"{title} [{owner}]"
    if due_date:
        if resolved_due_date and types.get(settings.notion_due_date_property) == "date":
            properties[settings.notion_due_date_property] = {"date": {"start": resolved_due_date}}
        else:
            title = f"{title} (Due: {resolved_due_date or due_date})"
    if source_meeting:
        source_prop = _text_property(types.get(settings.notion_source_property), source_meeting)
        if source_prop:
            properties[settings.notion_source_property] = source_prop
    properties[settings.notion_title_property] = {"title": [{"text": {"content": title}}]}

    data = _notion_request(
        "pages",
        {
            "parent": {"database_id": settings.notion_database_id},
            "properties": properties,
        },
    )
    result = _page_to_task(data).to_dict()
    _get_task_embedding(result["id"], result["title"])
    return result


if FastMCP is not None:
    mcp = FastMCP("docket-tracker")
    mcp.tool()(list_open_tasks)
    mcp.tool()(search_similar_tasks)
    mcp.tool()(create_task)
else:
    mcp = None


if __name__ == "__main__":
    if os.getenv("ACTIONSYNC_DIRECT") == "1" or mcp is None:
        print(json.dumps({"open_tasks": list_open_tasks()}, indent=2))
    else:
        mcp.run()
