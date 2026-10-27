"use client";

import { AlertCircle, CalendarDays, Check, ChevronDown, ExternalLink, Quote } from "lucide-react";
import { forwardRef, useState } from "react";
import { initials } from "@/app/components/TranscriptView";
import { decisionLabel, formatDue } from "@/lib/transcript";
import type { ActionItem, CreatedTask, ReviewItem } from "@/lib/types";

type Props = {
  index: number;
  review: ReviewItem;
  item: ActionItem;
  trackerName: string;
  selected: boolean;
  filed: CreatedTask | undefined;
  failure: string | undefined;
  active: boolean;
  hasSource: boolean;
  onToggle: (checked: boolean) => void;
  onChange: (patch: Partial<ActionItem>) => void;
  onHover: (active: boolean) => void;
  onLocate: () => void;
};

function percent(value: number | null | undefined) {
  return value === null || value === undefined ? "–" : `${Math.round(value * 100)}%`;
}

/** One plain-language line that says why the item got its decision. */
function verdict(review: ReviewItem, tracker: string) {
  const best = review.similar_tasks[0];
  if (review.action_item.confidence < 0.6) return "The transcript is vague about what this is or who owns it.";
  if (!best) return `Nothing like this in ${tracker} yet.`;
  const score = Math.round(Math.max(best.lexical, best.semantic ?? 0) * 100);
  if (review.decision === "duplicate") return `Already in ${tracker} as “${best.title}” (${score}% match).`;
  if (review.decision === "needs_review") return `Close to “${best.title}” in ${tracker} (${score}% match).`;
  return `Nothing close in ${tracker}. Nearest is “${best.title}” at ${score}%.`;
}

function DueChip({ value, locked, onChange }: { value: string | null; locked: boolean; onChange: (v: string | null) => void }) {
  const [editing, setEditing] = useState(false);
  const pretty = formatDue(value);
  return (
    <label className={`chip${value ? "" : " is-empty"}`}>
      <CalendarDays size={14} aria-hidden="true" />
      <span className="visually-hidden">Due date</span>
      <input
        value={editing || !value ? (value ?? "") : (pretty ?? "")}
        readOnly={locked}
        placeholder="No due date"
        onFocus={() => setEditing(true)}
        onBlur={() => setEditing(false)}
        onChange={(event) => onChange(event.target.value || null)}
      />
    </label>
  );
}

export const EntryCard = forwardRef<HTMLElement, Props>(function EntryCard(
  { index, review, item, trackerName, selected, filed, failure, active, hasSource, onToggle, onChange, onHover, onLocate },
  ref
) {
  const [open, setOpen] = useState(false);
  const locked = Boolean(filed);
  const titleId = `entry-${index}-title`;
  const detailsId = `entry-${index}-details`;

  return (
    <article
      ref={ref}
      id={`entry-${index}`}
      className={[
        "slip",
        `decision-${review.decision}`,
        selected || locked ? "is-selected" : "is-aside",
        active ? "is-active" : "",
        locked ? "is-filed" : "",
        open ? "is-open" : ""
      ].join(" ")}
      onMouseEnter={() => onHover(true)}
      onMouseLeave={() => onHover(false)}
      aria-labelledby={titleId}
    >
      <div className="slip-main">
        <div className="slip-select">
          {locked ? (
            <span className="filed-check" title="Filed">
              <Check size={15} strokeWidth={3} aria-hidden="true" />
            </span>
          ) : (
            <input
              type="checkbox"
              className="checkbox"
              checked={selected}
              onChange={(event) => onToggle(event.target.checked)}
              aria-label={`File “${item.task || "untitled"}”`}
            />
          )}
        </div>

        <div className="slip-content">
          <div className="slip-head">
            <span className={`ref hl-${review.decision}`} aria-hidden="true">
              {index + 1}
            </span>
            <label className="visually-hidden" htmlFor={titleId}>
              Task
            </label>
            <textarea
              id={titleId}
              className="slip-title"
              value={item.task}
              rows={1}
              readOnly={locked}
              onChange={(event) => onChange({ task: event.target.value.replace(/\n/g, " ") })}
              placeholder="Describe the task"
            />
            <span className={`decision hl-${review.decision}`}>{decisionLabel(review.decision)}</span>
          </div>

          <div className="slip-meta">
            <label className={`chip${item.owner ? "" : " is-empty"}`}>
              <span className="avatar small" aria-hidden="true">
                {item.owner ? initials(item.owner) : "?"}
              </span>
              <span className="visually-hidden">Owner</span>
              <input
                value={item.owner ?? ""}
                readOnly={locked}
                placeholder="Unassigned"
                onChange={(event) => onChange({ owner: event.target.value || null })}
              />
            </label>
            <DueChip value={item.deadline} locked={locked} onChange={(deadline) => onChange({ deadline })} />
            <span className="clarity" title="How clearly the transcript states the task, owner and date">
              <span className="clarity-bar" aria-hidden="true">
                <span style={{ width: `${Math.round(item.confidence * 100)}%` }} />
              </span>
              {percent(item.confidence)} clear
            </span>
          </div>

          <div className="slip-verdict">
            <p>{verdict(review, trackerName)}</p>
            <button
              type="button"
              className="why"
              aria-expanded={open}
              aria-controls={detailsId}
              onClick={() => setOpen((value) => !value)}
            >
              Why
              <ChevronDown size={15} aria-hidden="true" />
            </button>
          </div>

          {filed ? (
            <p className="filed-note">
              {filed.mock ? "Filed to the demo tracker" : "Filed to Notion"}
              {filed.due_date ? `, due ${formatDue(filed.due_date)}` : ""}
              {filed.url ? (
                <a href={filed.url} target="_blank" rel="noopener noreferrer">
                  Open in Notion
                  <ExternalLink size={13} aria-hidden="true" />
                </a>
              ) : null}
            </p>
          ) : null}

          {failure ? (
            <p className="failure-note" role="alert">
              <AlertCircle size={15} aria-hidden="true" />
              Not filed: {failure}
            </p>
          ) : null}
        </div>
      </div>

      {open ? (
        <div className="slip-details" id={detailsId}>
          {review.action_item.source_quote ? (
            <button type="button" className="source" onClick={onLocate} disabled={!hasSource}>
              <Quote size={14} aria-hidden="true" />
              <span>{review.action_item.source_quote}</span>
            </button>
          ) : null}
          <p className="reasoning">{review.reasoning}</p>
          {review.similar_tasks.length ? (
            <table className="match-table">
              <caption className="visually-hidden">Closest open tasks in {trackerName}</caption>
              <thead>
                <tr>
                  <th scope="col">Closest open tasks</th>
                  <th scope="col">Owner</th>
                  <th scope="col">Status</th>
                  <th scope="col" className="num" title="Shared wording">
                    Wording
                  </th>
                  <th scope="col" className="num" title="Similar meaning">
                    Meaning
                  </th>
                </tr>
              </thead>
              <tbody>
                {review.similar_tasks.map((task) => (
                  <tr key={task.id}>
                    <td>
                      {task.url ? (
                        <a href={task.url} target="_blank" rel="noopener noreferrer">
                          {task.title}
                          <ExternalLink size={11} aria-hidden="true" />
                        </a>
                      ) : (
                        task.title
                      )}
                    </td>
                    <td>{task.owner || "–"}</td>
                    <td>{task.status || "–"}</td>
                    <td className="num">{percent(task.lexical)}</td>
                    <td className="num">{percent(task.semantic)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </div>
      ) : null}
    </article>
  );
});
