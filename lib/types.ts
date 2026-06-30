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

export type ReviewItem = {
  action_item: ActionItem;
  decision: "new" | "duplicate" | "needs_review";
  reasoning: string;
  similar_tasks: TrackerTask[];
};

export type AgentResult = {
  source_meeting: string | null;
  summary: string;
  review_items: ReviewItem[];
  log_path: string;
};

export type CreatedTask = {
  id: string;
  title: string;
  owner: string | null;
  due_date: string | null;
  status: string | null;
  url: string | null;
  mock?: boolean;
};

export type CreateResult = {
  created: CreatedTask[];
  skipped: { item: unknown; reason: string }[];
};
