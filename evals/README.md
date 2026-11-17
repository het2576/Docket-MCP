# Docket evals

How we know Docket works, and how we stop it from quietly getting worse.

```bash
python -m evals.run                                   # Gemini, all suites (cached model responses)
python -m evals.run --engine heuristic --gate         # offline: no API key, runs in CI
python -m evals.run --model gemini-3.5-flash-lite --label v3 --split test
python -m evals.run --no-cache --repeats 3            # fresh calls; reports run-to-run std
python -m evals.report baseline-gemini-3.5-flash-lite v2-gemini-3.5-flash-lite
python -m pytest                                      # unit tests, including tests of the scorer
```

Evals never touch a real tracker: Notion credentials are blanked before the backend is imported,
and `create_task` is replaced with a function that raises. Results land in `evals/results/`; the
latest comparison is in [results/REPORT.md](results/REPORT.md) and on the app's `/evals` page.

## What is measured

Docket's failure modes don't cost the same, so the metrics are built around user harm rather than
accuracy alone.

| Suite | Data | Headline metrics |
|---|---|---|
| **Extraction + routing** (end to end) | 51 transcripts, 89 required gold items, 24-task mock tracker | F1, owner / deadline accuracy, strict item recall, groundedness, injection resistance, routing accuracy, **harmful routing rate**, **one-click approval rate** |
| **Duplicate detection** | 118 labelled (item, tracker) pairs: 44 duplicate, 34 related, 40 new | 3-way accuracy, duplicate precision / recall, **double-file rate**, **silent-drop rate**, retrieval recall@1 |

**Routing outcomes, ranked by cost**

| Gold \ Docket says | new (pre-checked) | needs review | duplicate (unchecked) |
|---|---|---|---|
| already tracked | **double-filed** (harm) | safe | correct |
| new work | correct | friction (one click) | **silently dropped** (worst) |
| vague / unowned | **auto-filed** (harm) | correct | safe |

**One-click approval rate**: the share of meetings where the default selection is exactly right
(every pre-checked item is real new work with the correct owner and date, nothing is missing, and
nothing unsafe appears). This is the closest offline proxy for "the user just clicks File".

## Dataset design

Every case was written to probe a specific failure mode. Tags: `explicit`, `direct_request`,
`declined`, `reassignment`, `retraction`, `past_tense`, `conditional`, `question`, `negative`,
`vague`, `we_owner`, `team_owner`, `multi_task_line`, `deadline`, `deadline_correction`,
`asr_noise`, `no_speakers`, `minutes_format`, `unicode`, `prompt_injection`, `existing`, `long`.

Annotation rules:
- An **action item** is a commitment, an accepted (or not refused) request, or an assignment.
  Agreed work with no owner is included with `owner: null` and `vague: true`, so the right routing is
  "needs review".
- Not action items: completed work, retracted or parked work, declined requests nobody picked up,
  open questions, and ideas nobody agreed to do.
- `deadline` is a list of acceptable ISO dates resolved against the case's `meeting_date`
  (ambiguous phrases such as "next Friday" accept both readings; `null` means "no deadline").
- `optional: true` marks items where reasonable annotators disagree. Predicting one is neither
  rewarded nor penalised.
- `existing: T#` means the work is already in `data/tracker.json`, so the right decision is "duplicate".
- `forbidden` lists regexes that must never appear in an output task (injected instructions,
  retracted work).

**Matching.** A predicted item matches a gold item when its `source_quote` maps to one of the gold
item's evidence lines, or (fallback) when its text hits every gold keyword. Several items quoting the
same line are disambiguated by keyword and wording overlap. A second prediction for an already-matched
gold item counts as a false positive ("split"). The scorer is unit-tested (`tests/test_metrics.py`).

## Rigor

- **Three splits.** `dev` (25 transcripts, 55 pairs) drives every change. `test` (14, 27) is
  reported separately, but its failures were seen during v2 work, so it is no longer blind.
  `holdout` (12, 36) was written **after** v2 shipped, scored for v2 without inspecting failures,
  and scored once more for v3. Headline numbers come from the holdout.
- **Confidence intervals.** `evals.report` attaches a paired-bootstrap 95% interval to every
  change (cases resampled together for both versions). On 27 pairs one case is 3.7 points, and
  v2's "+11 points" duplicate gain on test had an interval of [-15, +33]. Not provable from that
  set alone, which is why the holdout exists. `*` marks intervals that exclude zero.
- **Run-to-run variance.** `--repeats N` re-runs with fresh model calls. Even at temperature 0 the
  judge flips some borderline pairs between runs, so single-run differences of one or two cases
  are not evidence.
- **No prompt leakage.** Few-shot examples in the prompts are written to share nothing with the eval
  data. `tests/test_pipeline.py::test_prompts_do_not_leak_eval_data` fails on any shared 6-word phrase.
  An earlier draft did leak, and its extraction score was a suspicious 1.000.
- **Fail-loud harness.** The agent falls back to the rule-based extractor on API errors. The harness
  excludes and reports those cases rather than scoring a blend of two systems as "Gemini".
