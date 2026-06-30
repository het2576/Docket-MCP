from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal


Decision = Literal["new", "duplicate", "needs_review"]


@dataclass
class ActionItem:
    task: str
    owner: str | None = None
    deadline: str | None = None
    confidence: float = 0.0
    source_quote: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExtractionResult:
    action_items: list[ActionItem]
    summary: str

    def to_dict(self) -> dict:
        return {
            "action_items": [item.to_dict() for item in self.action_items],
            "summary": self.summary,
        }


@dataclass
class TrackerTask:
    id: str
    title: str
    owner: str | None = None
    due_date: str | None = None
    status: str | None = None
    url: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ReviewItem:
    action_item: ActionItem
    decision: Decision
    reasoning: str
    similar_tasks: list[TrackerTask] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "action_item": self.action_item.to_dict(),
            "decision": self.decision,
            "reasoning": self.reasoning,
            "similar_tasks": [task.to_dict() for task in self.similar_tasks],
        }
