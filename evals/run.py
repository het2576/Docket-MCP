"""Run the Docket evals.

    python -m evals.run                         # all suites, Gemini, cached responses
    python -m evals.run --engine heuristic      # the no-API-key fallback path
    python -m evals.run --split test --label v2 # held-out split only, saved as results/v2.json
    python -m evals.run --no-cache --repeats 3  # fresh model calls, report run-to-run variance
    python -m evals.run --gate                  # exit 1 if any metric drops below evals/thresholds.json

Evals never touch a real tracker: Notion credentials are blanked before the backend
is imported, and the agent's create_task is replaced with a function that raises.
"""
from __future__ import annotations

import os
import sys

os.environ["NOTION_API_KEY"] = ""
os.environ["NOTION_DATABASE_ID"] = ""

import argparse  # noqa: E402
import hashlib  # noqa: E402
import itertools  # noqa: E402
import json  # noqa: E402
import statistics  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from collections import Counter, defaultdict  # noqa: E402
from concurrent.futures import ThreadPoolExecutor  # noqa: E402
from datetime import date, datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import agent, llm  # noqa: E402
from backend import tracker_server as ts  # noqa: E402
from backend.config import settings  # noqa: E402
from backend.schema import ActionItem  # noqa: E402
from evals import metrics as M  # noqa: E402

DATA = ROOT / "evals" / "data"
RESULTS = ROOT / "evals" / "results"
CACHE = ROOT / "evals" / ".cache"
RUNS = ROOT / "evals" / ".runs"


# --------------------------------------------------------------------------- LLM metering + cache
class Meter:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.live_calls: Counter[str] = Counter()
        self.cache_hits = 0
        self.tokens: Counter[str] = Counter()
        self.local = threading.local()

    def hit(self) -> None:
        with self.lock:
            self.cache_hits += 1

    def record(self, path: str, usage: dict | None) -> None:
        kind = path.rsplit(":", 1)[-1]
        with self.lock:
            self.live_calls[kind] += 1
            for key in ("promptTokenCount", "candidatesTokenCount", "thoughtsTokenCount"):
                self.tokens[key] += int((usage or {}).get(key) or 0)
        self.local.live = getattr(self.local, "live", 0) + 1


_real_post = llm.gemini_post


def install_llm_cache(meter: Meter, use_cache: bool, salt: str) -> None:
    def post(path: str, payload: dict, timeout: int = 30) -> dict:
        key = hashlib.sha256(json.dumps([path, payload, salt], sort_keys=True).encode()).hexdigest()
        file = CACHE / "llm" / key[:2] / f"{key}.json"
        if use_cache and file.exists():
            meter.hit()
            return json.loads(file.read_text())
        data = _real_post(path, payload, timeout)
        meter.record(path, data.get("usageMetadata"))
        file.parent.mkdir(parents=True, exist_ok=True)
        tmp = file.with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, file)
        return data

    llm.gemini_post = post


# --------------------------------------------------------------------------- environment
def _forbid_create(*_args, **_kwargs):
    raise AssertionError("Evals must never create tracker tasks.")


def configure(engine: str, judge: bool | None, tracker: list[dict]) -> None:
    if engine == "heuristic":
        object.__setattr__(settings, "gemini_api_key", None)
    elif not settings.has_gemini:
        sys.exit("--engine gemini needs GEMINI_API_KEY (in .env.local or the environment).")
    if judge is not None and hasattr(settings, "dedup_judge"):
        object.__setattr__(settings, "dedup_judge", judge)
    agent.list_open_tasks = lambda: [dict(task) for task in tracker]
    agent.create_task = _forbid_create
    agent.update_task = _forbid_create
    ts.create_task = _forbid_create
    ts.update_task = _forbid_create
    agent.LOG_DIR = RUNS / "traces"
    ts._EMBEDDING_CACHE_PATH = CACHE / "embeddings.json"
    ts._embedding_cache = None
    CACHE.mkdir(parents=True, exist_ok=True)
    if settings.has_gemini:
        ts.warm_task_embeddings(tracker)  # once, up front, so worker threads only read the cache


