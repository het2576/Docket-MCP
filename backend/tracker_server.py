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
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CONTEXT = getattr(ssl, "_create_unverified_context", ssl.create_default_context)()

try:
    from mcp.server.fastmcp import FastMCP
except Exception:  # pragma: no cover - lets direct scripts run before mcp install.
    FastMCP = None  # type: ignore[assignment]

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from backend.config import settings
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
    if lowered in _WEEKDAYS:
        days_ahead = (_WEEKDAYS[lowered] - today.weekday()) % 7
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

    payload = {"content": {"parts": [{"text": text}]}}
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{_EMBEDDING_MODEL}:embedContent?key={settings.gemini_api_key}"
    )
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30, context=SSL_CONTEXT) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8")
        raise RuntimeError(f"Gemini embedding API error {exc.code}: {detail}") from exc
    return data["embedding"]["values"]


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


def list_open_tasks() -> list[dict]:
    if not settings.has_notion:
        return [task.to_dict() for task in MOCK_TASKS]

    done_filters = [
        {"property": settings.notion_status_property, "status": {"does_not_equal": value}}
        for value in settings.notion_done_values
    ]
    payload = {"filter": {"and": done_filters}, "page_size": 100}
    data = _notion_request(f"databases/{settings.notion_database_id}/query", payload)
    return [_page_to_task(page).to_dict() for page in data.get("results", [])]


_LEXICAL_THRESHOLD = 0.28

# gemini-embedding-001 cosine similarities run high even between unrelated
# titles (~0.45-0.6 baseline), unlike lexical scores which sit near 0 for
# unrelated text. Reusing the lexical threshold here would surface nearly
# every task on every query, so semantic matches need their own, higher bar.
_SEMANTIC_THRESHOLD = 0.65


def search_similar_tasks(query: str) -> list[dict]:
    tasks = [TrackerTask(**task) for task in list_open_tasks()]

    try:
        query_embedding = get_embedding(query)
    except Exception as exc:
        logger.warning("Semantic search unavailable, falling back to lexical-only: %s", exc)
        query_embedding = None

    ranked = []
    for task in tasks:
        lexical_score = similarity_score(query, task.title)
        semantic_score = 0.0
        if query_embedding is not None:
            task_embedding = _get_task_embedding(task.id, task.title)
            if task_embedding is not None:
                semantic_score = cosine_similarity(query_embedding, task_embedding)
        if lexical_score >= _LEXICAL_THRESHOLD or semantic_score >= _SEMANTIC_THRESHOLD:
            ranked.append((max(lexical_score, semantic_score), task))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return [task.to_dict() | {"similarity": round(score, 3)} for score, task in ranked[:5]]


def create_task(
    title: str,
    owner: str | None = None,
    due_date: str | None = None,
    source_meeting: str | None = None,
) -> dict:
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

    properties: dict[str, Any] = {
        settings.notion_title_property: {"title": [{"text": {"content": title}}]},
    }
    if owner:
        properties[settings.notion_owner_property] = {"rich_text": [{"text": {"content": owner}}]}
    if due_date:
        if resolved_due_date:
            properties[settings.notion_due_date_property] = {"date": {"start": resolved_due_date}}
        else:
            title = f"{title} (Due: {due_date})"
            properties[settings.notion_title_property] = {"title": [{"text": {"content": title}}]}
    if source_meeting:
        properties[settings.notion_source_property] = {"rich_text": [{"text": {"content": source_meeting}}]}

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
