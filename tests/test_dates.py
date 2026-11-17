from datetime import date

import pytest

from backend.dates import find_deadline, resolve_due_date

WED = date(2026, 3, 11)  # a Wednesday


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("today", "2026-03-11"),
        ("EOD", "2026-03-11"),
        ("after this call", "2026-03-11"),
        ("tomorrow", "2026-03-12"),
        ("tomorrow-ish", "2026-03-12"),
        ("by Friday", "2026-03-13"),
        ("this week", "2026-03-13"),
        ("end of the week", "2026-03-13"),
        ("Monday", "2026-03-16"),
        ("next Monday", "2026-03-16"),
        ("next Friday", "2026-03-20"),
        ("next week", "2026-03-20"),
        ("end of month", "2026-03-31"),
        ("in two weeks", "2026-03-25"),
        ("in 3 days", "2026-03-14"),
        ("the 24th", "2026-03-24"),
        ("the 5th", "2026-04-05"),  # already past this month -> next month
        ("March 20th", "2026-03-20"),
        ("by April 2nd", "2026-04-02"),
        ("20 March", "2026-03-20"),
        ("3/27", "2026-03-27"),
        ("Q2", "2026-06-30"),
        ("2026-05-01", "2026-05-01"),
    ],
)
def test_resolves_phrases_against_meeting_date(phrase, expected):
    assert resolve_due_date(phrase, WED) == expected


@pytest.mark.parametrize("phrase", [None, "", "before the board meeting", "soon", "the Monday after"])
def test_unparseable_returns_none(phrase):
    assert resolve_due_date(phrase, WED) is None


def test_weekday_on_same_day_is_today():
    assert resolve_due_date("Wednesday", WED) == "2026-03-11"


def test_find_deadline_prefers_last_phrase():
    phrase, resolved = find_deadline("Friday is tight, let's say Tuesday", WED)
    assert (phrase.lower(), resolved) == ("tuesday", "2026-03-17")


def test_year_rollover():
    assert resolve_due_date("January 5", date(2026, 12, 20)) == "2027-01-05"
