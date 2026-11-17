"use client";

import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { useMemo, useState } from "react";

type Row = { key: string; label: string; before: number | null; after: number | null; higher_is_better: boolean; ci?: [number, number] | null };
type Split = "holdout" | "test" | "dev" | "all";
type Section = { suite: "extraction" | "dedup"; split: Split; n: number; rows: Row[] };
type RepeatSummary = Record<string, Record<string, number>>;
type Comparison = { before: string; after: string; sections: Section[] };
type Tag = { precision: number; recall: number; f1: number; cases: number; zero_touch: number };
type DedupSummary = {
  n: number;
  accuracy: number;
  duplicate_precision: number;
  duplicate_recall: number;
  harmful_rate: number;
  confusion: Record<"duplicate" | "related" | "new", Partial<Record<"duplicate" | "needs_review" | "new", number>>>;
};

export type Dashboard = {
  current: string;
  meta: {
    model: string | null;
    dedup_judge: boolean;
    git: string | null;
    run_at: string;
    llm: { tokens: Record<string, number> };
  };
  data: { transcripts: number; gold_items: number; dedup_pairs: number };
  extraction: Record<string, number | null> & { latency_p50_s: number | null };
  extraction_test: Record<string, number | null>;
  extraction_holdout?: Record<string, number | null>;
  dedup_holdout?: DedupSummary | null;
  by_tag: Record<string, Tag>;
  dedup: DedupSummary;
  dedup_test: DedupSummary | null;
  sweep: {
    test_best?: { accuracy: number };
    test_current?: { accuracy: number };
    dev_best?: { accuracy: number; harmful_rate: number };
  } | null;
  comparisons: Comparison[];
  variance?: { repeats: number; extraction: RepeatSummary[] | null; dedup: RepeatSummary[] | null } | null;
  errors: {
    extraction: { case: string; split: string; kind: string; detail: string }[];
    dedup: { id: string; split: string; label: string; decision: string; outcome: string; query: string; top: string | null; reason: string | null }[];
  };
};

const SPLIT_LABEL: Record<Split, string> = { holdout: "Blind holdout", test: "Test", dev: "Dev", all: "All cases" };

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "–" : `${Math.round(v * 1000) / 10}%`);
const pts = (d: number) => `${d > 0 ? "+" : ""}${Math.round(d * 1000) / 10} pts`;

const TAG_LABEL: Record<string, string> = {
  asr_noise: "Noisy speech-to-text",
  brainstorm: "Brainstorming only",
  buried_commitment: "Commitment buried in a ramble",
  conditional: "Conditional work",
  deadline: "Deadlines",
  deadline_correction: "Renegotiated deadline",
  declined: "Declined requests",
  direct_request: "Direct requests",
  existing: "Already in the tracker",
  explicit: "Explicit commitments",
  long: "Long meetings",
  meeting_date_friday: "Meeting on a Friday",
  meeting_date_monday: "Meeting on a Monday",
  minutes_format: "Written minutes",
  mixed: "Mixed agenda",
  multi_task_line: "Two tasks in one line",
  negative: "No action items",
  no_speakers: "Prose notes, no speakers",
  owner_negotiation: "Owner negotiated",
  past_tense: "Already-done work",
  prompt_injection: "Prompt injection",
  question: "Questions, not tasks",
  reassignment: "Reassigned tasks",
  retraction: "Retracted tasks",
  team_owner: "Team owners",
  third_person_assignment: "Assigned by someone else",
  unicode: "Non-ASCII names",
  vague: "Unowned / vague",
  we_owner: "“We should…”"
};

const OUTCOME_TONE: Record<string, "new" | "needs_review" | "duplicate"> = {
  correct: "new",
  safe: "needs_review",
  friction: "needs_review",
  missed_context: "needs_review",
  double_filed: "duplicate",
  dropped: "duplicate",
  "auto filed vague": "duplicate",
  "false positive": "duplicate",
  missed: "duplicate"
};

