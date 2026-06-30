"use client";

import { Check, CheckCircle2, ExternalLink, FileSignature, Loader2, Play, RotateCcw, ScrollText, Stamp } from "lucide-react";
import { useMemo, useState } from "react";
import type { AgentResult, CreateResult, ReviewItem } from "@/lib/types";

const sampleTranscript = `Priya: Het, can you review duplicate detection thresholds before Friday?
Het: Yes, I will tighten the scoring and add two examples.
Maya: I will draft onboarding copy by tomorrow.
Arjun: Someone should look into exporting the reasoning trace for the demo.
Het: Action item: add a human approval step before creating tasks.`;

function decisionLabel(decision: ReviewItem["decision"]) {
  return decision.replace("_", " ");
}

function entryNumber(index: number) {
  return String(index + 1).padStart(2, "0");
}

function formatFileNo(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}${String(date.getDate()).padStart(2, "0")}`;
}

export default function HomePage() {
  const [sourceMeeting, setSourceMeeting] = useState("Weekly product sync");
  const [transcript, setTranscript] = useState(sampleTranscript);
  const [result, setResult] = useState<AgentResult | null>(null);
  const [selected, setSelected] = useState<Record<number, boolean>>({});
  const [isProcessing, setIsProcessing] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [createResult, setCreateResult] = useState<CreateResult | null>(null);
  const fileNo = useMemo(() => formatFileNo(new Date()), []);

  const selectedItems = useMemo(() => {
    if (!result) return [];
    return result.review_items.filter((_, index) => selected[index]);
  }, [result, selected]);

  async function processTranscript() {
    setIsProcessing(true);
    setError(null);
    setCreateResult(null);
    try {
      const response = await fetch("/api/process", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transcript, source_meeting: sourceMeeting })
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Processing failed.");
      setResult(payload);
      const nextSelected: Record<number, boolean> = {};
      payload.review_items.forEach((item: ReviewItem, index: number) => {
        nextSelected[index] = item.decision !== "duplicate";
      });
      setSelected(nextSelected);
    } catch (event) {
      setError(event instanceof Error ? event.message : "Something went wrong.");
    } finally {
      setIsProcessing(false);
    }
  }

  async function createTasks() {
    if (!selectedItems.length) return;
    setIsCreating(true);
    setError(null);
    try {
      const response = await fetch("/api/create-tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: selectedItems, source_meeting: sourceMeeting })
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Task creation failed.");
      setCreateResult(payload);
    } catch (event) {
      setError(event instanceof Error ? event.message : "Something went wrong.");
    } finally {
      setIsCreating(false);
    }
  }

  const entryCount = result?.review_items.length ?? 0;

  return (
    <main className="page">
      <header className="masthead">
        <div className="masthead-inner">
          <div className="wordmark">
            <span className="wordmark-icon">
              <Stamp size={17} aria-hidden="true" />
            </span>
            <span className="wordmark-text">
              Docket
              <span className="wordmark-tag">MCP</span>
            </span>
          </div>
          <div className="masthead-file">
            <span>File No.</span>
            <span className="masthead-file-value">{fileNo}</span>
          </div>
        </div>
      </header>

      <div className="shell">
        <section className="panel input-panel">
          <div className="panel-header">
            <span className="panel-kicker">Intake</span>
            <p className="panel-lede">
              Paste the transcript. Every action item gets a docket entry, a verbatim citation, and your sign-off
              before it reaches the tracker.
            </p>
          </div>

          <div className="form">
            <div className="field">
              <label htmlFor="source">Source meeting</label>
              <input
                id="source"
                className="text-input"
                value={sourceMeeting}
                onChange={(event) => setSourceMeeting(event.target.value)}
              />
            </div>

            <div className="field" style={{ minHeight: 0, flex: 1 }}>
              <label htmlFor="transcript">Transcript</label>
              <textarea
                id="transcript"
                className="textarea"
                value={transcript}
                onChange={(event) => setTranscript(event.target.value)}
              />
            </div>

            <div className="actions">
              <button className="button" onClick={processTranscript} disabled={isProcessing || !transcript.trim()}>
                {isProcessing ? <Loader2 size={17} className="spin" /> : <Play size={17} />}
                Process
              </button>
              <button
                className="button ghost"
                onClick={() => {
                  setTranscript(sampleTranscript);
                  setResult(null);
                  setCreateResult(null);
                  setError(null);
                }}
              >
                <RotateCcw size={16} />
                Reset
              </button>
            </div>
            {error ? <div className="status-text" role="alert">{error}</div> : null}
          </div>
        </section>

        <section className="panel review-panel">
          <div className="docket-top">
            <div>
              <span className="panel-kicker">Docket</span>
              <h2 className="docket-title">
                {result ? `${entryCount} ${entryCount === 1 ? "entry" : "entries"} extracted` : "Awaiting transcript"}
              </h2>
              <p className="docket-sub">
                {result ? (
                  <>
                    Trace logged to <span className="mono">{result.log_path}</span>
                  </>
                ) : (
                  "Run the agent to inspect extracted commitments before anything is created."
                )}
              </p>
            </div>
            <button className="button" onClick={createTasks} disabled={!selectedItems.length || isCreating}>
              {isCreating ? <Loader2 size={17} className="spin" /> : <FileSignature size={17} />}
              Create Approved Tasks
            </button>
          </div>

          {result?.summary ? <div className="summary">{result.summary}</div> : null}

          {!result ? (
            <div className="empty">
              <div className="empty-inner">
                <span className="empty-icon">
                  <ScrollText size={22} aria-hidden="true" />
                </span>
                <p className="empty-title">No entries yet</p>
                <p className="empty-text">
                  Paste a transcript and process it to see structured action items, duplicate checks, and the agent&rsquo;s
                  reasoning.
                </p>
              </div>
            </div>
          ) : (
            <div className="review-list">
              {result.review_items.map((item, index) => (
                <article className="review-item" key={`${item.action_item.task}-${index}`}>
                  <input
                    aria-label={`Approve ${item.action_item.task}`}
                    className="checkbox"
                    type="checkbox"
                    checked={Boolean(selected[index])}
                    onChange={(event) => setSelected((current) => ({ ...current, [index]: event.target.checked }))}
                  />
                  <div className="item-main">
                    <div className="item-head">
                      <span className="entry-no">{entryNumber(index)}</span>
                      <h3 className="task-title">{item.action_item.task}</h3>
                      <span className={`stamp ${item.decision}`}>{decisionLabel(item.decision)}</span>
                    </div>
                    <div className="meta">
                      <span className="meta-field">
                        <span className="meta-label">Owner</span>
                        <span className="meta-value">{item.action_item.owner || "Unassigned"}</span>
                      </span>
                      <span className="meta-field">
                        <span className="meta-label">Due</span>
                        <span className="meta-value">{item.action_item.deadline || "None"}</span>
                      </span>
                      <span className="meta-field">
                        <span className="meta-label">Confidence</span>
                        <span className="meta-value">{Math.round(item.action_item.confidence * 100)}%</span>
                      </span>
                    </div>
                    <p className="reasoning">{item.reasoning}</p>
                    {item.action_item.source_quote ? (
                      <blockquote className="quote">{item.action_item.source_quote}</blockquote>
                    ) : null}
                    {item.similar_tasks.length ? (
                      <div className="similar">
                        <span className="similar-mark" aria-hidden="true">
                          ↳
                        </span>
                        Cross-reference: {item.similar_tasks[0].title}
                        {item.similar_tasks[0].owner ? `, ${item.similar_tasks[0].owner}` : ""}
                      </div>
                    ) : null}
                  </div>
                </article>
              ))}
            </div>
          )}

          {createResult ? (
            <div className="receipt">
              <div className="receipt-header">
                <CheckCircle2 size={14} className="receipt-ok" aria-hidden="true" />
                <span className="receipt-headline">
                  {createResult.created.length} task{createResult.created.length !== 1 ? "s" : ""} filed to tracker
                </span>
                <span className="receipt-stamp">Filed</span>
              </div>
              {createResult.created.map((task, i) => (
                <div className="receipt-row" key={task.id ?? i}>
                  <span className="receipt-task">
                    <Check size={12} className="receipt-check" aria-hidden="true" />
                    {task.title}
                  </span>
                  {(task.owner || task.due_date) ? (
                    <span className="receipt-meta">
                      {task.owner ? <span>Owner: {task.owner}</span> : null}
                      {task.due_date ? <span>Due: {task.due_date}</span> : null}
                    </span>
                  ) : null}
                  {task.url ? (
                    <a className="receipt-link" href={task.url} target="_blank" rel="noopener noreferrer">
                      <ExternalLink size={11} aria-hidden="true" />
                      Open in tracker
                    </a>
                  ) : null}
                </div>
              ))}
              {createResult.skipped.length ? (
                <div className="receipt-skipped">
                  {createResult.skipped.length} item{createResult.skipped.length !== 1 ? "s" : ""} skipped — missing task title
                </div>
              ) : null}
            </div>
          ) : null}
        </section>
      </div>
    </main>
  );
}
