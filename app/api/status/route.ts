import { NextResponse } from "next/server";
import type { StatusResult } from "@/lib/types";

export const dynamic = "force-dynamic";

export function GET() {
  const status: StatusResult = {
    gemini: Boolean(process.env.GEMINI_API_KEY),
    model: process.env.GEMINI_API_KEY ? process.env.GEMINI_MODEL || "gemini-3.5-flash" : null,
    notion: Boolean(process.env.NOTION_API_KEY && process.env.NOTION_DATABASE_ID)
  };
  return NextResponse.json(status);
}
