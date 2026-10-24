import { useState, type FormEvent } from "react";
import {
  ArrowLeft,
  ArrowUpRight,
  FileText,
  LoaderCircle,
  Plus,
  Square,
  WandSparkles,
} from "lucide-react";
import {
  ErrorNotice,
  Feedback,
  ScopeLabel,
  Loading,
  Markdown,
  PageHeading,
  OlderRecords,
  ScopePicker,
  Sources,
  StatusBadge,
  WorkerState,
} from "./components";
import {
  customWindow,
  errorMessage,
  formatTime,
  isActive,
  WORKSPACE,
  type CreatedWork,
  type Report,
  type Scope,
  type StudioOverview,
} from "./model";
import {
  useReport,
  useStudio,
  useStudioAction,
  useStudioCollection,
} from "./queries";

function ReportComposer({
  onCreated,
}: {
  onCreated: (report: Report) => void;
}) {
  const [scope, setScope] = useState<Scope>(WORKSPACE);
  const [period, setPeriod] = useState<"day" | "week" | "custom">("day");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [validation, setValidation] = useState<string | null>(null);
  const create = useStudioAction<CreatedWork>();
  function submit(event: FormEvent) {
    event.preventDefault();
    setValidation(null);
    try {
      const window = period === "custom" ? customWindow(start, end) : {};
      create.mutate(
        {
          name: "report_create",
          payload: {
            scope,
            period,
            ...window,
            ...(title.trim() ? { title: title.trim() } : {}),
            prompt: prompt.trim(),
          },
        },
        {
          onSuccess: (result) => {
            if (result.report) onCreated(result.report);
          },
        },
      );
    } catch (error) {
      setValidation(errorMessage(error));
    }
  }
  return (
    <section className="panel studio-report-composer">
      <div className="studio-section-heading">
        <h2>Generate a report</h2>
        <p className="muted">
          Summarize recorded evidence for a scope and period.
        </p>
      </div>
      <form className="studio-form" onSubmit={submit} noValidate>
        <div className="studio-form-row">
          <ScopePicker scope={scope} onChange={setScope} id="report-scope" />
          <label className="studio-field" htmlFor="report-period">
            <span>Reporting period</span>
            <select
              id="report-period"
              value={period}
              onChange={(event) =>
                setPeriod(event.target.value as typeof period)
              }
            >
              <option value="day">Past day</option>
              <option value="week">Past week</option>
              <option value="custom">Custom window</option>
            </select>
          </label>
        </div>
        {period === "custom" ? (
          <div className="studio-form-row">
            <label className="studio-field" htmlFor="report-start">
              <span>Start time</span>
              <input
                id="report-start"
                type="datetime-local"
                value={start}
                onChange={(event) => setStart(event.target.value)}
              />
            </label>
            <label className="studio-field" htmlFor="report-end">
              <span>End time</span>
              <input
                id="report-end"
                type="datetime-local"
                value={end}
                onChange={(event) => setEnd(event.target.value)}
              />
            </label>
          </div>
        ) : null}
        <label className="studio-field" htmlFor="report-title">
          <span>
            Title <span className="muted">optional</span>
          </span>
          <input
            id="report-title"
            maxLength={120}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="Operations report"
          />
        </label>
        <label className="studio-field" htmlFor="report-prompt">
          <span>
            Focus <span className="muted">optional</span>
          </span>
          <textarea
            id="report-prompt"
            rows={3}
            maxLength={4000}
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            placeholder="What should this report examine?"
          />
        </label>
        <p className="studio-small muted">
          Reports describe recorded coverage and gaps. A report does not
          establish historical uptime.
        </p>
        {validation ? (
          <div className="studio-field-error" role="alert">
            {validation}
          </div>
        ) : null}
        {create.error ? <ErrorNotice error={create.error} /> : null}
        <button
          type="submit"
          className="button button-primary"
          disabled={create.isPending}
        >
          {create.isPending ? (
            <LoaderCircle size={15} className="studio-spin" />
          ) : (
            <Plus size={15} />
          )}
          {create.isPending ? "Saving report job…" : "Generate report"}
        </button>
      </form>
    </section>
  );
}
function ReportDetail({
  id,
  summary,
  onBack,
}: {
  id: string;
  summary?: Report;
  onBack: () => void;
}) {
  const query = useReport(id);
  const cancel = useStudioAction();
  const improve = useStudioAction<CreatedWork>();
  const [improvement, setImprovement] = useState<CreatedWork | null>(null);
  const detail = query.data?.report;
  const report = detail ?? summary;
  if (query.isPending && !report)
    return (
      <section className="panel studio-report-detail">
        <Loading>Loading report…</Loading>
      </section>
    );
  if (!report)
    return (
      <section className="panel studio-report-detail">
        <ErrorNotice
          error={query.error ?? new Error("Report unavailable.")}
          retry={() => void query.refetch()}
        />
      </section>
    );
  return (
    <article className="panel studio-report-detail">
      <header className="studio-report-header">
        <button
          className="studio-back studio-icon-button"
          aria-label="Back to reports"
          onClick={onBack}
        >
          <ArrowLeft size={17} />
        </button>
        <div>
          <div className="studio-title-line">
            <h2>{report.title}</h2>
            <StatusBadge status={report.status} />
          </div>
          <p className="studio-small muted">
            <ScopeLabel scope={report.scope} /> ·{" "}
            {formatTime(report.window_start)} to {formatTime(report.window_end)}
          </p>
        </div>
        <a
          className="studio-icon-button"
          href={`/studio?session=${report.session_id}`}
          aria-label="Open report conversation"
        >
          <ArrowUpRight size={17} />
        </a>
      </header>
      <div className="studio-report-body">
        {query.isPending ? <Loading>Loading report…</Loading> : null}
        {query.error ? (
          <ErrorNotice error={query.error} retry={() => void query.refetch()} />
        ) : null}
        {query.isError && !detail ? (
          <p className="muted">
            Saved report details are unavailable. Try the read again.
          </p>
        ) : null}
        {isActive(report.status) ? (
          <div className="studio-report-pending">
            <LoaderCircle size={22} className="studio-spin" />
            <h3>
              {report.status === "queued"
                ? "Report is queued"
                : "Report is being written"}
            </h3>
            <p>
              The saved job will run on the local worker. You can leave this
              page and return to it.
            </p>
            <button
              className="button"
              disabled={cancel.isPending}
              onClick={() =>
                cancel.mutate({
                  name: "job_cancel",
                  payload: { job_id: report.job_id },
                })
              }
            >
              <Square size={13} />
              {cancel.isPending ? "Cancelling…" : "Cancel job"}
            </button>
          </div>
        ) : null}
        {report.error ? <ErrorNotice error={new Error(report.error)} /> : null}
        {report.content ? (
          <Markdown>{report.content}</Markdown>
        ) : query.isSuccess &&
          detail &&
          (detail.content === null || detail.content === "") &&
          !isActive(report.status) &&
          !report.error ? (
          <p className="muted">No report content was recorded for this job.</p>
        ) : null}
        {cancel.error ? <ErrorNotice error={cancel.error} /> : null}
        {report.sources?.length ? (
          <Sources sources={report.sources} title="Report evidence" />
        ) : null}
      </div>
      <footer className="studio-report-footer">
        <div className="studio-small muted">
          Saved {formatTime(report.updated)} · Preference revision{" "}
          {report.preference_revision}
        </div>
        {report.status === "succeeded" && report.content ? (
          <>
            <Feedback type="report" id={report.id} rating={report.feedback} />
            <div className="studio-report-improve">
              <button
                className="button"
                disabled={improve.isPending || Boolean(improvement)}
                onClick={() =>
                  improve.mutate(
                    {
                      name: "improvement_create",
                      payload: { scope: report.scope, report_id: report.id },
                    },
                    { onSuccess: setImprovement },
                  )
                }
              >
                {improve.isPending ? (
                  <LoaderCircle size={15} className="studio-spin" />
                ) : (
                  <WandSparkles size={15} />
                )}
                {improvement ? "Improvement job saved" : "Propose improvements"}
              </button>
              <span className="studio-small muted">
                Proposals need your review in Workflow.
              </span>
            </div>
            {improvement ? (
              <a
                className="studio-inline-link"
                href={`/studio?session=${improvement.session.id}`}
              >
                Open improvement session <ArrowUpRight size={14} />
              </a>
            ) : null}
            {improve.error ? <ErrorNotice error={improve.error} /> : null}
          </>
        ) : null}
      </footer>
    </article>
  );
}
function SavedReports({
  data,
  selectedId,
  onSelect,
}: {
  data: StudioOverview;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const reports = useStudioCollection(
    "reports",
    data.reports,
    data.has_more.reports,
  );
  return (
    <section className="panel studio-saved-reports">
      <div className="studio-section-heading">
        <h2>
          Saved reports <span className="muted">{reports.items.length}</span>
        </h2>
      </div>
      {reports.items.length ? (
        <div className="studio-report-list">
          {reports.items.map((report) => (
            <button
              type="button"
              key={report.id}
              onClick={() => onSelect(report.id)}
              className={`studio-report-list-item ${selectedId === report.id ? "is-selected" : ""}`}
              aria-pressed={selectedId === report.id}
            >
              <div className="studio-report-list-icon">
                <FileText size={17} />
              </div>
              <div className="studio-report-list-text">
                <strong>{report.title}</strong>
                <span>
                  <ScopeLabel scope={report.scope} /> ·{" "}
                  {formatTime(report.created)}
                </span>
              </div>
              <StatusBadge status={report.status} />
            </button>
          ))}
        </div>
      ) : (
        <div className="studio-empty-list">
          <FileText size={24} />
          <h3>No saved reports</h3>
          <p>
            Generate a report from the application evidence OpsWatch has
            recorded.
          </p>
        </div>
      )}
      <OlderRecords
        label="reports"
        more={reports.hasMore}
        loading={reports.isLoading}
        error={reports.error}
        load={reports.loadOlder}
      />
    </section>
  );
}
export function ReportsPage() {
  const query = useStudio();
  const [selected, setSelected] = useState<string | null>(() =>
    new URLSearchParams(window.location.search).get("report"),
  );
  const [mobileDetail, setMobileDetail] = useState(Boolean(selected));
  function select(id: string) {
    setSelected(id);
    setMobileDetail(true);
    const url = new URL(window.location.href);
    url.searchParams.set("report", id);
    window.history.replaceState({}, "", url);
  }
  const data = query.data;
  return (
    <div className="studio-page">
      <PageHeading
        title="Reports"
        action={data ? <WorkerState worker={data.worker} /> : null}
      >
        Saved operational reports, grounded in recorded evidence.
      </PageHeading>
      {query.isPending ? (
        <Loading />
      ) : query.isError && !data ? (
        <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      ) : data ? (
        <>
          <div
            className={`studio-reports-grid ${mobileDetail ? "show-detail" : ""}`}
          >
            <div className="studio-reports-sidebar">
              <ReportComposer onCreated={(report) => select(report.id)} />
              <SavedReports
                data={data}
                selectedId={selected}
                onSelect={select}
              />
            </div>
            <div className="studio-report-detail-wrap">
              {selected ? (
                <ReportDetail
                  key={selected}
                  id={selected}
                  summary={data.reports.find(
                    (report) => report.id === selected,
                  )}
                  onBack={() => setMobileDetail(false)}
                />
              ) : (
                <section className="panel studio-report-placeholder">
                  <FileText size={28} />
                  <h2>Read a saved report</h2>
                  <p>
                    Select a report to inspect its findings, coverage, and
                    source references.
                  </p>
                </section>
              )}
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}