# --------------------------------------------------------------------------- extraction + routing suite
def score_case(case: dict, result: dict, seconds: float, live_calls: int) -> dict:
    lines, golds = case["lines"], case["gold"]
    reviews = result["review_items"]
    preds = [r["action_item"] for r in reviews]
    decisions = [r["decision"] for r in reviews]
    matched = M.match_items(preds, golds, lines)

    items = []
    for m in matched.matches:
        gold, pred, review = golds[m.gold], preds[m.pred], reviews[m.pred]
        expected = M.expected_decision(gold)
        top = (review.get("similar_tasks") or [{}])[0]
        items.append(
            {
                "gold": m.gold,
                "pred": m.pred,
                "optional": bool(gold.get("optional")),
                "gold_task": gold["task"],
                "pred_task": pred["task"],
                "owner": pred.get("owner"),
                "gold_owner": gold.get("owner"),
                "owner_ok": M.owner_matches(pred.get("owner"), gold.get("owner")),
                "deadline": pred.get("deadline"),
                "gold_deadline": gold["deadline"],
                "deadline_ok": M.deadline_matches(pred.get("deadline"), gold["deadline"]),
                "keywords": M.keyword_score(pred["task"], gold.get("keywords", [])),
                "clean": M.task_is_clean(pred["task"], pred.get("source_quote")),
                "decision": decisions[m.pred],
                "expected_decision": expected,
                "outcome": M.DECISION_OUTCOME[(expected, decisions[m.pred])],
                "existing_match_ok": (top.get("id") == gold["existing"]) if gold.get("existing") else None,
            }
        )

    required = [i for i, g in enumerate(golds) if not g.get("optional")]
    req_items = [it for it in items if not it["optional"]]
    fp_preds = matched.unmatched_preds + matched.split_preds
    missed = [i for i in matched.unmatched_golds if i in required]
    forbidden = M.forbidden_hits(preds, case.get("forbidden", []))

    # One-click approval: everything pre-checked is real new work with the right fields, and nothing is missing.
    good_new = {it["pred"] for it in items if it["expected_decision"] == "new" and it["owner_ok"] and it["deadline_ok"]}
    checked = {i for i, d in enumerate(decisions) if d == "new"}
    must_check = {it["pred"] for it in req_items if it["expected_decision"] == "new"}
    req_new = [i for i in required if M.expected_decision(golds[i]) == "new"]
    zero_touch = (
        checked <= good_new
        and must_check <= checked
        and len(must_check) == len(req_new)
        and not forbidden
    )

    return {
        "id": case["id"],
        "split": case["split"],
        "tags": case["tags"],
        "tp": len(req_items),
        "fp": len(fp_preds),
        "fn": len(missed),
        "items": items,
        "false_positives": [
            {"task": preds[i]["task"], "owner": preds[i].get("owner"), "quote": preds[i].get("source_quote"),
             "decision": decisions[i], "kind": "split" if i in matched.split_preds else "spurious"}
            for i in fp_preds
        ],
        "missed": [golds[i]["task"] for i in missed],
        "forbidden_hits": forbidden,
        "grounded": [M.is_grounded(p.get("source_quote"), lines) for p in preds],
        "confidences": [
            {"confidence": p.get("confidence", 0.0), "correct": i not in fp_preds} for i, p in enumerate(preds)
        ],
        "zero_touch": zero_touch,
        "seconds": round(seconds, 3),
        "live_calls": live_calls,
        "warnings": result.get("warnings", []),
        "engine": result.get("engine"),
    }


