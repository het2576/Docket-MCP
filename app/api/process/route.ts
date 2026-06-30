import { NextRequest, NextResponse } from "next/server";
import { runPython } from "@/lib/server/pythonRunner";

export async function POST(request: NextRequest) {
  const body = await request.json();
  if (!body.transcript || typeof body.transcript !== "string") {
    return NextResponse.json({ error: "Transcript is required." }, { status: 400 });
  }

  try {
    const result = await runPython("process", {
      transcript: body.transcript,
      source_meeting: body.source_meeting
    });
    return NextResponse.json(result);
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : "Unknown error" }, { status: 500 });
  }
}
