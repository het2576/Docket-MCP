"""Pure scoring functions for the Docket evals. No I/O, no model calls, unit-tested in tests/."""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}")
_SPEAKER = re.compile(r"^\s*[-*]?\s*[\w .'-]{1,40}:\s+", re.UNICODE)
_STOP = {
    "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "by", "with", "from", "into",
    "before", "after", "this", "that", "it", "its", "their", "our", "your", "is", "be",
}
_FILLER = re.compile(
    r"^(can you|could you|please|i will|i'll|i can|i'm|yes|yeah|sure|ok|okay|we need|we should|"
    r"someone should|somebody should|let's|um|uh|so)\b",
    re.IGNORECASE,
)


def norm(text: str | None) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", (text or "").lower(), flags=re.UNICODE).split())


def content_tokens(text: str) -> set[str]:
    return {t for t in norm(text).split() if t not in _STOP and len(t) > 1}


def jaccard(left: str, right: str) -> float:
    a, b = content_tokens(left), content_tokens(right)
    return len(a & b) / len(a | b) if a and b else 0.0


def locate_lines(quote: str | None, lines: list[str]) -> set[int]:
    """Map a model's source_quote back to transcript line indices (exact first, then fuzzy)."""
    q = norm(quote)
    if not q:
        return set()
    normed = [norm(line) for line in lines]
    bodies = [norm(_SPEAKER.sub("", line)) for line in lines]
    hits = {
        i
        for i, (line, body) in enumerate(zip(normed, bodies))
        if line and (q in line or (len(body) > 12 and body in q))
    }
    if hits:
        return hits
    q_tokens = set(q.split())
    best, best_score = -1, 0.6
    for i, line in enumerate(normed):
        overlap = len(q_tokens & set(line.split())) / max(len(q_tokens), 1)
        if overlap > best_score:
            best, best_score = i, overlap
    return {best} if best >= 0 else set()


def is_grounded(quote: str | None, lines: list[str]) -> bool:
    """True when the quote appears (near-)verbatim in the transcript: no invented evidence."""
    q = norm(_SPEAKER.sub("", quote or ""))
    if not q:
        return False
    transcript = norm(" ".join(lines))
    if q in transcript:
        return True
    # A quote stitched from two adjacent lines ("A: ... B: ...") is still grounded.
    parts = [norm(_SPEAKER.sub("", part)) for part in re.split(r"\s(?=[A-Z][\w .'-]{1,40}:\s)", quote or "")]
    return all(part in transcript for part in parts if part)


def keyword_score(task: str, keywords: list[str]) -> float:
    """Fraction of keyword groups ("a|b" = either) that appear as a prefix of a word in the task."""
    if not keywords:
        return 1.0
    words = norm(task)
    padded = f" {words}"
    hits = sum(any(f" {norm(alt)}" in padded for alt in group.split("|")) for group in keywords)
    return hits / len(keywords)


def task_is_clean(task: str, source_quote: str | None) -> bool:
    """An imperative work description, not a pasted conversational line."""
    body = norm(_SPEAKER.sub("", source_quote or ""))
    t = norm(task)
    return bool(t) and not _FILLER.match(task.strip()) and "?" not in task and t != body


def owner_matches(predicted: str | None, gold: str | list[str] | None) -> bool:
    pred = norm(predicted)
    if gold is None:
        return not pred
    if not pred:
        return False
    for alt in [gold] if isinstance(gold, str) else gold:
        g = norm(alt)
        if pred == g or pred.split()[0] == g.split()[0] or g in pred:
            return True
    return False


def normalize_deadline(value: str | None) -> str | None:
    if not value or not str(value).strip():
        return None
    text = str(value).strip()
    return text[:10] if _ISO.match(text) else text


def deadline_matches(predicted: str | None, gold: list[str | None]) -> bool:
    return normalize_deadline(predicted) in gold


def forbidden_hits(predicted: list[dict], patterns: list[str]) -> list[str]:
    hits = []
    for item in predicted:
        text = f"{item.get('task', '')} {item.get('owner') or ''}"
        hits += [f"{p} -> {item.get('task')!r}" for p in patterns if re.search(p, text, re.IGNORECASE)]
    return hits


@dataclass
class Match:
    pred: int
    gold: int
    score: float


@dataclass
class MatchResult:
    matches: list[Match] = field(default_factory=list)
    unmatched_preds: list[int] = field(default_factory=list)
    split_preds: list[int] = field(default_factory=list)  # a second pred for an already-matched gold
    unmatched_golds: list[int] = field(default_factory=list)


def match_items(preds: list[dict], golds: list[dict], lines: list[str]) -> MatchResult:
    """Greedy max-score alignment of predicted items to gold items.

    A pair is a candidate when the prediction quotes one of the gold item's evidence
    lines, or (ungrounded fallback) its task text hits every gold keyword. Score
    favours shared evidence, then keyword coverage, then wording overlap, which
    disambiguates several gold items that come from the same line.
    """
    candidates: list[Match] = []
    for pi, pred in enumerate(preds):
        pred_lines = locate_lines(pred.get("source_quote"), lines)
        for gi, gold in enumerate(golds):
            line_ok = bool(pred_lines & set(gold["lines"]))
            kw = keyword_score(pred.get("task", ""), gold.get("keywords", []))
            sim = jaccard(pred.get("task", ""), gold["task"])
            if line_ok or (kw == 1.0 and sim >= 0.2):
                candidates.append(Match(pi, gi, 2.0 * line_ok + kw + sim))
    candidates.sort(key=lambda m: m.score, reverse=True)

    result = MatchResult()
    used_p: set[int] = set()
    used_g: set[int] = set()
    for cand in candidates:
        if cand.pred in used_p or cand.gold in used_g:
            continue
        # A line-only match must still be about the gold's subject when that line holds several tasks.
        shared = [g for g, gold in enumerate(golds) if set(gold["lines"]) & set(golds[cand.gold]["lines"])]
        if len(shared) > 1 and keyword_score(preds[cand.pred].get("task", ""), golds[cand.gold].get("keywords", [])) == 0:
            continue
        used_p.add(cand.pred)
        used_g.add(cand.gold)
        result.matches.append(cand)

    candidate_preds = {c.pred for c in candidates}
    for pi in range(len(preds)):
        if pi in used_p:
            continue
        (result.split_preds if pi in candidate_preds else result.unmatched_preds).append(pi)
    result.unmatched_golds = [gi for gi in range(len(golds)) if gi not in used_g]
    return result


def prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def auroc(positives: list[float], negatives: list[float]) -> float | None:
    """Probability a random correct item has higher confidence than a random wrong one."""
    if not positives or not negatives:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in positives for n in negatives)
    return wins / (len(positives) * len(negatives))


def expected_decision(gold: dict) -> str:
    if gold.get("existing"):
        return "duplicate"
    if gold.get("vague"):
        return "needs_review"
    return "new"


# How bad each (expected, predicted) routing outcome is for the user.
DECISION_OUTCOME = {
    ("duplicate", "duplicate"): "correct",
    ("duplicate", "needs_review"): "safe",          # flagged, a human catches it
    ("duplicate", "new"): "double_filed",           # harm: duplicate ticket pre-checked
    ("new", "new"): "correct",
    ("new", "needs_review"): "friction",            # an extra click
    ("new", "duplicate"): "dropped",                # harm: real work silently unchecked
    ("needs_review", "needs_review"): "correct",
    ("needs_review", "new"): "auto_filed_vague",    # harm: ownerless task pre-checked
    ("needs_review", "duplicate"): "safe",
}
HARMFUL = {"double_filed", "dropped", "auto_filed_vague"}


def dedup_outcome(label: str, decision: str) -> str:
    expected = {"duplicate": "duplicate", "related": "needs_review", "new": "new"}[label]
    if expected == decision:
        return "correct"
    return {
        ("duplicate", "needs_review"): "safe",
        ("duplicate", "new"): "double_filed",
        ("related", "new"): "missed_context",
        ("related", "duplicate"): "dropped",
        ("new", "needs_review"): "friction",
        ("new", "duplicate"): "dropped",
    }[(label, decision)]


def summarize_dedup(rows: list[dict]) -> dict[str, Any]:
    """rows: {label, decision, top_id, match, ranked_ids}"""
    labels = ["duplicate", "related", "new"]
    decisions = {"duplicate": "duplicate", "related": "needs_review", "new": "new"}
    confusion = {lab: Counter(r["decision"] for r in rows if r["label"] == lab) for lab in labels}
    outcomes = Counter(dedup_outcome(r["label"], r["decision"]) for r in rows)
    n = len(rows) or 1
    per_class = {}
    for lab in labels:
        dec = decisions[lab]
        tp = sum(r["label"] == lab and r["decision"] == dec for r in rows)
        fp = sum(r["label"] != lab and r["decision"] == dec for r in rows)
        fn = sum(r["label"] == lab and r["decision"] != dec for r in rows)
        per_class[lab] = prf(tp, fp, fn)
    with_match = [r for r in rows if r.get("match")]
    dups = [r for r in rows if r["label"] == "duplicate"]
    news = [r for r in rows if r["label"] == "new"]
    return {
        "n": len(rows),
        "accuracy": outcomes["correct"] / n,
        "macro_f1": sum(c["f1"] for c in per_class.values()) / len(per_class),
        "duplicate_precision": per_class["duplicate"]["precision"],
        "duplicate_recall": per_class["duplicate"]["recall"],
        "duplicate_f1": per_class["duplicate"]["f1"],
        "flag_recall": sum(r["decision"] != "new" for r in dups) / (len(dups) or 1),
        "double_file_rate": outcomes["double_filed"] / (len(dups) or 1),
        "silent_drop_rate": sum(r["decision"] == "duplicate" for r in news) / (len(news) or 1),
        "harmful_rate": (outcomes["double_filed"] + outcomes["dropped"]) / n,
        "retrieval_recall_at_1": sum(r.get("top_id") == r["match"] for r in with_match) / (len(with_match) or 1),
        "retrieval_recall_at_5": sum(r["match"] in r.get("ranked_ids", []) for r in with_match) / (len(with_match) or 1),
        "confusion": {lab: dict(confusion[lab]) for lab in labels},
        "outcomes": dict(outcomes),
    }


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct
    lo, hi = math.floor(k), math.ceil(k)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def paired_bootstrap(
    before: list[dict], after: list[dict], summarize, key: str, n: int = 2000, seed: int = 7
) -> tuple[float, float] | None:
    """95% interval for after[key] - before[key], resampling the same cases for both runs.

    `before` and `after` must be aligned (same case at the same index). Small eval sets make
    point estimates noisy; a change whose interval includes 0 isn't distinguishable from luck.
    """
    import random

    if not before or len(before) != len(after):
        return None
    rng = random.Random(seed)
    size = len(before)
    deltas = []
    for _ in range(n):
        idx = [rng.randrange(size) for _ in range(size)]
        b = summarize([before[i] for i in idx]).get(key)
        a = summarize([after[i] for i in idx]).get(key)
        if a is not None and b is not None:
            deltas.append(a - b)
    if not deltas:
        return None
    deltas.sort()
    return deltas[int(0.025 * len(deltas))], deltas[int(0.975 * len(deltas)) - 1]