def summarize_extraction(cases: list[dict]) -> dict:
    tp, fp, fn = (sum(c[k] for c in cases) for k in ("tp", "fp", "fn"))
    req = [it for c in cases for it in c["items"] if not it["optional"]]
    allm = [it for c in cases for it in c["items"]]
    conf = [x for c in cases for x in c["confidences"]]
    fps = [x for x in conf if not x["correct"]]
    outcomes = Counter(it["outcome"] for it in req)
    negatives = [c for c in cases if "negative" in c["tags"]]
    injection = [c for c in cases if "prompt_injection" in c["tags"]]
    forbidden_cases = [c for c in cases if c["forbidden_hits"]]
    grounded = [g for c in cases for g in c["grounded"]]
    existing = [it for it in req if it["existing_match_ok"] is not None]
    live = [c["seconds"] for c in cases if c["live_calls"]]

    by_tag: dict[str, dict] = {}
    tags = sorted({t for c in cases for t in c["tags"]})
    for tag in tags:
        sub = [c for c in cases if tag in c["tags"]]
        s = M.prf(sum(c["tp"] for c in sub), sum(c["fp"] for c in sub), sum(c["fn"] for c in sub))
        s["cases"] = len(sub)
        s["zero_touch"] = sum(c["zero_touch"] for c in sub) / len(sub)
        by_tag[tag] = s

    def rate(values):
        values = list(values)
        return sum(values) / len(values) if values else None

    return {
        "cases": len(cases),
        **M.prf(tp, fp, fn),
        "tp": tp, "fp": fp, "fn": fn,
        "owner_accuracy": rate(it["owner_ok"] for it in req),
        "deadline_accuracy": rate(it["deadline_ok"] for it in req),
        "strict_item_recall": sum(it["owner_ok"] and it["deadline_ok"] for it in req) / max(tp + fn, 1),
        "task_keyword_coverage": rate(it["keywords"] == 1.0 for it in allm),
        "task_clean_rate": rate(it["clean"] for it in allm),
        "groundedness": rate(grounded),
        "negative_case_pass_rate": rate(c["fp"] == 0 for c in negatives),
        "injection_resistance": rate(not c["forbidden_hits"] for c in injection),
        "forbidden_violations": sum(len(c["forbidden_hits"]) for c in cases),
        "forbidden_cases": [c["id"] for c in forbidden_cases],
        "routing_accuracy": rate(it["outcome"] == "correct" for it in req),
        "harmful_routing_rate": rate(it["outcome"] in M.HARMFUL for it in req),
        "existing_task_identified": rate(it["existing_match_ok"] for it in existing),
        "routing_outcomes": dict(outcomes),
        "zero_touch_rate": rate(c["zero_touch"] for c in cases),
        "confidence_auroc": M.auroc([x["confidence"] for x in conf if x["correct"]], [x["confidence"] for x in fps]),
        "false_positives_below_0_6": rate(x["confidence"] < 0.6 for x in fps),
        "latency_p50_s": M.percentile(live, 0.5),
        "latency_p95_s": M.percentile(live, 0.95),
        "by_tag": by_tag,
    }


def run_extraction(cases: list[dict], workers: int, meter: Meter) -> list[dict]:
    wanted = "gemini" if settings.has_gemini else "heuristic"

    def one(case: dict) -> dict:
        meter.local.live = 0
        start = time.perf_counter()
        result = agent.run_agent(
            "\n".join(case["lines"]),
            source_meeting=case["id"],
            meeting_date=date.fromisoformat(case["meeting_date"]),
        )
        scored = score_case(case, result, time.perf_counter() - start, meter.local.live)
        # The agent falls back to the rule-based extractor on API errors. Scoring that as
        # "Gemini" would silently blend two systems, so such cases are excluded and reported.
        if result["engine"]["extraction"] != wanted:
            scored["error"] = (result.get("warnings") or ["extractor fell back"])[0]
        return scored

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, cases))


# --------------------------------------------------------------------------- duplicate-detection suite
def run_dedup(cases: list[dict], tracker: list[dict]) -> list[dict]:
    queries = [c["query"] for c in cases]
    embeddings: list = [None] * len(cases)
    if settings.has_gemini:
        embeddings = ts.get_embeddings(queries)
    items = [ActionItem(task=q, confidence=1.0) for q in queries]
    ranked = [ts.rank_similar_tasks(q, tracker, query_embedding=e) for q, e in zip(queries, embeddings)]
    routes = []
    for start in range(0, len(items), 10):  # meeting-sized batches, as the product sends them
        routes += agent.route_items(items[start:start + 10], ranked[start:start + 10], [])

    rows = []
    for case, emb, similar, (decision, reasoning, judge) in zip(cases, embeddings, ranked, routes):
        all_scores = []
        for task in tracker:
            sem = None
            if emb is not None:
                vec = ts._get_task_embedding(task["id"], task["title"])
                sem = round(ts.cosine_similarity(emb, vec), 4) if vec else None
            all_scores.append([task["id"], round(ts.similarity_score(case["query"], task["title"]), 4), sem])
        rows.append(
            case
            | {
                "decision": decision,
                "reasoning": reasoning,
                "judge": judge,
                "top_id": similar[0]["id"] if similar else None,
                "top_title": similar[0]["title"] if similar else None,
                "ranked_ids": [s["id"] for s in similar],
                "scores": all_scores,
                "outcome": M.dedup_outcome(case["label"], decision),
            }
        )
    return rows


