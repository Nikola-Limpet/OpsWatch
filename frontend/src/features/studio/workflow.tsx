import { useState, type FormEvent } from "react";
import { useForm } from "@tanstack/react-form";
import {
  ArrowUpRight,
  Check,
  ClipboardList,
  LoaderCircle,
  Save,
  WandSparkles,
} from "lucide-react";
import {
  ErrorNotice,
  Feedback,
  ScopeLabel,
  Loading,
  PageHeading,
  OlderRecords,
  ScopePicker,
  Sources,
  StatusBadge,
  WorkerState,
} from "./components";
import {
  formatTime,
  humanize,
  preferenceLines,
  problemStates,
  validatePreferenceLines,
  WORKSPACE,
  type CreatedWork,
  type Preferences,
  type Problem,
  type ProblemStatus,
  type Scope,
} from "./model";
import {
  useReloadStudio,
  useStudio,
  useStudioAction,
  useStudioCollection,
} from "./queries";
import { PstackGuide } from "./pstack";

type PreferenceFields =
  | "principles"
  | "stack"
  | "review_focus"
  | "verification"
  | "notes";
const listFields: {
  name: Exclude<PreferenceFields, "notes">;
  label: string;
  hint: string;
}[] = [
  {
    name: "principles",
    label: "Engineering principles",
    hint: "One principle per line.",
  },
  {
    name: "stack",
    label: "Technology stack",
    hint: "Languages, frameworks, and infrastructure you want agents to consider.",
  },
  {
    name: "review_focus",
    label: "Review focus",
    hint: "What should receive extra scrutiny?",
  },
  {
    name: "verification",
    label: "Verification expectations",
    hint: "Checks you expect before work is reported complete.",
  },
];
function preferenceValues(preferences: Preferences) {
  return {
    principles: preferences.principles.join("\n"),
    stack: preferences.stack.join("\n"),
    review_focus: preferences.review_focus.join("\n"),
    verification: preferences.verification.join("\n"),
    notes: preferences.notes,
  };
}
function PreferenceEditor({ preferences }: { preferences: Preferences }) {
  const [base, setBase] = useState(preferences);
  const [saved, setSaved] = useState(false);
  const save = useStudioAction<{ preferences: Preferences }>();
  const refresh = useReloadStudio();
  const form = useForm({
    defaultValues: preferenceValues(base),
    onSubmit: async ({ value }) => {
      setSaved(false);
      try {
        const result = await save.mutateAsync({
          name: "preferences_update",
          payload: {
            revision: base.revision,
            principles: preferenceLines(value.principles),
            stack: preferenceLines(value.stack),
            review_focus: preferenceLines(value.review_focus),
            verification: preferenceLines(value.verification),
            notes: value.notes.trim(),
          },
        });
        if (result.preferences) {
          setBase(result.preferences);
          form.reset(preferenceValues(result.preferences));
        }
        setSaved(true);
      } catch {
        /* The form keeps this draft and the API error is shown below. */
      }
    },
  });
  async function reload() {
    try {
      const current = (await refresh.mutateAsync()).preferences;
      setBase(current);
      form.reset(preferenceValues(current));
      save.reset();
      setSaved(false);
    } catch {
      /* Keep the draft when the saved revision cannot be loaded. */
    }
  }
  return (
    <section className="panel studio-preferences">
      <div className="studio-section-heading">
        <div className="studio-section-title">
          <h2>Engineering preferences</h2>
          <span className="badge">Revision {base.revision}</span>
        </div>
        <p className="muted">
          Saved preferences guide investigations, reports, and improvement
          proposals.
        </p>
      </div>
      <form
        className="studio-form"
        onSubmit={(event) => {
          event.preventDefault();
          event.stopPropagation();
          void form.handleSubmit();
        }}
      >
        {preferences.revision !== base.revision ? (
          <div className="studio-revision-notice" role="status">
            <p>
              A newer preference revision is saved. Your draft still uses
              revision {base.revision}.
            </p>
            <button
              className="studio-text-button"
              type="button"
              disabled={refresh.isPending}
              onClick={() => void reload()}
            >
              {refresh.isPending ? "Loading…" : "Reload saved preferences"}
            </button>
          </div>
        ) : null}
        {listFields.map(({ name, label, hint }) => (
          <form.Field
            key={name}
            name={name}
            validators={{
              onChange: ({ value }) => validatePreferenceLines(value),
            }}
          >
            {(field) => (
              <label className="studio-field" htmlFor={`preference-${name}`}>
                <span>{label}</span>
                <textarea
                  id={`preference-${name}`}
                  aria-label={label}
                  value={field.state.value}
                  onChange={(event) => {
                    setSaved(false);
                    field.handleChange(event.target.value);
                  }}
                  onBlur={field.handleBlur}
                  rows={name === "stack" ? 3 : 4}
                  aria-invalid={field.state.meta.errors.length > 0}
                  aria-describedby={`preference-${name}-help`}
                />
                <small id={`preference-${name}-help`} className="muted">
                  {hint} Up to 20 lines, 300 characters each.
                </small>
                {field.state.meta.errors.map((error, i) => (
                  <span className="studio-field-error" role="alert" key={i}>
                    {String(error)}
                  </span>
                ))}
              </label>
            )}
          </form.Field>
        ))}
        <form.Field name="notes">
          {(field) => (
            <label className="studio-field" htmlFor="preference-notes">
              <span>Additional notes</span>
              <textarea
                id="preference-notes"
                rows={4}
                maxLength={4000}
                value={field.state.value}
                onChange={(event) => {
                  setSaved(false);
                  field.handleChange(event.target.value);
                }}
                onBlur={field.handleBlur}
              />
            </label>
          )}
        </form.Field>
        {refresh.error ? <ErrorNotice error={refresh.error} /> : null}
        {save.error ? (
          <>
            <ErrorNotice error={save.error} />
            <button
              className="studio-text-button"
              type="button"
              disabled={refresh.isPending}
              onClick={() => void reload()}
            >
              {refresh.isPending ? "Loading…" : "Reload saved preferences"}
            </button>
          </>
        ) : null}
        <div className="studio-form-footer">
          <form.Subscribe
            selector={(state) => [state.canSubmit, state.isSubmitting] as const}
          >
            {([canSubmit, isSubmitting]) => (
              <button
                type="submit"
                className="button button-primary"
                disabled={!canSubmit || isSubmitting}
              >
                {isSubmitting ? (
                  <LoaderCircle size={15} className="studio-spin" />
                ) : (
                  <Save size={15} />
                )}
                {isSubmitting ? "Saving…" : "Save preferences"}
              </button>
            )}
          </form.Subscribe>
          {saved ? (
            <span className="studio-save-confirmation" role="status">
              <Check size={14} />
              Preferences saved
            </span>
          ) : null}
        </div>
        <p className="studio-small muted">
          Feedback informs new proposals. It never changes these preferences
          automatically.
        </p>
      </form>
    </section>
  );
}
function ImprovementComposer() {
  const [scope, setScope] = useState<Scope>(WORKSPACE);
  const [focus, setFocus] = useState("");
  const [created, setCreated] = useState<CreatedWork | null>(null);
  const create = useStudioAction<CreatedWork>();
  return (
    <section className="panel studio-improvement-composer">
      <div className="studio-section-heading">
        <h2>Look for recurring problems</h2>
        <p className="muted">
          The improvement agent proposes work from recorded evidence and
          feedback.
        </p>
      </div>
      <form
        className="studio-form"
        onSubmit={(event) => {
          event.preventDefault();
          create.mutate(
            {
              name: "improvement_create",
              payload: { scope, focus: focus.trim() },
            },
            { onSuccess: setCreated },
          );
        }}
      >
        <ScopePicker scope={scope} onChange={setScope} id="improvement-scope" />
        <label className="studio-field" htmlFor="improvement-focus">
          <span>
            Focus <span className="muted">optional</span>
          </span>
          <textarea
            id="improvement-focus"
            rows={3}
            maxLength={4000}
            value={focus}
            onChange={(event) => {
              setFocus(event.target.value);
              setCreated(null);
            }}
            placeholder="What should the agent examine?"
          />
        </label>
        {create.error ? <ErrorNotice error={create.error} /> : null}
        <div className="studio-form-footer">
          <button className="button" type="submit" disabled={create.isPending}>
            {create.isPending ? (
              <LoaderCircle size={15} className="studio-spin" />
            ) : (
              <WandSparkles size={15} />
            )}
            {create.isPending ? "Saving job…" : "Propose improvements"}
          </button>
          {created ? (
            <a
              className="studio-inline-link"
              href={`/studio?session=${created.session.id}`}
            >
              Open saved session <ArrowUpRight size={14} />
            </a>
          ) : null}
        </div>
        {created ? (
          <p className="studio-small muted" role="status">
            Improvement job saved. Proposals appear in the problem stack when
            the local worker finishes.
          </p>
        ) : null}
      </form>
    </section>
  );
}
function ProblemReview({ problem }: { problem: Problem }) {
  const [base, setBase] = useState(problem);
  const [status, setStatus] = useState<ProblemStatus>(problem.status);
  const [notes, setNotes] = useState(problem.notes ?? "");
  const update = useStudioAction<{ problem: Problem }>();
  const refresh = useReloadStudio();
  const [saved, setSaved] = useState(false);
  async function reload() {
    try {
      const current = (await refresh.mutateAsync()).problems.find(
        (item) => item.id === problem.id,
      );
      if (!current)
        throw new Error("This problem is no longer in the current snapshot.");
      setBase(current);
      setStatus(current.status);
      setNotes(current.notes ?? "");
      setSaved(false);
      update.reset();
    } catch {
      /* Preserve the review draft if loading fails. */
    }
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    setSaved(false);
    update.mutate(
      {
        name: "problem_update",
        payload: {
          problem_id: problem.id,
          revision: base.revision,
          status,
          notes: notes.trim(),
        },
      },
      {
        onSuccess: (result) => {
          if (result.problem) setBase(result.problem);
          setSaved(true);
        },
      },
    );
  }
  return (
    <article className="studio-problem-review">
      <header className="studio-problem-review-header">
        <div className="studio-title-line">
          <h3>{problem.title}</h3>
          <span className={`badge studio-priority-${problem.priority}`}>
            {problem.priority} priority
          </span>
        </div>
        <p className="studio-small muted">
          <ScopeLabel scope={problem.scope} /> · Proposed{" "}
          {formatTime(problem.created)}
        </p>
      </header>
      <p className="studio-problem-summary">{problem.summary}</p>
      <div className="studio-validation">
        <h4>Validation</h4>
        <p>{problem.validation || "No validation steps were recorded."}</p>
      </div>
      <Sources sources={problem.evidence_refs ?? []} title="Problem evidence" />
      {problem.source_report_id ? (
        <a
          className="studio-inline-link"
          href={`/reports?report=${problem.source_report_id}`}
        >
          Read source report <ArrowUpRight size={14} />
        </a>
      ) : null}
      <form className="studio-problem-form" onSubmit={submit}>
        {problem.revision !== base.revision ? (
          <div className="studio-revision-notice">
            <p>A newer review is saved. Reload it before updating.</p>
            <button
              className="studio-text-button"
              type="button"
              disabled={refresh.isPending}
              onClick={() => void reload()}
            >
              Reload saved review
            </button>
          </div>
        ) : null}
        <label
          className="studio-field"
          htmlFor={`problem-status-${problem.id}`}
        >
          <span>Review status</span>
          <select
            id={`problem-status-${problem.id}`}
            value={status}
            onChange={(event) => {
              setSaved(false);
              setStatus(event.target.value as ProblemStatus);
            }}
          >
            {problemStates.map((state) => (
              <option key={state} value={state}>
                {humanize(state)}
              </option>
            ))}
          </select>
        </label>
        <label className="studio-field" htmlFor={`problem-notes-${problem.id}`}>
          <span>Your review notes</span>
          <textarea
            id={`problem-notes-${problem.id}`}
            rows={3}
            maxLength={4000}
            value={notes}
            onChange={(event) => {
              setSaved(false);
              setNotes(event.target.value);
            }}
          />
        </label>
        {refresh.error ? <ErrorNotice error={refresh.error} /> : null}
        {update.error ? (
          <>
            <ErrorNotice error={update.error} />
            <button
              className="studio-text-button"
              type="button"
              disabled={refresh.isPending}
              onClick={() => void reload()}
            >
              Reload saved review
            </button>
          </>
        ) : null}
        <div className="studio-form-footer">
          <button
            className="button"
            disabled={update.isPending || problem.revision !== base.revision}
          >
            {update.isPending ? "Saving…" : "Save review"}
          </button>
          {saved ? (
            <span className="studio-save-confirmation" role="status">
              <Check size={14} />
              Review saved
            </span>
          ) : null}
        </div>
      </form>
      <Feedback type="problem" id={problem.id} rating={problem.feedback} />
    </article>
  );
}
function ProblemStack({
  problems,
  hasMore,
}: {
  problems: Problem[];
  hasMore: boolean;
}) {
  const savedProblems = useStudioCollection("problems", problems, hasMore);
  const [filter, setFilter] = useState("open");
  const [selected, setSelected] = useState<string | null>(null);
  const weight = { high: 0, medium: 1, low: 2 };
  const visible = savedProblems.items
    .filter(
      (problem) =>
        filter === "all" ||
        (filter === "open" &&
          !["done", "dismissed"].includes(problem.status)) ||
        problem.status === filter,
    )
    .sort(
      (a, b) =>
        weight[a.priority] - weight[b.priority] || b.updated - a.updated,
    );
  const current =
    visible.find((problem) => problem.id === selected) ?? visible[0];
  return (
    <section className="panel studio-problem-stack">
      <div className="studio-section-heading">
        <div className="studio-section-title">
          <h2>Problem stack</h2>
          <span className="badge">{savedProblems.items.length} recorded</span>
        </div>
        <p className="muted">
          Review proposals before accepting work. Status changes record your
          decision.
        </p>
        <label className="studio-problem-filter" htmlFor="problem-filter">
          <span className="studio-small">Show</span>
          <select
            id="problem-filter"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          >
            <option value="open">Open problems</option>
            <option value="all">All problems</option>
            {problemStates.map((state) => (
              <option key={state} value={state}>
                {humanize(state)}
              </option>
            ))}
          </select>
        </label>
      </div>
      {visible.length ? (
        <div className="studio-problem-workspace">
          <div className="studio-problem-list" aria-label="Problems">
            {visible.map((problem) => (
              <button
                type="button"
                key={problem.id}
                className={`studio-problem-list-item ${current?.id === problem.id ? "is-selected" : ""}`}
                aria-pressed={current?.id === problem.id}
                onClick={() => setSelected(problem.id)}
              >
                <div>
                  <strong>{problem.title}</strong>
                  <span
                    className={`studio-small studio-priority-${problem.priority}`}
                  >
                    {problem.priority} priority
                  </span>
                </div>
                <StatusBadge status={problem.status} />
              </button>
            ))}
          </div>
          {current ? (
            <ProblemReview key={current.id} problem={current} />
          ) : null}
        </div>
      ) : (
        <div className="studio-empty-list">
          <ClipboardList size={25} />
          <h3>
            {problems.length
              ? "No problems in this view"
              : "No problems proposed yet"}
          </h3>
          <p>
            {problems.length
              ? "Choose another status to inspect saved problems."
              : "Run the improvement agent to turn recorded evidence into proposals you can review."}
          </p>
        </div>
      )}
      <OlderRecords
        label="problems"
        more={savedProblems.hasMore}
        loading={savedProblems.isLoading}
        error={savedProblems.error}
        load={savedProblems.loadOlder}
      />
    </section>
  );
}
export function WorkflowPage() {
  const query = useStudio();
  const data = query.data;
  return (
    <div className="studio-page">
      <PageHeading
        title="Workflow"
        action={data ? <WorkerState worker={data.worker} /> : null}
      >
        Your engineering preferences and reviewable improvement work.
      </PageHeading>
      {query.isPending ? (
        <Loading />
      ) : query.isError && !data ? (
        <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      ) : data ? (
        <>
          <div className="studio-workflow-intro">
            <ol className="studio-workflow-ribbon">
              {data.preferences.workflow.map((step) => (
                <li key={step}>{humanize(step)}</li>
              ))}
            </ol>
            <p className="studio-small muted">
              Saved preferences guide agent behavior. Review recurring problems
              before accepting work.
            </p>
          </div>
          {data.pstack ? <PstackGuide catalog={data.pstack} /> : null}
          <div className="studio-workflow-grid">
            <PreferenceEditor preferences={data.preferences} />
            <div className="studio-workflow-work">
              <ImprovementComposer />
              <ProblemStack
                problems={data.problems}
                hasMore={data.has_more.problems ?? false}
              />
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}
