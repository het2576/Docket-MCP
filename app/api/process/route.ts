import { NextRequest, NextResponse } from "next/server";
import { runPython } from "@/lib/server/pythonRunner";

const MAX_TRANSCRIPT_CHARS = 200_000;

export async function POST(request: NextRequest) {
  const body = await request.json().catch(() => null);
  const transcript = typeof body?.transcript === "string" ? body.transcript.trim() : "";
  if (!transcript) {
    return NextResponse.json({ error: "Paste a transcript before processing." }, { status: 400 });
  }
  if (transcript.length > MAX_TRANSCRIPT_CHARS) {
    return NextResponse.json(
      { error: `Transcript is ${transcript.length.toLocaleString()} characters; the limit is 200,000.` },
      { status: 413 }
    );
  }

  try {
    const result = await runPython("process", {
      transcript,
      source_meeting: typeof body.source_meeting === "string" ? body.source_meeting.trim() : undefined
    });
    return NextResponse.json(result);
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : "Unknown error" }, { status: 500 });
  }
}