def _band_decision(scores: list, ld: float, lr: float, sd: float, sr: float) -> str:
    """Re-implements the threshold router on stored scores, for the calibration sweep."""
    cands = [
        (max(lex, sem or 0.0), lex, sem or 0.0)
        for _id, lex, sem in scores
        if lex >= ts._LEXICAL_THRESHOLD or (sem or 0.0) >= ts._SEMANTIC_THRESHOLD
    ]
    if not cands:
        return "new"
    _, lex, sem = max(cands)
    if lex >= ld or sem >= sd:
        return "duplicate"
    if lex >= lr or sem >= sr:
        return "needs_review"
    return "new"


def sweep_thresholds(rows: list[dict]) -> dict:
    """Pick thresholds on dev (fewest harmful errors, then accuracy), report them on test."""
    dev = [r for r in rows if r["split"] == "dev"]
    test = [r for r in rows if r["split"] == "test"]
    if not dev or rows[0]["scores"][0][2] is None:
        return {}
    grid = itertools.product(
        [x / 100 for x in range(60, 100, 5)],
        [x / 100 for x in range(35, 70, 5)],
        [x / 100 for x in range(76, 93)],
        [x / 100 for x in range(62, 82, 2)],
    )

    def evaluate(subset, params):
        decided = [r | {"decision": _band_decision(r["scores"], *params)} for r in subset]
        return M.summarize_dedup(decided)

    best = None
    for params in grid:
        ld, lr, sd, sr = params
        if lr >= ld or sr >= sd:
            continue
        s = evaluate(dev, params)
        key = (-s["harmful_rate"], s["accuracy"], s["macro_f1"])
        if best is None or key > best[0]:
            best = (key, params, s)
    _, params, dev_summary = best
    current = (ts.LEXICAL_DUPLICATE, ts.LEXICAL_REVIEW, ts.SEMANTIC_DUPLICATE, ts.SEMANTIC_REVIEW)
    return {
        "best": dict(zip(["lexical_duplicate", "lexical_review", "semantic_duplicate", "semantic_review"], params)),
        "current": dict(zip(["lexical_duplicate", "lexical_review", "semantic_duplicate", "semantic_review"], current)),
        "dev_best": {k: dev_summary[k] for k in ("accuracy", "harmful_rate", "macro_f1")},
        "test_best": {k: v for k, v in evaluate(test, params).items() if k in ("accuracy", "harmful_rate", "macro_f1")},
        "test_current": {k: v for k, v in evaluate(test, current).items() if k in ("accuracy", "harmful_rate", "macro_f1")},
    }


# --------------------------------------------------------------------------- reporting
HEADLINE = [
    ("f1", "Extraction F1"), ("precision", "Precision"), ("recall", "Recall"),
    ("owner_accuracy", "Owner accuracy"), ("deadline_accuracy", "Deadline accuracy"),
    ("strict_item_recall", "Strict item recall"), ("task_keyword_coverage", "Task text coverage"),
    ("task_clean_rate", "Task text clean"), ("groundedness", "Quote groundedness"),
    ("negative_case_pass_rate", "No-action meetings clean"), ("injection_resistance", "Injection resistance"),
    ("routing_accuracy", "Routing accuracy"), ("harmful_routing_rate", "Harmful routing rate"),
    ("zero_touch_rate", "One-click approval rate"), ("confidence_auroc", "Confidence AUROC"),
]
DEDUP_HEADLINE = [
    ("accuracy", "3-way accuracy"), ("macro_f1", "Macro F1"), ("duplicate_precision", "Duplicate precision"),
    ("duplicate_recall", "Duplicate recall"), ("flag_recall", "Duplicates flagged (dup or review)"),
    ("double_file_rate", "Double-file rate"), ("silent_drop_rate", "Silent-drop rate"),
    ("harmful_rate", "Harmful error rate"), ("retrieval_recall_at_1", "Right task ranked #1"),
]


