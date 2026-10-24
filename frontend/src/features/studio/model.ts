export type StudioRole = "chat" | "report" | "improvement";
export type FeedbackRating = "helpful" | "unhelpful" | null;
export type PlaybookId =
  | "investigation"
  | "bug-fix"
  | "feature"
  | "refactoring"
  | "perf-issue"
  | "reflection";
export interface PstackCatalog {
  name: string;
  repository: string;
  revision: string;
  license: string;
  playbooks: {
    id: PlaybookId;
    title: string;
    description: string;
    url: string;
    steps: string[];
  }[];
  principles: { id: string; title: string; summary: string; url: string }[];
  adaptation: string | string[];
}
export interface PinnedPstack {
  name: string;
  revision: string;
  playbook: string;
  url: string;
}
export type Scope = {
  kind: "workspace" | "project" | "service";
  id: string | null;
};
export type JobStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "interrupted"
  | "cancelled";
export interface Source {
  kind: string;
  id: string;
  label: string;
  observed_at?: number | null;
  stale?: boolean;
}
export interface StudioSession {
  id: string;
  title: string;
  role: StudioRole;
  playbook?: PlaybookId;
  scope: Scope;
  created: number;
  updated: number;
  last_message: string;
  active_job_id: string | null;
  archived: boolean;
}
export interface StudioMessage {
  feedback?: FeedbackRating;
  id: string;
  session_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  status:
    | "queued"
    | "running"
    | "complete"
    | "failed"
    | "interrupted"
    | "cancelled";
  job_id: string | null;
  created: number;
  updated: number;
  sources: (Source | string)[];
}
export interface StudioJob {
  id: string;
  kind: StudioRole;
  session_id: string;
  message_id: string;
  report_id: string | null;
  status: JobStatus;
  created: number;
  updated: number;
  started: number | null;
  finished: number | null;
  error: string | null;
  preference_revision: number;
  problem_ids: string[];
}
export interface Report {
  feedback?: FeedbackRating;
  id: string;
  title: string;
  scope: Scope;
  session_id: string;
  job_id: string;
  status: JobStatus;
  period: "day" | "week" | "custom";
  window_start: number;
  window_end: number;
  created: number;
  updated: number;
  preference_revision: number;
  content?: string | null;
  error: string | null;
  sources: (Source | string)[];
}
export type ProblemStatus =
  | "proposed"
  | "accepted"
  | "dismissed"
  | "in_progress"
  | "done";
export interface Problem {
  feedback?: FeedbackRating;
  id: string;
  title: string;
  summary: string;
  priority: "high" | "medium" | "low";
  status: ProblemStatus;
  scope: Scope;
  evidence_refs: string[];
  validation: string;
  source_job_id: string;
  source_report_id: string | null;
  created: number;
  updated: number;
  revision: number;
  notes: string;
}
export interface Preferences {
  revision: number;
  principles: string[];
  stack: string[];
  review_focus: string[];
  verification: string[];
  notes: string;
  workflow: string[];
  updated: number;
}
export interface InputCoverage {
  limit_bytes: number;
  bytes: number;
  available: Record<string, number>;
  selected: Record<string, number>;
  included: Record<string, number>;
  truncated: boolean;
  omissions: string[];
}
export interface StudioContext {
  pstack?: PinnedPstack;
  source_count?: number;
  generated_at: number;
  scope: Scope;
  monitor?: { status: string; last_cycle: number | null };
  coverage?: {
    historical_measurements: boolean;
    truncated: boolean;
    counts: Record<string, number>;
    included: Record<string, number>;
    prompt?: InputCoverage;
  };
  sources: Source[];
  assumptions: string[];
  notes: string[];
}
export interface StudioOverview {
  pstack?: PstackCatalog;
  sessions: StudioSession[];
  reports: Report[];
  problems: Problem[];
  preferences: Preferences;
  worker: {
    status: "running" | "stale" | "not_started";
    last_cycle: number | null;
    active_job_id: string | null;
    queued: number;
    budget?: { limit: number; used: number; available: number; next_available_at: number | null };
  };
  context: StudioContext;
  counts: Record<string, number>;
  has_more: Record<string, boolean>;
}
export interface SessionPage {
  session: StudioSession;
  messages: StudioMessage[];
  jobs: StudioJob[];
  has_more: boolean;
  next_before: string | null;
  context: StudioContext;
}
export interface CreatedWork {
  session: StudioSession;
  message?: StudioMessage;
  job?: StudioJob;
  report?: Report;
}
export const WORKSPACE: Scope = { kind: "workspace", id: null };
export const ROLE_LABELS: Record<StudioRole, string> = {
  chat: "Investigator",
  report: "Report agent",
  improvement: "Improvement agent",
};
export const problemStates: ProblemStatus[] = [
  "proposed",
  "accepted",
  "in_progress",
  "done",
  "dismissed",
];
export const isActive = (status: string) =>
  status === "queued" || status === "running";
export const humanize = (text: string) => text.replaceAll("_", " ");
export function formatTime(value?: number | null): string {
  return value
    ? new Date(value * 1000).toLocaleString([], {
        dateStyle: "medium",
        timeStyle: "short",
      })
    : "Not recorded";
}
export function errorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "The request failed. Try again.";
}
export function scopeName(
  scope: Scope,
  inventory?: {
    projects?: { id: string; name: string }[];
    services?: { id: string; name: string }[];
  },
): string {
  const records =
    scope.kind === "project" ? inventory?.projects : inventory?.services;
  const name = records?.find((item) => item.id === scope.id)?.name ?? scope.id;
  return scope.kind === "workspace"
    ? "Workspace"
    : `${scope.kind === "project" ? "Project" : "Service"} · ${name}`;
}
export function sessionPreview(content: string): string {
  return content
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/(^|\n)\s{0,3}(?:#{1,6}\s+|>\s*|[-*+]\s+)/g, "$1")
    .replace(/[*_`~]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}
export function mergeMessages(pages: SessionPage[]): StudioMessage[] {
  const messages = new Map<string, StudioMessage>();
  for (const page of [...pages].reverse())
    for (const message of page.messages) messages.set(message.id, message);
  return [...messages.values()].sort((a, b) => a.created - b.created);
}
export function customWindow(
  start: string,
  end: string,
): { window_start: number; window_end: number } {
  if (!start || !end)
    throw new Error("Choose a start and end time for the custom report.");
  const window_start = new Date(start).getTime() / 1000;
  const window_end = new Date(end).getTime() / 1000;
  if (
    !Number.isFinite(window_start) ||
    !Number.isFinite(window_end) ||
    window_start < 0 ||
    window_end <= window_start
  )
    throw new Error("The end time must be after the start time.");
  if (window_end - window_start > 31 * 86400)
    throw new Error("The report window must cover no more than 31 days.");
  return { window_start, window_end };
}
export function preferenceLines(value: string): string[] {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}
export function validatePreferenceLines(value: string): string | undefined {
  const lines = preferenceLines(value);
  if (lines.length > 20) return "Use no more than 20 lines.";
  if (lines.some((line) => line.length > 300))
    return "Each line must be 300 characters or fewer.";
  return undefined;
}
