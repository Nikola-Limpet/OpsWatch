/* Agent Studio extends OpsWatch's established graphite workspace.
   THESIS: saved conversations and their evidence remain visible together.
   OWN-WORLD: Geist, neutral panels, thin separators, semantic state labels.
   STORY: select scope, ask a question, inspect sources, review proposed work.
   FIRST VIEWPORT: session list, readable conversation, application context.
   FORM: responsive three-column workspace from the approved generated reference. */
import { useState } from "react";
import {
  FileText,
  LoaderCircle,
  MessageSquare,
  Plus,
  Search,
  WandSparkles,
} from "lucide-react";
import { Conversation } from "./chat";
import {
  ContextPanel,
  ErrorNotice,
  Loading,
  PageHeading,
  OlderRecords,
  ScopePicker,
  WorkerState,
} from "./components";
import {
  formatTime,
  sessionPreview,
  ROLE_LABELS,
  WORKSPACE,
  type PlaybookId,
  type Scope,
  type StudioRole,
  type StudioSession,
} from "./model";
import { useStudio, useStudioAction, useStudioCollection } from "./queries";
import "../../studio.css";
export { ReportsPage } from "./reports";
export { WorkflowPage } from "./workflow";

const roleIcons = { chat: Search, report: FileText, improvement: WandSparkles };
export function StudioPage() {
  const query = useStudio();
  const create = useStudioAction<{ session: StudioSession }>();
  const [selected, setSelected] = useState<string | null>(() =>
    new URLSearchParams(window.location.search).get("session"),
  );
  const [mobileDetail, setMobileDetail] = useState(Boolean(selected));
  const [role, setRole] = useState<StudioRole>("chat");
  const [playbook, setPlaybook] = useState<PlaybookId>("investigation");
  const [scope, setScope] = useState<Scope>(WORKSPACE);
  const [search, setSearch] = useState("");
  const [archived, setArchived] = useState(false);
  const data = query.data;
  const savedSessions = useStudioCollection(
    "sessions",
    data?.sessions,
    data?.has_more.sessions,
  );
  const currentId =
    selected ??
    savedSessions.items.find((session) => !session.archived)?.id ??
    null;
  const sessions =
    savedSessions.items.filter(
      (session) =>
        Boolean(session.archived) === archived &&
        `${session.title} ${session.last_message ?? ""}`
          .toLowerCase()
          .includes(search.toLowerCase()),
    ) ?? [];
  function select(id: string) {
    setSelected(id);
    setMobileDetail(true);
    const url = new URL(window.location.href);
    url.searchParams.set("session", id);
    window.history.replaceState({}, "", url);
  }
  function newSession() {
    create.mutate(
      { name: "session_create", payload: { role, scope, playbook } },
      { onSuccess: (result) => select(result.session.id) },
    );
  }
  return (
    <div className="studio-page">
      <PageHeading
        title="Agent Studio"
        action={
          <button
            className="button button-primary"
            onClick={newSession}
            disabled={create.isPending || !data}
          >
            {create.isPending ? (
              <LoaderCircle size={15} className="studio-spin" />
            ) : (
              <Plus size={16} />
            )}
            {create.isPending ? "Saving session…" : "New session"}
          </button>
        }
      >
        Investigate with your application context.
      </PageHeading>
      {query.isPending ? (
        <Loading />
      ) : query.isError && !data ? (
        <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      ) : data ? (
        <>
          <div className="studio-toolbar panel">
            <div
              className="studio-role-picker"
              role="radiogroup"
              aria-label="New session role"
            >
              <span className="studio-toolbar-label">New session role</span>
              {(Object.keys(ROLE_LABELS) as StudioRole[]).map((value) => {
                const Icon = roleIcons[value];
                return (
                  <button
                    type="button"
                    key={value}
                    role="radio"
                    aria-checked={role === value}
                    className={role === value ? "is-selected" : ""}
                    onClick={() => {
                      setRole(value);
                      setPlaybook(
                        value === "improvement"
                          ? "reflection"
                          : "investigation",
                      );
                    }}
                  >
                    <Icon size={15} />
                    <span>{ROLE_LABELS[value]}</span>
                  </button>
                );
              })}
            </div>
            <ScopePicker scope={scope} onChange={setScope} />
            {data.pstack ? (
              <label
                className="studio-select-label studio-playbook-select"
                htmlFor="studio-playbook"
              >
                <span>Playbook</span>
                <select
                  id="studio-playbook"
                  aria-label="Pstack playbook"
                  value={playbook}
                  onChange={(event) =>
                    setPlaybook(event.target.value as PlaybookId)
                  }
                >
                  {data.pstack.playbooks.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.title}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            <WorkerState worker={data.worker} />
          </div>
          {create.error ? <ErrorNotice error={create.error} /> : null}
          {query.error ? (
            <ErrorNotice
              error={query.error}
              retry={() => void query.refetch()}
            />
          ) : null}
          <div className={`studio-stage ${mobileDetail ? "show-detail" : ""}`}>
            <aside className="panel studio-sessions" aria-label="Sessions">
              <div className="studio-session-heading">
                <h2>Sessions</h2>
                <span className="studio-small muted">{sessions.length}</span>
              </div>
              <label className="studio-search">
                <Search size={15} />
                <input
                  aria-label="Search sessions"
                  placeholder="Search sessions…"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
              </label>
              <label className="studio-archive-toggle">
                <input
                  type="checkbox"
                  checked={archived}
                  onChange={(event) => setArchived(event.target.checked)}
                />
                Show archived
              </label>
              <div className="studio-session-list">
                {sessions.length ? (
                  sessions.map((session) => (
                    <button
                      key={session.id}
                      className={`studio-session-item ${currentId === session.id ? "is-selected" : ""}`}
                      aria-pressed={currentId === session.id}
                      onClick={() => select(session.id)}
                    >
                      <div className="studio-session-title">
                        <strong>{session.title}</strong>
                        {session.active_job_id ? (
                          <LoaderCircle
                            className="studio-spin"
                            size={13}
                            aria-label="Active job"
                          />
                        ) : null}
                      </div>
                      <p>
                        {session.last_message
                          ? sessionPreview(session.last_message)
                          : "No messages yet"}
                      </p>
                      <div className="studio-session-meta">
                        <span>{ROLE_LABELS[session.role]}</span>
                        <time>{formatTime(session.updated)}</time>
                      </div>
                    </button>
                  ))
                ) : (
                  <div className="studio-empty-list">
                    <MessageSquare size={23} />
                    <h3>
                      {search
                        ? "No matching sessions"
                        : archived
                          ? "No archived sessions"
                          : "No saved sessions"}
                    </h3>
                    <p>
                      {search
                        ? "Try a different search."
                        : "Choose an agent role and scope, then create a session."}
                    </p>
                  </div>
                )}
              </div>
              <OlderRecords
                label="sessions"
                more={savedSessions.hasMore}
                loading={savedSessions.isLoading}
                error={savedSessions.error}
                load={savedSessions.loadOlder}
              />
              <div className="studio-session-footer">
                Sessions and messages are saved by OpsWatch.
              </div>
            </aside>
            {currentId ? (
              <Conversation
                key={currentId}
                sessionId={currentId}
                overview={data}
                onBack={() => setMobileDetail(false)}
              />
            ) : (
              <>
                <section className="panel studio-start">
                  <MessageSquare size={30} />
                  <h2>Start with your application context</h2>
                  <p>
                    Create a scoped conversation to investigate recorded
                    evidence, write a report, or look for recurring problems.
                  </p>
                  <button
                    className="button button-primary"
                    disabled={create.isPending}
                    onClick={newSession}
                  >
                    <Plus size={15} />
                    New session
                  </button>
                </section>
                <ContextPanel
                  context={data.context}
                  preferences={data.preferences}
                />
              </>
            )}
          </div>
        </>
      ) : null}
    </div>
  );
}