def fmt(value) -> str:
    if value is None:
        return "–"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def print_report(result: dict) -> None:
    meta = result["meta"]
    print(f"\n## Docket evals: {meta['label']} ({meta['engine']}, split={meta['split']})\n")
    if "extraction" in result:
        ex = result["extraction"]
        for split, s in ex["summary"].items():
            print(f"### Extraction + routing [{split}] ({s['cases']} transcripts, tp={s['tp']} fp={s['fp']} fn={s['fn']})")
            for key, label in HEADLINE:
                print(f"  {label:<28} {fmt(s.get(key))}")
            if ex.get("variance"):
                v = ex["variance"].get(split, {})
                print("  run-to-run std:", ", ".join(f"{k}={fmt(x)}" for k, x in v.items()))
            print(f"  latency p50/p95 (s)          {fmt(s['latency_p50_s'])} / {fmt(s['latency_p95_s'])}")
        print("\n  Failures:")
        for c in ex["cases"]:
            problems = [f"FP {fp['kind']}: {fp['task']!r}" for fp in c["false_positives"]]
            problems += [f"MISSED: {m!r}" for m in c["missed"]]
            problems += [f"FORBIDDEN: {h}" for h in c["forbidden_hits"]]
            for it in c["items"]:
                if not it["owner_ok"]:
                    problems.append(f"owner {it['owner']!r} != {it['gold_owner']!r} ({it['gold_task']})")
                if not it["deadline_ok"]:
                    problems.append(f"deadline {it['deadline']!r} not in {it['gold_deadline']} ({it['gold_task']})")
                if not it["optional"] and it["outcome"] != "correct":
                    problems.append(f"routing {it['decision']} (want {it['expected_decision']}): {it['outcome']} ({it['gold_task']})")
            if problems:
                print(f"   {c['id']} [{c['split']}]")
                for p in problems:
                    print(f"      - {p}")
    if "dedup" in result:
        dd = result["dedup"]
        for split, s in dd["summary"].items():
            print(f"\n### Duplicate detection [{split}] ({s['n']} pairs)")
            for key, label in DEDUP_HEADLINE:
                print(f"  {label:<34} {fmt(s.get(key))}")
            print(f"  confusion (gold → decision)       {s['confusion']}")
        if dd.get("sweep"):
            print(f"\n  Threshold sweep: {json.dumps(dd['sweep'])}")
        wrong = [r for r in dd["cases"] if r["outcome"] != "correct"]
        print(f"\n  Dedup errors ({len(wrong)}):")
        for r in wrong:
            print(f"   {r['id']} [{r['split']}] {r['label']}→{r['decision']} ({r['outcome']}): {r['query']!r} top={r['top_title']!r}")
    m = meta["llm"]
    print(f"\nLLM calls: live={m['live_calls']} cache_hits={m['cache_hits']} tokens={m['tokens']}")


def by_split(rows: list[dict], summarize) -> dict:
    out = {"all": summarize(rows)}
    for split in ("dev", "test", "holdout"):
        sub = [r for r in rows if r["split"] == split]
        if sub and len(sub) != len(rows):
            out[split] = summarize(sub)
    return out


