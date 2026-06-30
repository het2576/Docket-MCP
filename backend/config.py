from __future__ import annotations

import os
from dataclasses import dataclass


def _csv(value: str | None, default: list[str]) -> list[str]:
    if not value:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str | None = os.getenv("GEMINI_API_KEY")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
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
