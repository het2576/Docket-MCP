"""Compare saved eval runs on identical cases and write evals/results/REPORT.md.

    python -m evals.report baseline-gemini-3.5-flash-lite v2-gemini-3.5-flash-lite \
        --also baseline-heuristic v2-heuristic
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from evals import metrics as M
from evals.run import summarize_extraction

RESULTS = Path(__file__).resolve().parent / "results"
SPLITS = ("holdout", "test", "dev", "all")
TITLES = {
    "holdout": "blind holdout (written after v2, never inspected while tuning)",
    "test": "test split",
    "dev": "dev split (used for tuning)",
    "all": "all splits",
}

EXTRACTION_ROWS = [
    ("f1", "Extraction F1", True), ("precision", "Precision", True), ("recall", "Recall", True),
    ("owner_accuracy", "Owner accuracy", True), ("deadline_accuracy", "Deadline accuracy", True),
    ("strict_item_recall", "Strict item recall (task+owner+date)", True),
    ("task_clean_rate", "Task text is clean", True),
    ("negative_case_pass_rate", "No-action meetings stay empty", True),
    ("injection_resistance", "Prompt-injection resistance", True),
    ("routing_accuracy", "Routing accuracy (new/review/dup)", True),
    ("harmful_routing_rate", "Harmful routing rate", False),
    ("zero_touch_rate", "One-click approval rate", True),
]
DEDUP_ROWS = [
    ("accuracy", "3-way accuracy", True), ("macro_f1", "Macro F1", True),
    ("duplicate_precision", "Duplicate precision", True), ("duplicate_recall", "Duplicate recall", True),
    ("double_file_rate", "Double-file rate", False), ("silent_drop_rate", "Silent-drop rate", False),
    ("harmful_rate", "Harmful error rate", False),
]


def load(label: str) -> dict:
    return json.loads((RESULTS / f"{label}.json").read_text())


def fmt(v) -> str:
    return "–" if v is None else f"{v:.3f}"


def delta(a, b, higher_is_better: bool) -> str:
    if a is None or b is None:
        return ""
    d = b - a
    if abs(d) < 0.0005:
        return "="
    good = (d > 0) == higher_is_better
    return f"{'+' if d > 0 else ''}{d:.3f} {'✅' if good else '⚠️'}"


def ci_text(ci) -> str:
    if not ci:
        return ""
    lo, hi = ci
    sig = " *" if lo > 0 or hi < 0 else ""
    return f"[{lo:+.3f}, {hi:+.3f}]{sig}"


def table(rows, before: dict, after: dict, names: tuple[str, str], cis: dict | None = None) -> list[str]:
    out = [f"| Metric | {names[0]} | {names[1]} | Δ | 95% CI of Δ |", "|---|---:|---:|---:|---:|"]
    for key, label, hib in rows:
        out.append(
            f"| {label} | {fmt(before.get(key))} | {fmt(after.get(key))} | {delta(before.get(key), after.get(key), hib)} "
            f"| {ci_text((cis or {}).get(key))} |"
        )
    return out


def bootstrap_rows(rows, before_cases, after_cases, summarize) -> dict:
    return {key: M.paired_bootstrap(before_cases, after_cases, summarize, key, n=1000) for key, _, _ in rows}


def section_rows(rows, before: dict, after: dict, cis: dict | None = None) -> list[dict]:
    return [
        {"key": k, "label": label, "before": before.get(k), "after": after.get(k), "higher_is_better": hib,
         "ci": list((cis or {}).get(k) or []) or None}
        for k, label, hib in rows
    ]


def compare(before_label: str, after_label: str, sections: list[dict] | None = None) -> list[str]:
    before, after = load(before_label), load(after_label)
    lines = [f"## {before_label} → {after_label}", ""]
    if "extraction" in before and "extraction" in after:
        common = {c["id"] for c in before["extraction"]["cases"]} & {c["id"] for c in after["extraction"]["cases"]}
        for split in SPLITS:
            pick = lambda run: sorted(  # noqa: E731
                (c for c in run["extraction"]["cases"] if c["id"] in common and (split == "all" or c["split"] == split)),
                key=lambda c: c["id"],
            )
            b, a = pick(before), pick(after)
            if not b:
                continue
            title = TITLES[split]
            lines += [f"**Extraction + routing, {title}** ({len(b)} transcripts, identical case set)", ""]
            sb, sa = summarize_extraction(b), summarize_extraction(a)
            cis = bootstrap_rows(EXTRACTION_ROWS, b, a, summarize_extraction)
            lines += table(EXTRACTION_ROWS, sb, sa, (before_label, after_label), cis)
            lines.append("")
            if sections is not None:
                sections.append({"suite": "extraction", "split": split, "n": len(b), "rows": section_rows(EXTRACTION_ROWS, sb, sa, cis)})
    if "dedup" in before and "dedup" in after:
        common = {r["id"] for r in before["dedup"]["cases"]} & {r["id"] for r in after["dedup"]["cases"]}
        for split in SPLITS:
            pick = lambda run: sorted(  # noqa: E731
                (r for r in run["dedup"]["cases"] if r["id"] in common and (split == "all" or r["split"] == split)),
                key=lambda r: r["id"],
            )
            b, a = pick(before), pick(after)
            if not b:
                continue
            lines += [f"**Duplicate detection, {TITLES[split]}** ({len(b)} pairs)", ""]
            db, da = M.summarize_dedup(b), M.summarize_dedup(a)
            cis = bootstrap_rows(DEDUP_ROWS, b, a, M.summarize_dedup)
            lines += table(DEDUP_ROWS, db, da, (before_label, after_label), cis)
            lines.append("")
            if sections is not None:
                sections.append({"suite": "dedup", "split": split, "n": len(b), "rows": section_rows(DEDUP_ROWS, db, da, cis)})
    meta = after["meta"]
    lines.append(
        f"_{after_label}: model `{meta.get('model')}`, judge={'on' if meta.get('dedup_judge') else 'off'}, "
        f"git {meta.get('git')}, run {meta.get('run_at')}. LLM calls {meta['llm']['live_calls']}, tokens {meta['llm']['tokens']}._"
    )
    lines.append("")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("before")
    parser.add_argument("after")
    parser.add_argument("--also", nargs=2, action="append", default=[], metavar=("BEFORE", "AFTER"))
    args = parser.parse_args()
    lines = [
        "# Docket eval report", "",
        "Generated by `python -m evals.report`. Methodology: [evals/README.md](../README.md). "
        "Δ intervals are paired-bootstrap 95% CIs over cases; `*` marks an interval that excludes zero.", "",
    ]
    comparisons = []
    for before, after in [(args.before, args.after), *args.also]:
        sections: list[dict] = []
        lines += compare(before, after, sections)
        comparisons.append({"before": before, "after": after, "sections": sections})
    (RESULTS / "REPORT.md").write_text("\n".join(lines))
    (RESULTS / "dashboard.json").write_text(json.dumps(dashboard(args.after, comparisons), indent=1, ensure_ascii=False))
    print("\n".join(lines))


def dashboard(current_label: str, comparisons: list[dict]) -> dict:
    """Everything the web /evals page renders, precomputed so metric logic lives only in Python."""
    run = load(current_label)
    ex, dd = run["extraction"], run["dedup"]
    summary = {k: v for k, v in ex["summary"]["all"].items() if k != "by_tag"}
    extraction_errors = []
    for case in ex["cases"]:
        for fp in case["false_positives"]:
            extraction_errors.append({"case": case["id"], "split": case["split"], "kind": "false positive", "detail": fp["task"]})
        for miss in case["missed"]:
            extraction_errors.append({"case": case["id"], "split": case["split"], "kind": "missed", "detail": miss})
        for it in case["items"]:
            if not it["optional"] and it["outcome"] != "correct":
                extraction_errors.append({"case": case["id"], "split": case["split"], "kind": it["outcome"].replace("_", " "),
                                          "detail": f"{it['pred_task']} → {it['decision'].replace('_', ' ')}"})
    dedup_errors = [
        {"id": r["id"], "split": r["split"], "label": r["label"], "decision": r["decision"], "outcome": r["outcome"],
         "query": r["query"], "top": r["top_title"], "reason": (r.get("judge") or {}).get("reason")}
        for r in dd["cases"] if r["outcome"] != "correct"
    ]
    variance = None
    repeats_path = RESULTS / f"{current_label}-x3.json"
    if repeats_path.exists():
        rep = json.loads(repeats_path.read_text())
        variance = {
            "repeats": rep["meta"]["repeats"],
            "extraction": rep.get("extraction", {}).get("repeat_summaries"),
            "dedup": rep.get("dedup", {}).get("repeat_summaries"),
        }
    return {
        "current": current_label,
        "variance": variance,
        "extraction_holdout": {k: v for k, v in ex["summary"].get("holdout", {}).items() if k != "by_tag"},
        "dedup_holdout": dd["summary"].get("holdout"),
        "meta": run["meta"],
        "data": {"transcripts": ex["summary"]["all"]["cases"], "gold_items": summary["tp"] + summary["fn"],
                 "dedup_pairs": dd["summary"]["all"]["n"]},
        "extraction": summary,
        "extraction_test": {k: v for k, v in ex["summary"].get("test", {}).items() if k != "by_tag"},
        "by_tag": ex["summary"]["all"]["by_tag"],
        "dedup": dd["summary"]["all"],
        "dedup_test": dd["summary"].get("test"),
        "sweep": dd.get("sweep"),
        "comparisons": comparisons,
        "errors": {"extraction": extraction_errors, "dedup": dedup_errors},
    }


if __name__ == "__main__":
    main()
