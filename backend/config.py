from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_env_files() -> None:
    """Read .env.local / .env so the CLI and MCP server see the same config as Next.js.

    Real environment variables always win, matching Next.js precedence.
    """
    for name in (".env.local", ".env"):
        path = ROOT / name
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and not key.startswith("http"):
                os.environ.setdefault(key, value)


_load_env_files()


def _csv(value: str | None, default: list[str]) -> list[str]:
    if not value:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str | None = os.getenv("GEMINI_API_KEY")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
    # Gemini 3 models think at "high" by default, which can take minutes for a transcript.
    gemini_thinking_level: str = os.getenv("GEMINI_THINKING_LEVEL", "low")
    notion_api_key: str | None = os.getenv("NOTION_API_KEY")
    notion_database_id: str | None = os.getenv("NOTION_DATABASE_ID")
    notion_title_property: str = os.getenv("NOTION_TITLE_PROPERTY", "Name")
    notion_owner_property: str = os.getenv("NOTION_OWNER_PROPERTY", "Owner")
    notion_due_date_property: str = os.getenv("NOTION_DUE_DATE_PROPERTY", "Due Date")
    notion_status_property: str = os.getenv("NOTION_STATUS_PROPERTY", "Status")
    notion_source_property: str = os.getenv("NOTION_SOURCE_PROPERTY", "Source Meeting")
    notion_done_values: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "notion_done_values",
            _csv(os.getenv("NOTION_STATUS_DONE_VALUES"), ["Done", "Complete", "Completed", "Archived"]),
        )

    @property
    def has_gemini(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def has_notion(self) -> bool:
        return bool(self.notion_api_key and self.notion_database_id)


settings = Settings()
