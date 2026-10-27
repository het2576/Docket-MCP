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
      source_meeting: typeof body.source_meeting === "string" ? body.source_meeting.trim() : undefined
    });
    return NextResponse.json(result);
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : "Unknown error" }, { status: 500 });
  }
}
