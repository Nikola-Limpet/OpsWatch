import { type ReactNode } from "react";
import {
  AlertCircle,
  Check,
  ChevronDown,
  Clock3,
  FileText,
  Info,
  LoaderCircle,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import { useSnapshot } from "../../api";
import {
  errorMessage,
  formatTime,
  humanize,
  scopeName,
  type FeedbackRating,
  type InputCoverage,
  type Scope,
  type Source,
  type StudioContext,
  type StudioOverview,
} from "./model";
import { useStudioAction } from "./queries";

export function PageHeading({
  title,
  children,
  action,
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <header className="studio-heading">
      <div>
        <h1>{title}</h1>
        <p>{children}</p>
      </div>
      {action}
    </header>
  );
}
export function Loading({
  children = "Loading saved work…",
}: {
  children?: ReactNode;
}) {
  return (
    <div className="studio-loading" role="status">
      <LoaderCircle size={18} className="studio-spin" />
      {children}
    </div>
  );
}
export function ErrorNotice({
  error,
  retry,
}: {
  error: unknown;
  retry?: () => void;
}) {
  return (
    <div className="studio-error" role="alert">
      <AlertCircle size={17} />
      <div>{errorMessage(error)}</div>
      {retry ? (
        <button className="studio-text-button" onClick={retry}>
          Try again
        </button>
      ) : null}
    </div>
  );
}
export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`badge studio-status studio-status-${status}`}>
      {status === "running" ? (
        <LoaderCircle size={12} className="studio-spin" />
      ) : null}
      {humanize(status)}
    </span>
  );
}
export function OlderRecords({
  label,
  more,
  loading,
  error,
  load,
}: {
  label: string;
  more: boolean;
  loading: boolean;
  error: unknown;
  load: () => void;
}) {
  if (!more && !error) return null;
  return (
    <div className="studio-older-records">
      {error ? <ErrorNotice error={error} /> : null}
      <button
        className="button"
        type="button"
        aria-label={`Load older ${label}`}
        disabled={loading}
        onClick={load}
      >
        {loading ? <LoaderCircle size={14} className="studio-spin" /> : null}
        {loading ? "Loading…" : `Load older ${label}`}
      </button>
    </div>
  );
}
export function ScopePicker({
  scope,
  onChange,
  id = "studio-scope",
  disabled = false,
}: {
  scope: Scope;
  onChange: (scope: Scope) => void;
  id?: string;
  disabled?: boolean;
}) {
  const snapshot = useSnapshot();
  const value =
    scope.kind === "workspace" ? "workspace" : `${scope.kind}:${scope.id}`;
  return (
    <label className="studio-select-label" htmlFor={id}>
      <span>Scope</span>
      <select
        id={id}
        value={value}
        disabled={disabled}
        onChange={(event) => {
          const [kind, ...identity] = event.target.value.split(":");
          onChange(
            kind === "workspace"
              ? { kind, id: null }
              : { kind: kind as "project" | "service", id: identity.join(":") },
          );
        }}
      >
        <option value="workspace">Whole workspace</option>
        {(snapshot.data?.projects?.length ?? 0) > 0 ? (
          <optgroup label="Projects">
            {snapshot.data?.projects.map((project) => (
              <option key={project.id} value={`project:${project.id}`}>
                {project.name}
              </option>
            ))}
          </optgroup>
        ) : null}
        {(snapshot.data?.services?.length ?? 0) > 0 ? (
          <optgroup label="Services">
            {snapshot.data?.services.map((service) => (
              <option key={service.id} value={`service:${service.id}`}>
                {service.name} · {service.environment}
              </option>
            ))}
          </optgroup>
        ) : null}
        {scope.kind !== "workspace" &&
        ![
          ...(snapshot.data?.projects ?? []),
          ...(snapshot.data?.services ?? []),
        ].some((item) => item.id === scope.id) ? (
          <option value={value}>{scopeName(scope, snapshot.data)}</option>
        ) : null}
      </select>
    </label>
  );
}
export function ScopeLabel({ scope }: { scope: Scope }) {
  const snapshot = useSnapshot();
  return <>{scopeName(scope, snapshot.data)}</>;
}
export function WorkerState({ worker }: { worker: StudioOverview["worker"] }) {
  const labels = {
    running: "Worker online",
    stale: "Worker heartbeat stale",
    not_started: "Worker not started",
  };
  return (
    <span
      className={`studio-worker studio-worker-${worker.status}`}
      title={`Last cycle: ${formatTime(worker.last_cycle)}`}
    >
      <span className="studio-state-dot" />
      {labels[worker.status]}
      {worker.queued > 0 ? (
        <span className="muted"> · {worker.queued} queued</span>
      ) : null}
    </span>
  );
}
export function Sources({
  sources,
  title = "Context sources",
  total,
}: {
  sources: (Source | string)[];
  title?: string;
  total?: number;
}) {
  if (!sources.length)
    return <p className="studio-small muted">No source references recorded.</p>;
  return (
    <details className="studio-sources">
      <summary>
        <FileText size={14} />
        <span>{title}</span>
        <span className="muted">
          {sources.length}
          {total && total > sources.length ? ` / ${total}` : ""}
        </span>
        <ChevronDown size={14} />
      </summary>
      <ul>
        {sources.map((source, index) => (
          <li
            key={
              typeof source === "string"
                ? source
                : `${source.kind}:${source.id}:${index}`
            }
          >
            {typeof source === "string" ? (
              <code>{source}</code>
            ) : (
              <>
                <strong>{source.label || source.id}</strong>
                <span className="studio-source-meta">
                  {source.kind} · <code>{source.id}</code>
                </span>
                <span className="studio-source-meta">
                  {formatTime(source.observed_at)}
                  {source.stale ? " · Stale observation" : ""}
                </span>
              </>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}
export function Feedback({
  type,
  id,
  rating,
}: {
  type: "message" | "report" | "problem";
  id: string;
  rating?: FeedbackRating;
}) {
  const mutation = useStudioAction();
  const selected = rating ?? null;
  const rate = (rating: "helpful" | "unhelpful") =>
    mutation.mutate({
      name: "feedback",
      payload: { subject_type: type, subject_id: id, rating },
    });
  return (
    <div className="studio-feedback">
      <span className="studio-small muted">
        {selected ? "Feedback saved" : "Was this useful?"}
      </span>
      <button
        type="button"
        className="studio-icon-button"
        aria-label="Helpful"
        aria-pressed={selected === "helpful"}
        disabled={mutation.isPending}
        onClick={() => rate("helpful")}
      >
        <ThumbsUp size={14} />
      </button>
      <button
        type="button"
        className="studio-icon-button"
        aria-label="Unhelpful"
        aria-pressed={selected === "unhelpful"}
        disabled={mutation.isPending}
        onClick={() => rate("unhelpful")}
      >
        <ThumbsDown size={14} />
      </button>
      {mutation.error ? (
        <span role="alert" className="studio-feedback-error">
          {errorMessage(mutation.error)}
        </span>
      ) : null}
    </div>
  );
}
const markdownComponents = {
  a: ({ href, children }: { href?: string; children?: ReactNode }) => (
    <a href={href} target="_blank" rel="noreferrer noopener">
      {children}
    </a>
  ),
  img: ({ alt }: { alt?: string }) => (
    <span className="muted">{alt ? `[Image: ${alt}]` : "[Image omitted]"}</span>
  ),
};
export function Markdown({ children }: { children: string }) {
  return (
    <div className="studio-markdown">
      <ReactMarkdown skipHtml components={markdownComponents}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
const inputLabels: Record<string, string> = {
  history: "Recent messages",
  feedback: "Saved feedback",
  existing_problems: "Open problems",
  preference_items: "Preference items",
  preference_notes_characters: "Preference notes · characters",
  report_content_bytes: "Saved report · bytes",
};
function omissionLabel(value: string) {
  const labels: Record<string, string> = {
    history: "Conversation history",
    feedback: "Feedback records",
    existing_problems: "Open problem proposals",
    "preference notes": "Preference notes",
    "preference principles": "Engineering principles",
    "preference stack": "Technology stack preferences",
    "preference review_focus": "Review focus preferences",
    "preference verification": "Verification expectations",
    "report content": "Saved report content",
    "report source summaries": "Report source summaries",
    "context services": "Recorded services",
    "context projects": "Recorded projects",
    "context checks": "Recorded checks",
    "context incidents": "Recorded incidents",
    "context queries": "Query observations",
    "context repairs": "Repair history",
  };
  return labels[value] ?? humanize(value);
}
function PromptCoverage({ coverage }: { coverage: InputCoverage }) {
  const metrics = Object.entries(inputLabels).filter(
    ([key]) =>
      ["history", "feedback", "existing_problems", "preference_items"].includes(
        key,
      ) ||
      coverage.available[key] > 0 ||
      coverage.included[key] > 0,
  );
  const count = (value: number | undefined) =>
    typeof value === "number" ? value.toLocaleString() : "?";
  return (
    <details className="studio-input-coverage" open={coverage.truncated}>
      <summary>
        <span>Model input coverage</span>
        <span className={coverage.truncated ? "studio-warning" : "muted"}>
          {coverage.truncated ? "Limited" : "Recorded"}
        </span>
        <ChevronDown size={14} />
      </summary>
      <div className="studio-input-coverage-body">
        <p className="studio-small muted">Included / available for this run</p>
        <dl className="studio-input-counts">
          {metrics.map(([key, label]) => (
            <div key={key}>
              <dt>{label}</dt>
              <dd
                title={
                  typeof coverage.selected[key] === "number"
                    ? `${count(coverage.selected[key])} selected before the input size limit`
                    : undefined
                }
              >
                {count(coverage.included[key])}
                {" / "}
                <span className="muted">{count(coverage.available[key])}</span>
              </dd>
            </div>
          ))}
        </dl>
        <p className="studio-input-size">
          {count(coverage.bytes)} / {count(coverage.limit_bytes)} bytes
        </p>
        {coverage.omissions.length ? (
          <div className="studio-input-omissions">
            <p>Omitted or shortened</p>
            <ul>
              {coverage.omissions.map((item) => (
                <li key={item}>{omissionLabel(item)}</li>
              ))}
            </ul>
          </div>
        ) : coverage.truncated ? (
          <p className="studio-small muted">
            Some available records were outside this run's selected input.
          </p>
        ) : null}
      </div>
    </details>
  );
}
export function ContextPanel({
  context,
  preferences,
}: {
  context: StudioContext;
  preferences: StudioOverview["preferences"];
}) {
  const counts = context.coverage?.counts ?? {};
  const included = context.coverage?.included ?? {};
  const labels: Record<string, string> = {
    services: "Services",
    projects: "Projects",
    checks: "Checks",
    incidents: "Incidents",
    queries: "Query observations",
    repairs: "Repair history",
  };
  return (
    <aside className="panel studio-context" aria-label="Application context">
      <div className="studio-section-heading">
        <h2>Application context</h2>
        <span className="studio-small muted">
          <ScopeLabel scope={context.scope} />
        </span>
      </div>
      <section className="studio-context-section">
        <div className="studio-context-time">
          <Clock3 size={14} />
          <span>Built {formatTime(context.generated_at)}</span>
        </div>
        <dl className="studio-count-list">
          {Object.entries(labels).map(([key, label]) => (
            <div key={key}>
              <dt>
                <Check size={13} />
                {label}
              </dt>
              <dd>
                {included[key] ?? 0}
                <span className="muted"> / {counts[key] ?? 0}</span>
              </dd>
            </div>
          ))}
        </dl>
        <p className="studio-small muted">
          Included / available in the recorded snapshot
        </p>
      </section>
      {context.pstack ? (
        <section className="studio-context-section">
          <div className="studio-section-title">
            <h3>Pstack playbook</h3>
            <a
              href={context.pstack.url}
              target="_blank"
              rel="noreferrer noopener"
            >
              Read source
            </a>
          </div>
          <p className="studio-pinned-playbook">
            {humanize(context.pstack.playbook)}
          </p>
          <p className="studio-small muted">
            Pinned revision <code>{context.pstack.revision.slice(0, 10)}</code>
          </p>
        </section>
      ) : null}
      <section className="studio-context-section">
        <Sources sources={context.sources ?? []} total={context.source_count} />
        <div className="studio-context-note">
          <Info size={15} />
          <p>
            {context.coverage?.historical_measurements === false
              ? "Historical coverage is limited. Reports use recorded events and observations."
              : "Context uses recorded application evidence."}
          </p>
        </div>
        {context.coverage?.truncated ? (
          <p className="studio-small studio-warning">
            {context.coverage.prompt?.truncated
              ? "The model input omits some available information. Inspect its coverage below."
              : "Context is bounded. Some recorded evidence is omitted."}
          </p>
        ) : null}
        {context.coverage?.prompt ? (
          <PromptCoverage coverage={context.coverage.prompt} />
        ) : null}
        {[...(context.notes ?? []), ...(context.assumptions ?? [])].map(
          (note, i) => (
            <p className="studio-small muted" key={i}>
              {note}
            </p>
          ),
        )}
      </section>
      <section className="studio-context-section">
        <div className="studio-section-title">
          <h3>Your workflow</h3>
          <a href="/workflow">Edit preferences</a>
        </div>
        <ol className="studio-workflow-steps">
          {preferences.workflow.map((step) => (
            <li key={step}>
              <span>{humanize(step)}</span>
            </li>
          ))}
        </ol>
        <p className="studio-small muted">
          Preference revision {preferences.revision}
        </p>
      </section>
    </aside>
  );
}
