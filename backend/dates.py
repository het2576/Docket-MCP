"""Deterministic deadline resolution, anchored to the meeting date (not the day you file).

LLMs are unreliable at calendar arithmetic ("this week" from a Wednesday came back
as a Sunday in evals), so extraction asks the model for the verbatim deadline
phrase and resolves it here. The model's own ISO date is only a fallback for
phrases this module doesn't understand.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

_WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}
_MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
_MONTHS |= {name.lower(): i for i, name in enumerate(calendar.month_abbr) if name}
_MONTHS["sept"] = 9
_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "couple of": 2, "few": 3}
_LEAD = re.compile(
    r"^(?:by|before|due|on|until|no later than|for|at|around|aiming for|ideally|latest|at the latest|"
    r"sometime|some time|the)\s+",
    re.IGNORECASE,
)


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def _friday_of(day: date) -> date:
    return day + timedelta(days=4 - day.weekday()) if day.weekday() <= 4 else day + timedelta(days=11 - day.weekday())


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _upcoming(year: int, month: int, day: int, today: date) -> date | None:
    """A month/day with no year: this year, unless that's well in the past."""
    try:
        candidate = date(year, month, day)
    except ValueError:
        return None
    if candidate < today - timedelta(days=60):
        try:
            candidate = date(year + 1, month, day)
        except ValueError:
            return None
    return candidate


def _resolve(text: str, today: date) -> date | None:
    t = re.sub(r"[.,!?;:]+$", "", text.strip().lower())
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"-ish$|ish$", "", t).strip()
    for _ in range(3):
        t = _LEAD.sub("", t).strip()

    if iso := parse_iso_date(t):
        return iso
    if t in ("today", "tonight", "eod", "end of day", "end of the day", "end of today", "this afternoon",
             "this morning", "this evening", "after this call", "after the call", "right after this", "asap today"):
        return today
    if t.startswith("tomorrow") or t in ("eod tomorrow", "end of day tomorrow"):
        return today + timedelta(days=1)
    if t in ("day after tomorrow", "the day after tomorrow"):
        return today + timedelta(days=2)
    if t in ("this week", "end of week", "end of the week", "eow", "the end of the week", "end of this week",
             "later this week", "friday this week"):
        return _friday_of(today)
    if t in ("next week", "end of next week"):
        return _friday_of(today + timedelta(days=7))
    if t == "early next week":
        return today + timedelta(days=7 - today.weekday())
    if t in ("end of month", "end of the month", "eom", "this month", "end of this month", "month end", "month-end"):
        return _month_end(today.year, today.month)
    if t in ("next month", "end of next month"):
        year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        return _month_end(year, month)
    if t in ("end of quarter", "end of the quarter", "this quarter", "eoq"):
        q_end_month = ((today.month - 1) // 3 + 1) * 3
        return _month_end(today.year, q_end_month)
    if m := re.fullmatch(r"q([1-4])", t):
        return _month_end(today.year, int(m.group(1)) * 3)

    if m := re.fullmatch(r"in (\d+|a|an|one|two|three|four|five|six|couple of|few) (day|days|week|weeks)", t):
        n = int(m.group(1)) if m.group(1).isdigit() else _NUMBERS[m.group(1)]
        return today + timedelta(days=n * (7 if m.group(2).startswith("week") else 1))

    if m := re.fullmatch(r"(this |next |coming |this coming )?([a-z]+)( this week| next week)?( morning| afternoon| eod)?", t):
        prefix, name, suffix = (m.group(1) or "").strip(), m.group(2), (m.group(3) or "").strip()
        if name in _WEEKDAYS:
            target = _WEEKDAYS[name]
            if prefix == "next" or suffix == "next week":
                # "next Friday" = Friday of the following week.
                monday_next = today + timedelta(days=7 - today.weekday())
                return monday_next + timedelta(days=target)
            days_ahead = (target - today.weekday()) % 7
            return today + timedelta(days=days_ahead)

    # "the 24th", "24th"
    if m := re.fullmatch(r"(\d{1,2})(st|nd|rd|th)?", t):
        day = int(m.group(1))
        if 1 <= day <= 31:
            candidate = _upcoming(today.year, today.month, day, today) if day >= today.day else None
            if candidate is None or candidate < today:
                year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
                candidate = _upcoming(year, month, day, today)
            return candidate
    # "march 20th", "mar 20", "march 20, 2026"
    if m := re.fullmatch(r"([a-z]+) (\d{1,2})(st|nd|rd|th)?(,? (\d{4}))?", t):
        if m.group(1) in _MONTHS:
            if m.group(5):
                try:
                    return date(int(m.group(5)), _MONTHS[m.group(1)], int(m.group(2)))
                except ValueError:
                    return None
            return _upcoming(today.year, _MONTHS[m.group(1)], int(m.group(2)), today)
    # "20 march", "20th of march"
    if m := re.fullmatch(r"(\d{1,2})(st|nd|rd|th)?( of)? ([a-z]+)", t):
        if m.group(4) in _MONTHS:
            return _upcoming(today.year, _MONTHS[m.group(4)], int(m.group(1)), today)
    # "3/27" or "3/27/2026" (US month/day)
    if m := re.fullmatch(r"(\d{1,2})/(\d{1,2})(/(\d{2,4}))?", t):
        month, day = int(m.group(1)), int(m.group(2))
        if m.group(4):
            year = int(m.group(4)) + (2000 if len(m.group(4)) == 2 else 0)
            try:
                return date(year, month, day)
            except ValueError:
                return None
        if 1 <= month <= 12:
            return _upcoming(today.year, month, day, today)
    return None


def resolve_due_date(due_date: str | None, today: date | None = None) -> str | None:
    """Turn a deadline phrase ("Friday", "end of month", "the 24th", "3/27") into YYYY-MM-DD, or None."""
    if not due_date or not str(due_date).strip():
        return None
    resolved = _resolve(str(due_date), today or date.today())
    return resolved.isoformat() if resolved else None


# Phrases the rule-based extractor looks for inside a transcript line, longest first.
DEADLINE_PATTERN = re.compile(
    r"\b(?:by|before|due|on|until|for|aiming for|no later than)?\s*("
    r"(?:the )?day after tomorrow|tomorrow(?:-ish)?|today|tonight|eod|end of (?:the )?(?:day|week|month|quarter)|"
    r"end of next (?:week|month)|this week|next week|next month|after this call|"
    r"in (?:\d+|a|an|one|two|three|four|five|six|a couple of|a few) (?:days?|weeks?)|"
    r"(?:(?:this|next|coming) )?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"(?:january|february|march|april|may|june|july|august|september|october|november|december) \d{1,2}(?:st|nd|rd|th)?|"
    r"the \d{1,2}(?:st|nd|rd|th)|\d{1,2}/\d{1,2}(?:/\d{2,4})?"
    r")\b",
    re.IGNORECASE,
)


def find_deadline(text: str, today: date) -> tuple[str | None, str | None]:
    """Last deadline phrase in `text` and its resolved date (later phrases tend to be the agreed one)."""
    matches = [m.group(1) for m in DEADLINE_PATTERN.finditer(text)]
    for phrase in reversed(matches):
        resolved = resolve_due_date(phrase, today)
        if resolved:
            return phrase, resolved
    return None, None
