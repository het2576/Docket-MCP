import type { ActionItem, ReviewItem } from "@/lib/types";

export type TranscriptLine = { speaker: string | null; text: string; raw: string };

const SPEAKER = /^\s*([A-Z][\p{L} .'-]{0,40}?):\s+(.*)$/u;

export function splitLines(transcript: string): TranscriptLine[] {
  return transcript
    .split(/\r?\n/)
    .filter((line) => line.trim())
    .map((raw) => {
      const match = raw.match(SPEAKER);
      return match ? { speaker: match[1].trim(), text: match[2], raw } : { speaker: null, text: raw.trim(), raw };
    });
}

/** Turn Zoom/Meet/Teams caption exports (WebVTT, SRT) into "Speaker: text" lines. */
export function normalizeTranscriptFile(name: string, content: string): string {
  const isCaptions = /\.(vtt|srt)$/i.test(name) || content.trimStart().startsWith("WEBVTT");
  if (!isCaptions) return content.trim();

  const lines: string[] = [];
  for (const block of content.replace(/\r/g, "").split(/\n{2,}/)) {
    const text = block
      .split("\n")
      .filter((line) => line.trim() && !/^WEBVTT/.test(line) && !/^\d+$/.test(line.trim()) && !line.includes("-->"))
      .filter((line) => !/^(NOTE|STYLE|REGION)\b/.test(line))
      .join(" ")
      .trim();
    if (!text) continue;
    const voice = text.match(/^<v\s+([^>]+)>(.*?)(<\/v>)?$/);
    const line = voice ? `${voice[1].trim()}: ${voice[2].trim()}` : text.replace(/<[^>]+>/g, "");
    // Captions split one turn across cues; merge consecutive cues from the same speaker.
    const previous = lines[lines.length - 1];
    const speaker = line.match(SPEAKER)?.[1];
    if (previous && speaker && previous.startsWith(`${speaker}: `)) {
      lines[lines.length - 1] = `${previous} ${line.slice(speaker.length + 2)}`;
    } else {
      lines.push(line);
    }
  }
  return lines.join("\n");
}

function normalize(text: string) {
  return text
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
}

/** Find the transcript line each review item was quoted from (index into splitLines), or -1. */
export function locateQuotes(lines: TranscriptLine[], items: ReviewItem[]): number[] {
  const normalizedLines = lines.map((line) => normalize(line.raw));
  return items.map(({ action_item }) => {
    const quote = normalize(action_item.source_quote || "");
    if (!quote) return -1;
    const exact = normalizedLines.findIndex((line) => line.includes(quote) || (line.length > 12 && quote.includes(line)));
    if (exact !== -1) return exact;

    const quoteWords = new Set(quote.split(" "));
    let best = -1;
    let bestScore = 0.6;
    normalizedLines.forEach((line, index) => {
      const words = line.split(" ");
      const overlap = words.filter((word) => quoteWords.has(word)).length / Math.max(quoteWords.size, 1);
      if (overlap > bestScore) {
        best = index;
        bestScore = overlap;
      }
    });
    return best;
  });
}

const DECISION_LABEL = { new: "New", needs_review: "Needs review", duplicate: "Duplicate" } as const;

export function decisionLabel(decision: ReviewItem["decision"]) {
  return DECISION_LABEL[decision];
}

export function toMarkdown(meeting: string, items: { item: ActionItem; decision: ReviewItem["decision"] }[]) {
  const rows = items.map(({ item, decision }) => {
    const meta = [item.owner || "Unassigned", item.deadline ? `due ${item.deadline}` : null, decisionLabel(decision)]
      .filter(Boolean)
      .join(", ");
    return `- [ ] ${item.task} (${meta})`;
  });
  return [`**Action items: ${meeting || "Meeting"}**`, "", ...rows].join("\n");
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** "2026-10-30" → "Fri, Oct 30"; anything else is shown as written. */
export function formatDue(value: string | null) {
  if (!value) return null;
  if (!ISO_DATE.test(value)) return value;
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}
