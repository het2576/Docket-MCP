import { NextRequest, NextResponse } from "next/server";
import { runPython } from "@/lib/server/pythonRunner";

export async function POST(request: NextRequest) {
  const body = await request.json();
  if (!Array.isArray(body.items)) {
    return NextResponse.json({ error: "items must be an array." }, { status: 400 });
  }

  try {
    const result = await runPython("create", {
      items: body.items,
      source_meeting: body.source_meeting
    });
    return NextResponse.json(result);
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : "Unknown error" }, { status: 500 });
  }
}
