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

export type JudgeVerdict = {
  verdict: "same" | "related" | "different";
  task_id: string | null;
  reason: string;
  item_subject?: string;
};

export type ReviewItem = {
  action_item: ActionItem;
  decision: Decision;
  reasoning: string;
  similar_tasks: SimilarTask[];
  /** The LLM duplicate double-check, when it ran. */
  judge?: JudgeVerdict | null;
};

export type EngineInfo = {
  extraction: "gemini" | "heuristic";
  model: string | null;
  tracker: "notion" | "demo";
  semantic_search: boolean;
  prompt_version?: string | null;
  dedup_judge?: boolean;
  /** Set when the model call failed and the rule-based extractor ran instead. */
  fallback_reason?: string | null;
};

export type StageUsage = { calls: number; ms: number; input_tokens: number; output_tokens: number };

export type RunUsage = {
  calls: number;
  failed_calls: number;
  retries: number;
  model_ms: number;
  wall_ms: number;
  input_tokens: number;
  output_tokens: number;
  by_stage: Record<string, StageUsage>;
};

export type AgentResult = {
  source_meeting: string | null;
  meeting_date?: string;
  summary: string;
  review_items: ReviewItem[];
  open_task_count: number;
  engine: EngineInfo;
  warnings: string[];
  usage?: RunUsage;
  trace: unknown[];
  log_path: string;
};

export type FileMode = "create" | "update";

export type CreatedTask = TrackerTask & {
  index: number;
  mock?: boolean;
  mode?: FileMode;
  note?: string | null;
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
