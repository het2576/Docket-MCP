"use client";

import { AlertTriangle, Check, ClipboardCopy, Download, FileUp, Loader2, PencilLine, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { EntryCard } from "@/app/components/EntryCard";
import { ThemeToggle } from "@/app/components/ThemeToggle";
import { TranscriptView } from "@/app/components/TranscriptView";
import { decisionLabel, locateQuotes, normalizeTranscriptFile, splitLines, toMarkdown } from "@/lib/transcript";
import type { ActionItem, AgentResult, CreatedTask, CreateResult, Decision, StatusResult } from "@/lib/types";

const DEFAULT_TRANSCRIPT = `Priya: Het, can you review duplicate detection thresholds before Friday?
Het: Yes, I will tighten the scoring and add two examples.
Maya: I will draft onboarding copy by tomorrow.
Arjun: Someone should look into exporting the reasoning trace for the demo.
Het: Action item: add a human approval step before creating tasks.`;

const DRAFT_KEY = "docket:draft";
const DECISIONS: Decision[] = ["new", "needs_review", "duplicate"];
const ACCEPTED_FILES = ".txt,.md,.vtt,.srt,text/plain,text/vtt";

type Sample = { id: string; label: string; transcript: string };
type Phase = "idle" | "processing" | "filing";

/** "gemini-3.5-flash" → "Gemini 3.5 Flash" */
function modelName(model: string) {
  return model
    .split("-")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function readDraft(): { meeting: string; transcript: string } | null {
  try {
    const raw = window.localStorage.getItem(DRAFT_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export default function HomePage() {
  const [meeting, setMeeting] = useState("Weekly product sync");
  const [transcript, setTranscript] = useState(DEFAULT_TRANSCRIPT);
  const [status, setStatus] = useState<StatusResult | null>(null);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [result, setResult] = useState<AgentResult | null>(null);
  const [processedTranscript, setProcessedTranscript] = useState("");
  const [items, setItems] = useState<ActionItem[]>([]);
  const [selected, setSelected] = useState<Record<number, boolean>>({});
  const [filed, setFiled] = useState<Record<number, CreatedTask>>({});
  const [failed, setFailed] = useState<Record<number, string>>({});
  const [filter, setFilter] = useState<"all" | Decision>("all");
  const [activeEntry, setActiveEntry] = useState<number | null>(null);
  const [editing, setEditing] = useState(true);
  const [dragging, setDragging] = useState(false);

  const fileInput = useRef<HTMLInputElement>(null);
  const entryRefs = useRef<Record<number, HTMLElement | null>>({});
  const noticeTimer = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    const draft = readDraft();
    if (draft?.transcript) {
      // Restored after hydration on purpose: localStorage doesn't exist during server render.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setTranscript(draft.transcript);
      setMeeting(draft.meeting ?? "");
    }
    fetch("/api/status")
      .then((response) => response.json())
      .then(setStatus)
      .catch(() => setStatus(null));
    fetch("/api/samples")
      .then((response) => response.json())
      .then(setSamples)
      .catch(() => setSamples([]));
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(DRAFT_KEY, JSON.stringify({ meeting, transcript }));
    } catch {
      // Storage can be unavailable (private mode); the draft just won't persist.
    }
  }, [meeting, transcript]);

  const flash = useCallback((message: string) => {
    setNotice(message);
    clearTimeout(noticeTimer.current);
    noticeTimer.current = setTimeout(() => setNotice(null), 2600);
  }, []);

  const reviewItems = useMemo(() => result?.review_items ?? [], [result]);
  const isStale = Boolean(result) && transcript !== processedTranscript;
  const showMarked = Boolean(result) && !editing && !isStale;

  const lines = useMemo(() => splitLines(processedTranscript), [processedTranscript]);
  const lineForEntry = useMemo(() => locateQuotes(lines, reviewItems), [lines, reviewItems]);
  const marks = useMemo(() => {
    const map = new Map<number, { entry: number; decision: Decision }[]>();
    lineForEntry.forEach((line, entry) => {
      if (line < 0) return;
      map.set(line, [...(map.get(line) ?? []), { entry, decision: reviewItems[entry].decision }]);
    });
    return map;
  }, [lineForEntry, reviewItems]);

  const counts = useMemo(() => {
    const tally: Record<"all" | Decision, number> = { all: reviewItems.length, new: 0, needs_review: 0, duplicate: 0 };
    reviewItems.forEach((review) => tally[review.decision]++);
    return tally;
  }, [reviewItems]);

  const pending = reviewItems.map((_, index) => index).filter((index) => selected[index] && !filed[index]);
  const filedCount = Object.keys(filed).length;
  const trackerName = (result?.engine.tracker ?? (status?.notion ? "notion" : "demo")) === "notion" ? "Notion" : "the demo tracker";
  const visible = reviewItems.map((review, index) => ({ review, index })).filter(({ review }) => filter === "all" || review.decision === filter);

  async function processTranscript() {
    if (!transcript.trim() || phase !== "idle") return;
    setPhase("processing");
    setError(null);
    try {
      const response = await fetch("/api/process", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transcript, source_meeting: meeting })
      });
      const payload = await response.json().catch(() => ({ error: "The server returned an unreadable response." }));
      if (!response.ok) throw new Error(payload.error || "Processing failed.");
      const next = payload as AgentResult;
      setResult(next);
      setProcessedTranscript(transcript);
      setItems(next.review_items.map((review) => ({ ...review.action_item })));
      setSelected(Object.fromEntries(next.review_items.map((review, index) => [index, review.decision === "new"])));
      setFiled({});
      setFailed({});
      setFilter("all");
      setEditing(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Processing failed.");
    } finally {
      setPhase("idle");
    }
  }

  async function fileTasks() {
    if (!pending.length || phase !== "idle") return;
    const indices = [...pending];
    setPhase("filing");
    setError(null);
    try {
      const response = await fetch("/api/create-tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          items: indices.map((index) => ({ action_item: items[index], decision: reviewItems[index].decision })),
          source_meeting: meeting
        })
      });
      const payload = await response.json().catch(() => ({ error: "The server returned an unreadable response." }));
      if (!response.ok) throw new Error(payload.error || "Filing failed.");
      const outcome = payload as CreateResult;
      const nextFiled: Record<number, CreatedTask> = {};
      const nextFailed: Record<number, string> = {};
      outcome.created.forEach((task) => (nextFiled[indices[task.index]] = task));
      outcome.failed.forEach((failure) => (nextFailed[indices[failure.index]] = failure.reason));
      outcome.skipped.forEach((skip) => (nextFailed[indices[skip.index]] = "Add a task title first."));
      setFiled((current) => ({ ...current, ...nextFiled }));
      setFailed((current) => {
        const merged = { ...current, ...nextFailed };
        Object.keys(nextFiled).forEach((key) => delete merged[Number(key)]);
        return merged;
      });
      const filedNow = outcome.created.length;
      const problems = outcome.failed.length + outcome.skipped.length;
      flash(
        problems
          ? `Filed ${filedNow} of ${indices.length}. ${problems} need attention.`
          : `Filed ${filedNow} ${filedNow === 1 ? "task" : "tasks"} to ${trackerName}.`
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Filing failed.");
    } finally {
      setPhase("idle");
    }
  }

  async function loadFile(file: File) {
    if (file.size > 2_000_000) {
      setError(`${file.name} is larger than 2 MB. Paste the relevant part instead.`);
      return;
    }
    const text = normalizeTranscriptFile(file.name, await file.text());
    setTranscript(text);
    setEditing(true);
    if (!meeting.trim() || meeting === "Weekly product sync") setMeeting(file.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " "));
    flash(`Loaded ${file.name}`);
  }

  function selectEntry(entry: number) {
    setFilter("all");
    setActiveEntry(entry);
    requestAnimationFrame(() => entryRefs.current[entry]?.scrollIntoView({ block: "center", behavior: "smooth" }));
  }

  function setAll(predicate: (decision: Decision) => boolean) {
    setSelected(Object.fromEntries(reviewItems.map((review, index) => [index, predicate(review.decision)])));
  }

  async function copyMarkdown() {
    const rows = reviewItems.map((review, index) => ({ item: items[index], decision: review.decision }));
    try {
      await navigator.clipboard.writeText(toMarkdown(meeting, rows));
      flash("Copied the list as Markdown");
    } catch {
      setError("The clipboard isn't available in this browser.");
    }
  }

  function downloadTrace() {
    if (!result) return;
    const blob = new Blob([JSON.stringify(result, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = result.log_path.split("/").pop() || "docket-trace.json";
    link.click();
    URL.revokeObjectURL(url);
  }

  const engine = result?.engine;
  const model = engine ? (engine.extraction === "gemini" ? engine.model : null) : status?.model;
  const extractionLabel = !engine && !status ? "Checking…" : model ? modelName(model) : "Rule-based extraction";
  const extractionOn = engine ? engine.extraction === "gemini" : Boolean(status?.gemini);
  const trackerOn = engine ? engine.tracker === "notion" : Boolean(status?.notion);

  const scriptLines = phase === "processing" ? splitLines(transcript) : lines;
  const showScript = showMarked || phase === "processing";
  const speakerCount = new Set(splitLines(transcript).map((line) => line.speaker).filter(Boolean)).size;
  const lineCount = splitLines(transcript).length;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <svg className="brand-mark" viewBox="0 0 28 28" aria-hidden="true">
            <rect x="4" y="3.5" width="20" height="21" rx="3.5" />
            <path className="brand-stroke" d="M8.5 11h11" />
            <path d="M8.5 16h7M8.5 20h4.5" />
          </svg>
          <span className="brand-name">Docket</span>
        </div>
        <div className="topbar-right">
          <div className="connections" aria-label="Connections">
            <span className={`conn${extractionOn ? " on" : ""}`}>
              <span className="dot" aria-hidden="true" />
              {extractionLabel}
            </span>
            <span className={`conn${trackerOn ? " on" : ""}`}>
              <span className="dot" aria-hidden="true" />
              {trackerOn ? "Notion" : "Demo tracker"}
              {result ? <span className="conn-sub">{result.open_task_count} open</span> : null}
            </span>
          </div>
          <ThemeToggle />
        </div>
      </header>

      <main className="desk">
        <section
          className={`paper${dragging ? " is-dragging" : ""}`}
          aria-label="Transcript"
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);
          }}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            const file = event.dataTransfer.files[0];
            if (file) void loadFile(file);
          }}
        >
          <div className="paper-head">
            <label htmlFor="meeting" className="visually-hidden">
              Meeting name
            </label>
            <input
              id="meeting"
              className="meeting-title"
              value={meeting}
              onChange={(event) => setMeeting(event.target.value)}
              placeholder="Name this meeting"
              autoComplete="off"
            />
            <p className="paper-meta">
              {lineCount ? `${lineCount} ${lineCount === 1 ? "line" : "lines"}, ${speakerCount} ${speakerCount === 1 ? "speaker" : "speakers"}` : "Empty transcript"}
              {isStale ? <span className="stale"> Edited since processing</span> : null}
            </p>
          </div>

          <div className="paper-body">
            {showScript ? (
              <TranscriptView
                lines={scriptLines}
                marks={marks}
                activeEntry={activeEntry}
                reading={phase === "processing"}
                onHoverEntry={setActiveEntry}
                onSelectEntry={selectEntry}
              />
            ) : (
              <>
                <label htmlFor="transcript" className="visually-hidden">
                  Transcript
                </label>
                <textarea
                  id="transcript"
                  className="transcript-input"
                  value={transcript}
                  onChange={(event) => setTranscript(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                      event.preventDefault();
                      void processTranscript();
                    }
                  }}
                  placeholder={"Paste a transcript, one speaker per line:\n\nPriya: Het, can you send the launch notes by Friday?\nHet: Yes, I'll have them ready."}
                  spellCheck={false}
                />
              </>
            )}
            {dragging ? (
              <div className="drop-hint" aria-hidden="true">
                <FileUp size={26} />
                Drop a .txt, .vtt or .srt file
              </div>
            ) : null}
          </div>

          <div className="paper-actions">
            {showMarked ? (
              <button type="button" className="btn secondary" onClick={() => setEditing(true)}>
                <PencilLine size={16} aria-hidden="true" />
                Edit transcript
              </button>
            ) : (
              <button
                type="button"
                className="btn primary"
                onClick={() => void processTranscript()}
                disabled={phase !== "idle" || !transcript.trim()}
              >
                {phase === "processing" ? <Loader2 size={16} className="spin" aria-hidden="true" /> : null}
                {phase === "processing" ? "Reading…" : result && isStale ? "Process again" : "Find action items"}
                {phase === "idle" ? <kbd>⌘↵</kbd> : null}
              </button>
            )}
            <div className="paper-tools">
              <button type="button" className="btn ghost" onClick={() => fileInput.current?.click()}>
                <FileUp size={16} aria-hidden="true" />
                Open file
              </button>
              <input
                ref={fileInput}
                type="file"
                accept={ACCEPTED_FILES}
                hidden
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void loadFile(file);
                  event.target.value = "";
                }}
              />
              {samples.length ? (
                <select
                  className="sample-select"
                  aria-label="Load a sample transcript"
                  value=""
                  onChange={(event) => {
                    const sample = samples.find((entry) => entry.id === event.target.value);
                    if (!sample) return;
                    setTranscript(sample.transcript);
                    setMeeting(sample.label);
                    setEditing(true);
                  }}
                >
                  <option value="" disabled>
                    Samples
                  </option>
                  {samples.map((sample) => (
                    <option key={sample.id} value={sample.id}>
                      {sample.label}
                    </option>
                  ))}
                </select>
              ) : null}
            </div>
          </div>
        </section>

        <section className="board" aria-label="Action items" aria-busy={phase === "processing"}>
          {error ? (
            <div className="banner error" role="alert">
              <AlertTriangle size={16} aria-hidden="true" />
              <span>{error}</span>
              <button type="button" className="icon-btn" onClick={() => setError(null)} aria-label="Dismiss">
                <X size={15} />
              </button>
            </div>
          ) : null}

          {phase === "processing" ? (
            <div className="working">
              <h1 className="board-title">Reading the transcript</h1>
              <p className="board-sub">
                Finding who promised what, then checking each one against{" "}
                {trackerOn ? "your open Notion tasks" : "the demo tracker"}.
              </p>
              <div className="ghost-slips">
                {[0, 1, 2].map((row) => (
                  <div className="ghost-slip" key={row} style={{ animationDelay: `${row * 160}ms` }}>
                    <span />
                    <span />
                  </div>
                ))}
              </div>
            </div>
          ) : !result ? (
            <div className="hero">
              <h1>
                Every promise from the meeting, checked before it reaches {status?.notion ? "Notion" : "your tracker"}.
              </h1>
              <p className="hero-sub">
                Paste a transcript or drop in a Zoom, Meet or Teams caption file. Docket marks each commitment, finds who
                owns it and when it&rsquo;s due, and flags anything already in {status?.notion ? "Notion" : "the tracker"}.
              </p>

              <div className="demo" aria-hidden="true">
                <div className="demo-paper">
                  <div className="demo-line">
                    <span className="avatar">P</span>
                    <p>Het, can you send the launch notes by Friday?</p>
                  </div>
                  <div className="demo-line">
                    <span className="avatar">H</span>
                    <p>
                      <mark className="demo-mark">Yes, I&rsquo;ll have the launch notes ready Friday.</mark>
                      <span className="ref hl-new">1</span>
                    </p>
                  </div>
                </div>
                <div className="demo-slip">
                  <div className="demo-slip-head">
                    <span className="ref hl-new">1</span>
                    <strong>Send the launch notes</strong>
                    <span className="decision hl-new">New</span>
                  </div>
                  <div className="demo-slip-meta">
                    <span className="chip">
                      <span className="avatar small">H</span>Het
                    </span>
                    <span className="chip">Fri, Oct 30</span>
                  </div>
                </div>
              </div>

              <ol className="steps">
                <li>
                  <strong>Add the transcript</strong>
                  <span>Paste, open or drop a file on the page.</span>
                </li>
                <li>
                  <strong>Check what was promised</strong>
                  <span>Fix owners and dates, skip duplicates.</span>
                </li>
                <li>
                  <strong>File what you approve</strong>
                  <span>Nothing is created until you do.</span>
                </li>
              </ol>
            </div>
          ) : (
            <>
              <div className="board-head">
                <div className="board-heading">
                  <span className="board-count">{reviewItems.length}</span>
                  <div>
                    <h1 className="board-title">
                      {reviewItems.length === 1 ? "action item" : "action items"}
                    </h1>
                    {result.summary ? <p className="board-sub">{result.summary}</p> : null}
                  </div>
                </div>
                <div className="board-tools">
                  <button type="button" className="btn on-desk" onClick={copyMarkdown} disabled={!reviewItems.length}>
                    <ClipboardCopy size={15} aria-hidden="true" />
                    Copy list
                  </button>
                  <button type="button" className="btn on-desk" onClick={downloadTrace}>
                    <Download size={15} aria-hidden="true" />
                    Trace
                  </button>
                </div>
              </div>

              {result.warnings.map((warning) => (
                <div className="banner warn" key={warning}>
                  <AlertTriangle size={16} aria-hidden="true" />
                  <span>{warning}</span>
                </div>
              ))}

              {reviewItems.length ? (
                <div className="ribbon-wrap">
                  <div className="ribbon" aria-hidden="true">
                    {DECISIONS.filter((key) => counts[key]).map((key) => (
                      <span key={key} className={`ribbon-seg hl-${key}${filter !== "all" && filter !== key ? " is-dim" : ""}`} style={{ flexGrow: counts[key] }} />
                    ))}
                  </div>
                  <div className="ribbon-legend" role="group" aria-label="Show">
                    {DECISIONS.map((key) => (
                      <button
                        type="button"
                        key={key}
                        className={`legend hl-${key}${filter === key ? " is-on" : ""}`}
                        aria-pressed={filter === key}
                        disabled={counts[key] === 0}
                        onClick={() => setFilter((current) => (current === key ? "all" : key))}
                      >
                        <span className="swatch" aria-hidden="true" />
                        <strong>{counts[key]}</strong> {decisionLabel(key).toLowerCase()}
                      </button>
                    ))}
                    {filter !== "all" ? (
                      <button type="button" className="legend-reset" onClick={() => setFilter("all")}>
                        Show all
                      </button>
                    ) : null}
                  </div>
                </div>
              ) : (
                <div className="empty-result">
                  <p>Nobody committed to anything in this meeting, so there&rsquo;s nothing to file.</p>
                  <button type="button" className="btn secondary" onClick={() => setEditing(true)}>
                    <PencilLine size={16} aria-hidden="true" />
                    Edit transcript
                  </button>
                </div>
              )}

              <div className="slips">
                {visible.map(({ review, index }) => (
                  <EntryCard
                    key={index}
                    ref={(node) => {
                      entryRefs.current[index] = node;
                    }}
                    index={index}
                    review={review}
                    item={items[index]}
                    trackerName={trackerName === "Notion" ? "Notion" : "the tracker"}
                    selected={Boolean(selected[index])}
                    filed={filed[index]}
                    failure={failed[index]}
                    active={activeEntry === index}
                    hasSource={lineForEntry[index] >= 0}
                    onToggle={(checked) => setSelected((current) => ({ ...current, [index]: checked }))}
                    onChange={(patch) =>
                      setItems((current) => current.map((item, i) => (i === index ? { ...item, ...patch } : item)))
                    }
                    onHover={(on) => setActiveEntry((current) => (on ? index : current === index ? null : current))}
                    onLocate={() => {
                      setEditing(false);
                      setActiveEntry(index);
                    }}
                  />
                ))}
              </div>

              {reviewItems.length ? (
                <div className="dock">
                  <div className="dock-info">
                    <span className="dock-count">
                      {pending.length
                        ? `${pending.length} of ${reviewItems.length - filedCount} selected`
                        : filedCount
                          ? `${filedCount} filed`
                          : "Nothing selected"}
                    </span>
                    <span className="dock-links">
                      <button type="button" className="link-btn" onClick={() => setAll((decision) => decision !== "duplicate")}>
                        All but duplicates
                      </button>
                      <button type="button" className="link-btn" onClick={() => setAll(() => false)}>
                        Clear
                      </button>
                    </span>
                  </div>
                  <button
                    type="button"
                    className="btn file"
                    onClick={() => void fileTasks()}
                    disabled={!pending.length || phase !== "idle"}
                  >
                    {phase === "filing" ? <Loader2 size={16} className="spin" aria-hidden="true" /> : <Check size={17} strokeWidth={2.6} aria-hidden="true" />}
                    {phase === "filing"
                      ? "Filing…"
                      : pending.length
                        ? `File ${pending.length} ${pending.length === 1 ? "task" : "tasks"} to ${trackerName}`
                        : `File to ${trackerName}`}
                  </button>
                </div>
              ) : null}
            </>
          )}
        </section>
      </main>

      <div className="toast-region" role="status" aria-live="polite">
        {notice ? (
          <div className="toast">
            <Check size={15} strokeWidth={2.6} aria-hidden="true" />
            {notice}
          </div>
        ) : null}
      </div>
    </div>
  );
}
