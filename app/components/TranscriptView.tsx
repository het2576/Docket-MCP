"use client";

import { useEffect, useRef } from "react";
import type { TranscriptLine } from "@/lib/transcript";
import type { Decision } from "@/lib/types";

type Mark = { entry: number; decision: Decision };

type Props = {
  lines: TranscriptLine[];
  marks: Map<number, Mark[]>;
  activeEntry: number | null;
  reading?: boolean;
  onHoverEntry: (entry: number | null) => void;
  onSelectEntry: (entry: number) => void;
};

export function initials(name: string) {
  const parts = name.trim().split(/\s+/);
  return ((parts[0]?.[0] ?? "") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}

/** The transcript as a script: speakers in the gutter, a highlighter stroke behind every line that produced an entry. */
export function TranscriptView({ lines, marks, activeEntry, reading = false, onHoverEntry, onSelectEntry }: Props) {
  const lineRefs = useRef<Record<number, HTMLDivElement | null>>({});

  useEffect(() => {
    if (activeEntry === null) return;
    for (const [lineIndex, lineMarks] of marks) {
      if (lineMarks.some((mark) => mark.entry === activeEntry)) {
        lineRefs.current[lineIndex]?.scrollIntoView({ block: "nearest", behavior: "smooth" });
        return;
      }
    }
  }, [activeEntry, marks]);

  let markOrder = 0;
  return (
    <div className={`script${reading ? " is-reading" : ""}`}>
      {lines.map((line, index) => {
        const lineMarks = reading ? undefined : marks.get(index);
        const previousSpeaker = index > 0 ? lines[index - 1].speaker : null;
        const continued = Boolean(line.speaker) && line.speaker === previousSpeaker;
        const isActive = lineMarks?.some((mark) => mark.entry === activeEntry) ?? false;

        let text: React.ReactNode = <span className="script-text">{line.text}</span>;
        if (lineMarks) {
          const first = lineMarks[0];
          const delay = 250 + markOrder++ * 140;
          text = (
            <>
              <a
                href={`#entry-${first.entry}`}
                className={`highlight hl-${first.decision}${isActive ? " is-active" : ""}`}
                style={{ animationDelay: `${delay}ms` }}
                onMouseEnter={() => onHoverEntry(first.entry)}
                onMouseLeave={() => onHoverEntry(null)}
                onFocus={() => onHoverEntry(first.entry)}
                onBlur={() => onHoverEntry(null)}
                onClick={(event) => {
                  event.preventDefault();
                  onSelectEntry(first.entry);
                }}
                aria-label={`Go to item ${lineMarks.map((mark) => mark.entry + 1).join(" and ")}: ${line.text}`}
              >
                {line.text}
              </a>
              {lineMarks.map((mark) => (
                <span key={mark.entry} className={`ref hl-${mark.decision}`} aria-hidden="true">
                  {mark.entry + 1}
                </span>
              ))}
            </>
          );
        }

        return (
          <div
            className={`script-line${lineMarks ? " is-marked" : ""}${isActive ? " is-active" : ""}`}
            key={index}
            ref={(node) => {
              lineRefs.current[index] = node;
            }}
            style={reading ? ({ "--read-delay": `${index * 320}ms` } as React.CSSProperties) : undefined}
          >
            <div className="script-speaker">
              {line.speaker && !continued ? (
                <>
                  <span className="avatar" aria-hidden="true">
                    {initials(line.speaker)}
                  </span>
                  <span className="speaker-name">{line.speaker}</span>
                </>
              ) : null}
            </div>
            <p className="script-body">{text}</p>
          </div>
        );
      })}
    </div>
  );
}
