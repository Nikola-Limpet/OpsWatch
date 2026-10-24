export type Health =
  | 'healthy'
  | 'warning'
  | 'critical'
  | 'unknown'
  | 'stale'
  | 'resolved'
  | 'quiet'
  | 'disabled';
export interface Check {
  kind: string;
  key?: string;
  title: string;
  checked: number | null;
  stale?: boolean;
  healthy: boolean | number | null;
  evidence: Record<string, unknown>;
}
export interface Service {
  id: string;
  name: string;
  kind?: string;
  provider: string;
  account?: string;
  environment: string;
  status: Health;
  checked: number | null;
  latency_ms: number | null;
  threshold_ms: number;
  url?: string | null;
  favicon?: string | null;
  enabled?: boolean;
  open_incidents?: number;
  checks: Check[];
}
export interface Project {
  id: string;
  name: string;
  enabled_count: number;
  services: Service[];
}
export interface Incident {
  id?: number;
  ref: string;
  target: string;
  title: string;
  kind: string;
  status: string;
  severity: Health;
  opened: number;
  updated: number;
  occurrences?: number;
  evidence: Record<string, unknown>;
  next_step?: string;
  action?: string;
  repair?: { available: boolean; reason?: string };
  links?: { text: string; url: string }[];
}
export interface Investigation {
  id: string;
  incident: string;
  session_id: string;
  status: string;
  summary: string;
  updated: number;
  title?: string;
  service?: string;
}
export interface Repair {
  id: string;
  incident: string;
  status: string;
  summary?: string;
  pr_url?: string | null;
  updated: number;
  target?: string;
  service?: string;
  title?: string;
}
export interface QuerySample extends Check {
  name: string;
  target?: string;
}
export interface Snapshot {
  generated_at: number;
  timezone: string;
  monitor: { status: string; last_cycle: number | null };
  repair_worker?: { status: string; last_cycle?: number | null };
  summary: {
    total: number;
    healthy: number;
    attention: number;
    unknown: number;
    open_incidents: number;
  };
  services: Service[];
  projects: Project[];
  selection_editable: boolean;
  incidents: Incident[];
  incidents_limit?: number;
  queries: QuerySample[];
  repairs: Repair[];
  investigations?: Investigation[];
  repair_incidents?: Incident[];
  automation?: {
    auto_fix?: boolean;
    daily_limit?: number;
    notifications?: boolean;
  };
}
