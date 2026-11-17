"""The scorer is code too: a bug here produces confident, wrong numbers."""
from evals import metrics as M

LINES = [
    "Maya: Priya, can you check the experiment screenshots before EOD?",
    "Priya: Yes, I can do that.",
    "Grace: I'll send the deck to the client and book the conference room.",
]
GOLD = [
    {"task": "Check the experiment screenshots", "keywords": ["screenshot"], "owner": "Priya", "deadline": ["2026-03-11"], "lines": [0, 1]},
    {"task": "Send the deck to the client", "keywords": ["deck"], "owner": "Grace", "deadline": [None], "lines": [2]},
    {"task": "Book the conference room", "keywords": ["room"], "owner": "Grace", "deadline": [None], "lines": [2]},
]


def pred(task, quote, owner=None):
    return {"task": task, "source_quote": quote, "owner": owner, "deadline": None, "confidence": 0.9}


def test_matches_by_evidence_line_and_disambiguates_shared_lines():
    preds = [
        pred("Book the conference room", LINES[2]),
        pred("Check experiment screenshots", LINES[1]),
        pred("Send the deck to the client", LINES[2]),
    ]
    result = M.match_items(preds, GOLD, LINES)
    assert {(m.pred, m.gold) for m in result.matches} == {(0, 2), (1, 0), (2, 1)}
    assert not result.unmatched_preds and not result.split_preds and not result.unmatched_golds


def test_second_prediction_for_same_gold_is_a_split():
    preds = [pred("Check the screenshots", LINES[0]), pred("Check screenshots today", LINES[1])]
    result = M.match_items(preds, GOLD[:1], LINES)
    assert len(result.matches) == 1 and result.split_preds == [1]


def test_unrelated_prediction_is_spurious():
    result = M.match_items([pred("Order pizza", "Nobody said this")], GOLD, LINES)
    assert result.unmatched_preds == [0] and len(result.unmatched_golds) == 3


def test_line_only_match_on_multi_task_line_needs_subject():
    # Quotes the shared line but is about neither deliverable.
    result = M.match_items([pred("Handle client stuff", LINES[2])], GOLD, LINES)
    assert not result.matches


def test_owner_matching():
    assert M.owner_matches("Priya Shah", "Priya")
    assert M.owner_matches("Design team", ["design team", "design"])
    assert M.owner_matches(None, None)
    assert not M.owner_matches("Het", None)  # guessing an owner for an unowned task is wrong
    assert not M.owner_matches(None, "Priya")


def test_deadline_matching():
    assert M.deadline_matches("2026-03-11T00:00:00", ["2026-03-11"])
    assert M.deadline_matches(None, [None, "2026-03-13"])
    assert not M.deadline_matches("Friday", ["2026-03-13"])


def test_groundedness():
    assert M.is_grounded("Yes, I can do that.", LINES)
    assert M.is_grounded("Priya: Yes, I can do that", LINES)
    assert not M.is_grounded("I will invent a quote", LINES)


def test_task_cleanliness():
    assert M.task_is_clean("Check the experiment screenshots", LINES[0])
    assert not M.task_is_clean("Can you check the screenshots?", LINES[0])
    assert not M.task_is_clean("Yes, I can do that", LINES[1])


def test_auroc():
    assert M.auroc([0.9, 0.8], [0.1, 0.2]) == 1.0
    assert M.auroc([0.5], [0.5]) == 0.5
    assert M.auroc([0.9], []) is None


def test_dedup_summary_counts_harm():
    rows = [
        {"label": "duplicate", "decision": "new", "match": "T1", "top_id": "T1", "ranked_ids": ["T1"]},
        {"label": "new", "decision": "duplicate", "match": None, "top_id": "T2", "ranked_ids": ["T2"]},
        {"label": "related", "decision": "needs_review", "match": "T3", "top_id": "T3", "ranked_ids": ["T3"]},
    ]
    s = M.summarize_dedup(rows)
    assert s["double_file_rate"] == 1.0
    assert s["silent_drop_rate"] == 1.0
    assert round(s["harmful_rate"], 3) == 0.667
    assert s["accuracy"] == 1 / 3