function CiNote({ row }: { row: Row }) {
  if (!row.ci) return null;
  const [lo, hi] = row.ci;
  const significant = lo > 0 || hi < 0;
  return (
    <span className={`ev-ci${significant ? " is-sig" : ""}`} title="Paired-bootstrap 95% confidence interval of the change">
      95% CI {pts(lo)} to {pts(hi)}
    </span>
  );
}

function Delta({ row }: { row: Row }) {
  if (row.before === null || row.after === null) return <span className="ev-delta">–</span>;
  const d = row.after - row.before;
  if (Math.abs(d) < 0.0005) {
    return (
      <span className="ev-delta is-flat">
        <Minus size={13} aria-hidden="true" />
        same
      </span>
    );
  }
  const good = d > 0 === row.higher_is_better;
  const Icon = d > 0 ? ArrowUpRight : ArrowDownRight;
  return (
    <span className={`ev-delta ${good ? "is-good" : "is-bad"}`}>
      <Icon size={13} aria-hidden="true" />
      {pts(d)}
    </span>
  );
}

function Kpi({ label, value, baseline, higherIsBetter = true, note }: { label: string; value: number | null; baseline: number | null; higherIsBetter?: boolean; note: string }) {
  return (
    <div className="ev-kpi">
      <span className="ev-kpi-label">{label}</span>
      <strong className="ev-kpi-value">{pct(value)}</strong>
      {baseline !== null && value !== null ? (
        <Delta row={{ key: label, label, before: baseline, after: value, higher_is_better: higherIsBetter }} />
      ) : (
        <span className="ev-delta is-flat">no baseline</span>
      )}
      <span className="ev-kpi-note">{note}</span>
    </div>
  );
}

