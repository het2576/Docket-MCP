export type ActionItem = {
  task: string;
  owner: string | null;
  deadline: string | null;
  confidence: number;
  source_quote: string;
};

export type TrackerTask = {
  id: string;
  title: string;
  owner: string | null;
  due_date: string | null;
  status: string | null;
  url: string | null;
};

export type SimilarTask = TrackerTask & {
  similarity: number;
  lexical: number;
  semantic: number | null;
};

export type Decision = "new" | "duplicate" | "needs_review";

export type ReviewItem = {
  action_item: ActionItem;
  decision: Decision;
  reasoning: string;
  similar_tasks: SimilarTask[];
};

export type EngineInfo = {
  extraction: "gemini" | "heuristic";
  model: string | null;
  tracker: "notion" | "demo";
  semantic_search: boolean;
};

export type AgentResult = {
  source_meeting: string | null;
  summary: string;
  review_items: ReviewItem[];
  open_task_count: number;
  engine: EngineInfo;
  warnings: string[];
  trace: unknown[];
  log_path: string;
};

export type CreatedTask = TrackerTask & {
  index: number;
  mock?: boolean;
};

export type CreateResult = {
  created: CreatedTask[];
  skipped: { index: number; reason: string }[];
  failed: { index: number; title: string; reason: string }[];
};

export type StatusResult = {
  gemini: boolean;
  model: string | null;
  notion: boolean;
};
