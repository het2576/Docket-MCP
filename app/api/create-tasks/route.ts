import { NextRequest, NextResponse } from "next/server";
import { runPython } from "@/lib/server/pythonRunner";

export async function POST(request: NextRequest) {
  const body = await request.json().catch(() => null);
  if (!Array.isArray(body?.items) || body.items.length === 0) {
    return NextResponse.json({ error: "Select at least one item to file." }, { status: 400 });
  }
  if (body.items.length > 100) {
    return NextResponse.json({ error: "File at most 100 tasks at a time." }, { status: 413 });
  }

  try {
    const result = await runPython("create", {
      items: body.items,
      source_meeting: typeof body.source_meeting === "string" ? body.source_meeting.trim() : undefined,
      meeting_date: typeof body.meeting_date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(body.meeting_date) ? body.meeting_date : undefined,
      // What Docket proposed vs what the user approved, for online metrics (logs/feedback.jsonl).
      feedback: Array.isArray(body.feedback) && body.feedback.length <= 200 ? body.feedback : undefined,
      engine: typeof body.engine === "object" && body.engine !== null ? body.engine : undefined,
      trace_path: typeof body.trace_path === "string" ? body.trace_path : undefined
    });
    return NextResponse.json(result);
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : "Unknown error" }, { status: 500 });
  }
}