function CompareTable({ section, names }: { section: Section; names: [string, string] }) {
  return (
    <table className="ev-compare">
      <caption className="visually-hidden">
        {names[0]} versus {names[1]}
      </caption>
      <thead>
        <tr>
          <th scope="col">Metric</th>
          <th scope="col">Before → after</th>
          <th scope="col" className="num">
            Change
          </th>
        </tr>
      </thead>
      <tbody>
        {section.rows.map((row) => {
          const good = row.higher_is_better;
          return (
            <tr key={row.key}>
              <th scope="row">
                {row.label}
                {!good ? <span className="ev-lower"> lower is better</span> : null}
              </th>
              <td>
                <div className="ev-bars" aria-label={`${pct(row.before)} before, ${pct(row.after)} after`}>
                  <span className="ev-bar is-before" style={{ width: `${(row.before ?? 0) * 100}%` }} />
                  <span className={`ev-bar is-after${good ? "" : " is-cost"}`} style={{ width: `${(row.after ?? 0) * 100}%` }} />
                </div>
                <span className="ev-bar-values">
                  {pct(row.before)} <span aria-hidden="true">→</span> <strong>{pct(row.after)}</strong>
                </span>
              </td>
              <td className="num">
                <Delta row={row} />
                <CiNote row={row} />
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

const VARIANCE_ROWS: [string, string, string][] = [
  ["extraction", "f1", "Extraction F1"],
  ["extraction", "routing_accuracy", "Routing accuracy"],
  ["extraction", "zero_touch_rate", "One-click approval"],
  ["extraction", "harmful_routing_rate", "Harmful routing"],
  ["dedup", "accuracy", "Duplicate 3-way accuracy"],
  ["dedup", "harmful_rate", "Duplicate harmful errors"]
];

const GOLD_ROWS = [
  ["duplicate", "Already tracked"],
  ["related", "Related work"],
  ["new", "New work"]
] as const;
const DECISION_COLS = [
  ["duplicate", "Duplicate"],
  ["needs_review", "Needs review"],
  ["new", "New"]
] as const;
const CELL_OUTCOME: Record<string, Record<string, "correct" | "ok" | "harm">> = {
  duplicate: { duplicate: "correct", needs_review: "ok", new: "harm" },
  related: { duplicate: "harm", needs_review: "correct", new: "ok" },
  new: { duplicate: "harm", needs_review: "ok", new: "correct" }
};

function Confusion({ summary }: { summary: DedupSummary }) {
  return (
    <table className="ev-matrix">
      <caption className="visually-hidden">Gold label by Docket decision</caption>
      <thead>
        <tr>
          <td />
          {DECISION_COLS.map(([key, label]) => (
            <th scope="col" key={key}>
              Docket said <br />
              <strong>{label}</strong>
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {GOLD_ROWS.map(([gold, label]) => {
          const row = summary.confusion[gold] ?? {};
          const total = Object.values(row).reduce((a, b) => a + (b ?? 0), 0) || 1;
          return (
            <tr key={gold}>
              <th scope="row">{label}</th>
              {DECISION_COLS.map(([decision]) => {
                const count = row[decision] ?? 0;
                const kind = CELL_OUTCOME[gold][decision];
                return (
                  <td key={decision} className={`ev-cell is-${kind}${count ? "" : " is-zero"}`} style={{ ["--share" as string]: count / total }}>
                    <span className="ev-cell-count">{count}</span>
                    <span className="ev-cell-kind">{kind === "correct" ? "right" : kind === "ok" ? "one click" : "harm"}</span>
                  </td>
                );
              })}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function EvalsView({ data }: { data: Dashboard }) {
  const [split, setSplit] = useState<Split>("holdout");
  const [errorTab, setErrorTab] = useState<"dedup" | "extraction">("dedup");
  const main = data.comparisons[0];

  // Headline value and its delta must come from the same cases, so both are read from the
  // identical-case comparison (test split) rather than from the full current run.
  const kpi = useMemo(() => {
    const ext = data.extraction_holdout ?? data.extraction;
    const ddSummary = (data.dedup_holdout ?? data.dedup) as unknown as Record<string, number | null>;
    return (suite: Section["suite"], key: string) => {
      const row = main?.sections.find((s) => s.suite === suite && s.split === "holdout")?.rows.find((r) => r.key === key);
      if (row) return { value: row.after, baseline: row.before };
      return { value: (suite === "extraction" ? ext : ddSummary)[key] ?? null, baseline: null };
    };
  }, [data, main]);

  const dd = data.dedup_holdout ?? data.dedup;
  // A single run can look perfect by luck; show the spread across repeated runs when we have it.
  const harmRuns = (data.variance?.dedup ?? []).map((run) => run.holdout?.harmful_rate).filter((v): v is number => typeof v === "number");
  const harmNote = harmRuns.length > 1
    ? `${pct(Math.min(...harmRuns))}–${pct(Math.max(...harmRuns))} across ${harmRuns.length} runs; this run shown`
    : "Double-filed or silently dropped";
  const tags = Object.entries(data.by_tag).sort((a, b) => a[1].f1 - b[1].f1 || b[1].cases - a[1].cases);
  // Fixed locale and timezone so the server render and the browser agree (avoids a hydration mismatch).
  const runDate = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }).format(
    new Date(data.meta.run_at)
  );

  return (
    <main className="ev-main">
      <section className="ev-hero">
        <p className="ev-kicker">Evaluation report</p>
        <h1>How accurate is Docket?</h1>
        <p className="ev-sub">
          {data.data.transcripts} hand-labelled meetings ({data.data.gold_items} action items) and {data.data.dedup_pairs} duplicate-detection
          pairs across three splits: dev (used for tuning), test, and a blind holdout written after v2 and scored once. Every change
          carries a paired-bootstrap 95% interval, because on sets this size one case moves a metric by several points.
        </p>
        <ul className="ev-meta" aria-label="Run details">
          <li>
            <span>Model</span>
            {data.meta.model ?? "rule-based"}
          </li>
          <li>
            <span>Duplicate double-check</span>
            {data.meta.dedup_judge ? "on" : "off"}
          </li>
          <li>
            <span>Median latency</span>
            {data.extraction.latency_p50_s ? `${data.extraction.latency_p50_s.toFixed(1)} s / meeting` : "–"}
          </li>
          <li>
            <span>Run</span>
            {runDate}
            {data.meta.git ? ` · ${data.meta.git}` : ""}
          </li>
        </ul>
      </section>

      <p className="ev-kpi-caption">
        Headline numbers come from the blind holdout: meetings and pairs written after v2 shipped and scored once. Changes are versus{" "}
        {main?.before ?? "the previous version"}.
      </p>
      <section className="ev-kpis" aria-label="Headline metrics, blind holdout">
        <Kpi label="Extraction F1" {...kpi("extraction", "f1")} note="Found the right action items" />
        <Kpi label="Owner + deadline right" {...kpi("extraction", "strict_item_recall")} note="Item, owner and date all correct" />
        <Kpi label="One-click approval" {...kpi("extraction", "zero_touch_rate")} note="Meetings filed with no edits" />
        <Kpi label="Duplicate recall" {...kpi("dedup", "duplicate_recall")} note="Already-tracked work caught" />
        <Kpi label="Duplicate precision" {...kpi("dedup", "duplicate_precision")} note="Flags that were truly duplicates" />
        <Kpi label="Harmful errors" {...kpi("dedup", "harmful_rate")} higherIsBetter={false} note={harmNote} />
      </section>

      <div className="ev-grid">
        <section className="ev-paper ev-span-2" aria-labelledby="ev-change">
          <div className="ev-paper-head">
            <div>
              <h2 id="ev-change">What changed between versions</h2>
              <p>
                Same cases, before and after. v2: deterministic dates, a leakage-free prompt and an LLM duplicate judge. v3: the judge names each
                task&rsquo;s subject before deciding.
              </p>
            </div>
            <div className="ev-seg" role="radiogroup" aria-label="Split">
              {(["holdout", "test", "all"] as const).map((value) => (
                <button key={value} type="button" role="radio" aria-checked={split === value} className={split === value ? "is-on" : ""} onClick={() => setSplit(value)}>
                  {SPLIT_LABEL[value]}
                </button>
              ))}
            </div>
          </div>
          {data.comparisons.map((comparison) => (
            <div key={`${comparison.before}->${comparison.after}`} className="ev-compare-group">
              <h3>
                {comparison.after.includes("heuristic") ? "Rule-based fallback (no API key)" : `Gemini pipeline · ${data.meta.model}`}
                <span className="ev-compare-names">
                  {comparison.before} → {comparison.after}
                </span>
              </h3>
              {!comparison.sections.some((s) => s.split === split) ? (
                <p className="ev-note">No cases in common on this split (this baseline predates it).</p>
              ) : null}
              {comparison.sections
                .filter((s) => s.split === split)
                .map((section) => (
                  <div key={section.suite} className="ev-compare-block">
                    <p className="ev-compare-title">
                      {section.suite === "extraction" ? "Extraction + routing" : "Duplicate detection"}
                      <span>
                        {section.n} {section.suite === "extraction" ? "meetings" : "pairs"}
                      </span>
                    </p>
                    <CompareTable section={section} names={[comparison.before, comparison.after]} />
                  </div>
                ))}
            </div>
          ))}
        </section>

        <section className="ev-paper" aria-labelledby="ev-matrix">
          <div className="ev-paper-head">
            <div>
              <h2 id="ev-matrix">Where duplicate decisions land</h2>
              <p>Blind holdout. Errors aren&rsquo;t equal: a dropped task is worse than an extra click.</p>
            </div>
          </div>
          <Confusion summary={dd} />
          {data.sweep?.test_current && data.sweep.test_best ? (
            <p className="ev-note">
              <strong>Why a judge, not better thresholds:</strong> the best thresholds found on dev scored {pct(data.sweep.test_best.accuracy)} on test, the
              same as the shipped ones ({pct(data.sweep.test_current.accuracy)}). Similarity scores alone had plateaued; reading the two titles side by side
              didn&rsquo;t.
            </p>
          ) : null}
        </section>

        <section className="ev-paper" aria-labelledby="ev-tags">
          <div className="ev-paper-head">
            <div>
              <h2 id="ev-tags">By scenario</h2>
              <p>Every meeting probes a failure mode. Weakest first.</p>
            </div>
          </div>
          <ul className="ev-tags">
            {tags.map(([tag, s]) => (
              <li key={tag}>
                <span className="ev-tag-name">
                  {TAG_LABEL[tag] ?? tag.replace(/_/g, " ")}
                  <span className="ev-tag-n">{s.cases}</span>
                </span>
                <span className="ev-tag-bar" aria-hidden="true">
                  <span style={{ width: `${s.f1 * 100}%` }} />
                </span>
                <span className="ev-tag-val">{pct(s.f1)}</span>
              </li>
            ))}
          </ul>
        </section>

        {data.variance?.extraction || data.variance?.dedup ? (
          <section className="ev-paper ev-span-2" aria-labelledby="ev-var">
            <div className="ev-paper-head">
              <div>
                <h2 id="ev-var">Run-to-run variance</h2>
                <p>
                  The same inputs, {data.variance.repeats} fresh runs at temperature 0. LLM outputs still vary, so a single run can&rsquo;t
                  settle a small difference.
                </p>
              </div>
            </div>
            <table className="ev-var">
              <thead>
                <tr>
                  <th scope="col">Metric (all splits)</th>
                  {Array.from({ length: data.variance.repeats }, (_, i) => (
                    <th scope="col" className="num" key={i}>
                      Run {i + 1}
                    </th>
                  ))}
                  <th scope="col" className="num">
                    Spread
                  </th>
                </tr>
              </thead>
              <tbody>
                {VARIANCE_ROWS.map(([suite, key, label]) => {
                  const runs = (suite === "extraction" ? data.variance?.extraction : data.variance?.dedup) ?? [];
                  const values = runs.map((run) => run.all?.[key]).filter((v): v is number => typeof v === "number");
                  if (!values.length) return null;
                  const spread = Math.max(...values) - Math.min(...values);
                  return (
                    <tr key={`${suite}-${key}`}>
                      <th scope="row">{label}</th>
                      {values.map((v, i) => (
                        <td className="num" key={i}>
                          {pct(v)}
                        </td>
                      ))}
                      <td className="num">{Math.round(spread * 1000) / 10} pts</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </section>
        ) : null}

        <section className="ev-paper ev-span-2" aria-labelledby="ev-errors">
          <div className="ev-paper-head">
            <div>
              <h2 id="ev-errors">Open failures</h2>
              <p>Every remaining miss in the current run, with the model&rsquo;s own reason where it gave one.</p>
            </div>
            <div className="ev-seg" role="tablist" aria-label="Failure type">
              {(["dedup", "extraction"] as const).map((value) => (
                <button key={value} type="button" role="tab" aria-selected={errorTab === value} className={errorTab === value ? "is-on" : ""} onClick={() => setErrorTab(value)}>
                  {value === "dedup" ? `Duplicates · ${data.errors.dedup.length}` : `Extraction · ${data.errors.extraction.length}`}
                </button>
              ))}
            </div>
          </div>
          <ul className="ev-errors" role="tabpanel">
            {errorTab === "dedup"
              ? data.errors.dedup.map((e) => (
                  <li key={e.id}>
                    <span className={`decision hl-${OUTCOME_TONE[e.outcome] ?? "needs_review"}`}>{e.outcome.replace(/_/g, " ")}</span>
                    <div>
                      <p className="ev-err-main">
                        “{e.query}” <span className="ev-err-vs">vs</span> “{e.top ?? "nothing"}”
                      </p>
                      <p className="ev-err-sub">
                        Gold <strong>{e.label}</strong>, Docket said <strong>{e.decision.replace("_", " ")}</strong>
                        {e.reason ? ` · “${e.reason}”` : ""}
                      </p>
                    </div>
                    <span className="ev-err-id">
                      {e.id} · {e.split}
                    </span>
                  </li>
                ))
              : data.errors.extraction.map((e, i) => (
                  <li key={`${e.case}-${i}`}>
                    <span className={`decision hl-${OUTCOME_TONE[e.kind] ?? "needs_review"}`}>{e.kind}</span>
                    <div>
                      <p className="ev-err-main">{e.detail}</p>
                    </div>
                    <span className="ev-err-id">
                      {e.case} · {e.split}
                    </span>
                  </li>
                ))}
          </ul>
        </section>
      </div>

      <footer className="ev-foot">
        Methodology, annotation rules and caveats are in <code>evals/README.md</code>. Reproduce with <code>python -m evals.run</code>; the
        rule-based suite runs in CI on every push with no API key.
      </footer>
    </main>
  );
}
