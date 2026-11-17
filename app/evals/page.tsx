import { readFile } from "node:fs/promises";
import path from "node:path";
import type { Metadata } from "next";
import Link from "next/link";
import { ThemeToggle } from "@/app/components/ThemeToggle";
import { EvalsView, type Dashboard } from "@/app/evals/EvalsView";
import { FlaskConical, NotebookPen } from "lucide-react";

export const dynamic = "force-dynamic";
export const metadata: Metadata = {
  title: "Docket · Evals",
  description: "How accurate Docket is, measured on hand-labelled meetings with a held-out test split."
};

async function loadDashboard(): Promise<Dashboard | null> {
  try {
    const file = path.join(process.cwd(), "evals", "results", "dashboard.json");
    return JSON.parse(await readFile(file, "utf8")) as Dashboard;
  } catch {
    return null;
  }
}

export default async function EvalsPage() {
  const data = await loadDashboard();
  return (
    <div className="app ev-app">
      <header className="topbar">
        <Link href="/" className="brand" aria-label="Docket home">
          <svg className="brand-mark" viewBox="0 0 28 28" aria-hidden="true">
            <rect x="4" y="3.5" width="20" height="21" rx="3.5" />
            <path className="brand-stroke" d="M8.5 11h11" />
            <path d="M8.5 16h7M8.5 20h4.5" />
          </svg>
          <span className="brand-name">Docket</span>
        </Link>
        <div className="topbar-right">
          <Link href="/" className="nav-link">
            <NotebookPen size={15} aria-hidden="true" />
            Review
          </Link>
          <Link href="/evals" className="nav-link" aria-current="page">
            <FlaskConical size={15} aria-hidden="true" />
            Evals
          </Link>
          <ThemeToggle />
        </div>
      </header>
      {data ? (
        <EvalsView data={data} />
      ) : (
        <main className="ev-main">
          <section className="ev-paper ev-empty">
            <h1>No eval results yet</h1>
            <p>Run the suite, then build the report this page reads:</p>
            <pre>
              <code>
                python -m evals.run --label v2{"\n"}python -m evals.report baseline v2
              </code>
            </pre>
          </section>
        </main>
      )}
    </div>
  );
}