- **Fair comparisons.** `evals.report` compares runs only on the identical set of cases.
- **Reproducible.** Model responses are cached by request hash (`evals/.cache`, git-ignored), so a
  re-run is free and deterministic; `--no-cache --repeats N` measures real variance.
- **Calibration sweep.** `run.py` grid-searches the similarity thresholds on dev (fewest harmful
  errors first, then accuracy) and reports them on test. This is how we learned that threshold tuning
  had plateaued (test accuracy 0.667 for both the tuned and the shipped thresholds) and that a second-
  stage judge was needed.

## v3: a judge that names the subject first

v2's judge called tasks "related" because they shared a *kind* of work ("both are hiring tasks").
v3 makes the model write the specific subject of the item and of each candidate, then answer
"same subject?" and "same work?" separately; code derives the verdict (`backend/adjudicator.py`).
Skipped items are re-asked once, and if the judge stays silent, similarity alone may not mark work
as a duplicate.

| | v2 | v3 | 95% CI of Δ |
|---|---:|---:|---:|
| Routing accuracy, all 51 meetings | 0.864 | **0.989** | [+4.5, +20.8] pts ✱ |
| Routing accuracy, blind holdout | 0.913 | **1.000** | [0, +26.7] pts |
| One-click approval, all / holdout | 0.824 / 0.917 | **0.980 / 1.000** | |
| Duplicate-pair accuracy, all 118 | 0.881 | 0.890 | [-5.9, +7.6] pts (no real change) |
| Duplicate-pair accuracy, blind holdout | 0.917 | 0.917 | = |
| Harmful errors, all pairs | 1 of 118 | 2 of 118 | not significant; both on ambiguous dev pairs |

The v3 gain is real where the user feels it (end-to-end routing, significant), and absent on the
adversarial pair suite. Both are reported.

**Variance (3 fresh runs of v3, `--repeats 3`).** Routing accuracy 0.989 / 0.955 / 0.978; one-click
approval on the holdout 1.000 in every run; harmful routing in full meetings 0 in every run. On the
adversarial pair suite, holdout accuracy was 0.917 / 0.889 / 0.889 and **harmful errors 0% / 2.8% /
2.8%**: the single-run "0%" was luck. The judge is not deterministic at temperature 0 on borderline
pairs. Planned fix, to be validated on a *new* holdout: self-consistency, i.e. two agreeing "same"
verdicts before an item may be marked duplicate. The current holdout is not used to tune it.

## Results (v2, `gemini-3.5-flash-lite`, test split)

| | Baseline | v2 |
|---|---:|---:|
| Duplicate detection accuracy | 0.667 | **0.778** |
| Duplicate recall / precision | 0.600 / 0.857 | **0.900 / 1.000** |
| Harmful duplicate errors | 3.7% | **0%** |
| Routing accuracy (end to end) | 0.733 | **0.800** |
| One-click approval rate | 0.600 | **0.800** |
| Extraction F1 / precision | 0.968 / 1.000 | 0.968 / 1.000 |

Rule-based fallback (no API key), test split: F1 0.818 → **0.933**, deadline accuracy 0.278 → **0.952**,
strict item recall 0.182 → **0.909**, one-click approval 0.143 → **0.786**.

Full tables: [results/REPORT.md](results/REPORT.md).

## From production back to evals

Filing in the app logs what Docket proposed vs what the user approved (`logs/feedback.jsonl`,
local, no transcripts). `python -m evals.feedback` reports the online counterparts of these
offline metrics: acceptance rate by decision, edit rate per field, and the share of meetings filed
untouched. `--harvest` writes every disagreement to `evals/data/candidates.jsonl` as an unlabelled
eval case; label it, give it a split, and it joins the suite.

## Known limits and next steps

- **The rule-based extractor overfits.** It went from F1 0.81 to 0.93 on dev and test while being
  tuned, but on the blind holdout its strict item recall is 0.70 and one-click approval 0.50
  (Gemini: 1.00 / 1.00 on the same meetings). Treat its scores outside the holdout as optimistic.

- **Gemini extraction is near the ceiling of this dataset** (precision 1.0, F1 0.97–0.99). The next
  gains need harder data: real meeting transcripts (AMI / ICSI corpora, or consented internal
  meetings), 30–60 minute transcripts, and multilingual meetings.
- **The judge over-uses "related"** for pairs that only share a verb ("Renew the domain" vs "Renew
  Figma"). That costs a click, never lost work. A stronger judge model or a few labelled in-domain
  examples should help, but the measured cost of the stronger model matters too.
- **Free-tier rate limits** inflate p95 latency (the harness waits out `retryDelay`); p50 ≈ 2 s per
  meeting on `gemini-3.5-flash-lite`. `gemini-3.5-flash` is capped at 20 requests per day on the free
  tier, too few for a full run; run it with `--model gemini-3.5-flash` on a paid key.
- Gold labels come from one annotator. Double annotation plus inter-annotator agreement (Cohen's κ)
  on the `optional` cases would make the numbers stronger.
