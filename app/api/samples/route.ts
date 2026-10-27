import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { NextResponse } from "next/server";

const LABELS: Record<string, string> = {
  standup: "Product standup",
  group_project: "Group project",
  zero_actions: "No action items"
};

export async function GET() {
  const dir = path.join(process.cwd(), "samples");
  const files = (await readdir(dir).catch(() => [])).filter((file) => file.endsWith(".txt")).sort();
  const samples = await Promise.all(
    files.map(async (file) => {
      const id = file.replace(/\.txt$/, "");
      return { id, label: LABELS[id] ?? id.replace(/_/g, " "), transcript: (await readFile(path.join(dir, file), "utf8")).trim() };
    })
  );
  samples.sort((a, b) => Number(b.id === "standup") - Number(a.id === "standup"));
  return NextResponse.json(samples);
}