def check_gate(result: dict, engine: str) -> list[str]:
    spec = json.loads((ROOT / "evals" / "thresholds.json").read_text()).get(engine, {})
    failures = []
    for suite, rules in spec.items():
        summary = result.get(suite, {}).get("summary", {}).get("all")
        if not summary:
            continue
        for key, rule in rules.items():
            value = summary.get(key)
            if value is None:
                continue
            if "min" in rule and value < rule["min"]:
                failures.append(f"{suite}.{key}={value:.3f} < min {rule['min']}")
            if "max" in rule and value > rule["max"]:
                failures.append(f"{suite}.{key}={value:.3f} > max {rule['max']}")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--suite", choices=["extraction", "dedup", "all"], default="all")
    parser.add_argument("--engine", choices=["gemini", "heuristic"], default="gemini")
    parser.add_argument("--split", choices=["dev", "test", "holdout", "all"], default="all")
    parser.add_argument("--judge", choices=["on", "off", "default"], default="default",
                        help="LLM duplicate adjudication (default: the product setting)")
    parser.add_argument("--label", default=None, help="save results to evals/results/<label>.json")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--only", nargs="*", help="case ids to run")
    parser.add_argument("--model", default=None, help="override GEMINI_MODEL for this run")
    parser.add_argument("--judge-model", default=None, help="duplicate-judge model (default: same as --model)")
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    tracker = json.loads((DATA / "tracker.json").read_text())
    if args.model:
        object.__setattr__(settings, "gemini_model", args.model)
    object.__setattr__(settings, "gemini_judge_model", args.judge_model or settings.gemini_model)
    # Evals measure quality, not the web request's time budget (rate-limit waits would trip it).
    object.__setattr__(settings, "run_budget_s", 0)
    judge = {"on": True, "off": False, "default": None}[args.judge]
    configure(args.engine, judge, tracker)
    meter = Meter()

    def pick(cases):
        cases = [c for c in cases if args.split == "all" or c["split"] == args.split]
        return [c for c in cases if not args.only or c["id"] in args.only]

    started = time.time()
    result: dict = {}
    if args.suite in ("extraction", "all"):
        cases = pick(json.loads((DATA / "extraction.json").read_text()))
        runs, errors = [], []
        for rep in range(args.repeats):
            install_llm_cache(meter, not args.no_cache, salt="" if rep == 0 else f"repeat-{rep}")
            scored = run_extraction(cases, args.workers, meter)
            errors += [c for c in scored if c.get("error")]
            runs.append([c for c in scored if not c.get("error")])
        result["extraction"] = {
            "summary": by_split(runs[0], summarize_extraction),
            "cases": runs[0],
            "errors": [{"id": c["id"], "error": c["error"]} for c in errors],
        }
        if args.repeats > 1 and all(runs):
            per_run = [by_split(r, summarize_extraction) for r in runs]
            keys = ["f1", "owner_accuracy", "deadline_accuracy", "zero_touch_rate", "routing_accuracy", "harmful_routing_rate"]
            result["extraction"]["variance"] = {
                split: {k: statistics.pstdev([p[split][k] or 0 for p in per_run]) for k in keys}
                for split in per_run[0]
            }
            result["extraction"]["repeat_summaries"] = [
                {split: {k: s[k] for k in keys} for split, s in p.items()} for p in per_run
            ]
    if args.suite in ("dedup", "all"):
        dedup_runs = []
        for rep in range(args.repeats):
            install_llm_cache(meter, not args.no_cache, salt="" if rep == 0 else f"repeat-{rep}")
            dedup_runs.append(run_dedup(pick(json.loads((DATA / "dedup.json").read_text())), tracker))
        rows = dedup_runs[0]
        result["dedup"] = {"summary": by_split(rows, M.summarize_dedup), "cases": rows, "sweep": sweep_thresholds(rows)}
        if args.repeats > 1:
            per_run = [by_split(r, M.summarize_dedup) for r in dedup_runs]
            keys = ["accuracy", "duplicate_precision", "duplicate_recall", "harmful_rate"]
            result["dedup"]["repeat_summaries"] = [{split: {k: s[k] for k in keys} for split, s in p.items()} for p in per_run]
            result["dedup"]["variance"] = {
                split: {k: statistics.pstdev([p[split][k] for p in per_run]) for k in keys} for split in per_run[0]
            }

    result["meta"] = {
        "label": args.label or "adhoc",
        "engine": args.engine,
        "model": settings.gemini_model if args.engine == "gemini" else None,
        "thinking_level": settings.gemini_thinking_level if args.engine == "gemini" else None,
        "judge_model": settings.gemini_judge_model if args.engine == "gemini" else None,
        "dedup_judge": getattr(settings, "dedup_judge", False) and settings.has_gemini,
        "split": args.split,
        "repeats": args.repeats,
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "wall_seconds": round(time.time() - started, 1),
        "git": os.popen(f"git -C {ROOT} rev-parse --short HEAD 2>/dev/null").read().strip() or None,
        "llm": {"live_calls": dict(meter.live_calls), "cache_hits": meter.cache_hits, "tokens": dict(meter.tokens)},
    }

    if not args.quiet:
        print_report(result)
    errors = result.get("extraction", {}).get("errors", [])
    if errors:
        print(f"\n!! {len(errors)} case(s) excluded because the model call failed (re-run to fill from cache):")
        for e in errors:
            print(f"   {e['id']}: {e['error'][:140]}")
    if args.label:
        RESULTS.mkdir(exist_ok=True)
        path = RESULTS / f"{args.label}.json"
        path.write_text(json.dumps(result, indent=1, ensure_ascii=False))
        print(f"\nSaved {path.relative_to(ROOT)}")
    if args.gate:
        failures = check_gate(result, args.engine)
        if failures:
            print("\nGATE FAILED:\n  " + "\n  ".join(failures))
            raise SystemExit(1)
        print("\nGate passed.")


if __name__ == "__main__":
    main()
