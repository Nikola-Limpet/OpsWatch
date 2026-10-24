import { useCallback, useMemo, useState } from "react";
import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  MessageNotSentError,
  MessagePrimitive,
  ThreadPrimitive,
  useExternalStoreRuntime,
  type AppendMessage,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import {
  Archive,
  ArrowLeft,
  ArrowUp,
  Check,
  LoaderCircle,
  Pencil,
  Square,
  X,
} from "lucide-react";
import {
  ContextPanel,
  ErrorNotice,
  Feedback,
  ScopeLabel,
  Loading,
  Sources,
  StatusBadge,
} from "./components";
import {
  errorMessage,
  formatTime,
  isActive,
  mergeMessages,
  ROLE_LABELS,
  type StudioMessage,
  type StudioOverview,
  type StudioSession,
} from "./model";
import { useSession, useStudioAction } from "./queries";

const convertMessage = (message: StudioMessage): ThreadMessageLike => ({
  id: message.id,
  role: message.role,
  createdAt: new Date(message.created * 1000),
  content: message.content ? [{ type: "text", text: message.content }] : [],
  ...(message.role === "assistant"
    ? {
        status: isActive(message.status)
          ? { type: "running" as const }
          : message.status === "complete"
            ? { type: "complete" as const, reason: "stop" as const }
            : {
                type: "incomplete" as const,
                reason: ["interrupted", "cancelled"].includes(message.status)
                  ? ("cancelled" as const)
                  : ("error" as const),
              },
      }
    : {}),
});
function ChatMarkdown() {
  return (
    <div className="studio-markdown">
      <MarkdownTextPrimitive
        skipHtml
        components={{
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer noopener">
              {children}
            </a>
          ),
          img: ({ alt }) => (
            <span className="muted">
              {alt ? `[Image: ${alt}]` : "[Image omitted]"}
            </span>
          ),
        }}
      />
    </div>
  );
}
function ConversationMessage({
  record,
  role,
}: {
  record?: StudioMessage;
  role: string;
}) {
  return (
    <MessagePrimitive.Root className={`studio-message studio-message-${role}`}>
      <div className="studio-message-label">
        <span>
          {role === "user" ? "You" : role === "system" ? "System" : "OpsWatch"}
        </span>
        {record ? (
          <time dateTime={new Date(record.created * 1000).toISOString()}>
            {formatTime(record.created)}
          </time>
        ) : null}
        {record && record.status !== "complete" ? (
          <StatusBadge status={record.status} />
        ) : null}
      </div>
      <div className="studio-message-content">
        <MessagePrimitive.Parts components={{ Text: ChatMarkdown }} />
        {(!record || !record.content) &&
        (record?.status === "queued" ||
          record?.status === "running" ||
          !record) ? (
          <div className="studio-pending-message" role="status">
            <LoaderCircle size={15} className="studio-spin" />
            {record?.status === "queued"
              ? "Waiting for the local worker"
              : "Working with recorded context"}
          </div>
        ) : null}
        {record &&
        (record.status === "failed" ||
          record.status === "interrupted" ||
          record.status === "cancelled") &&
        !record.content ? (
          <p className="muted">
            {record.status === "failed"
              ? "The agent did not return a response. Review the job error below and try again."
              : record.status === "cancelled"
                ? "You cancelled this job. Send another message to continue."
                : "This response was interrupted. Send another message to continue."}
          </p>
        ) : null}
        {record?.role === "assistant" && record.sources?.length ? (
          <Sources sources={record.sources} title="Evidence references" />
        ) : null}
      </div>
      {record?.role === "assistant" && record.status === "complete" ? (
        <Feedback type="message" id={record.id} rating={record.feedback} />
      ) : null}
    </MessagePrimitive.Root>
  );
}
function SessionTitle({ session }: { session: StudioSession }) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(session.title);
  const update = useStudioAction();
  return (
    <div className="studio-conversation-title">
      {editing ? (
        <form
          className="studio-title-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (title.trim())
              update.mutate(
                {
                  name: "session_update",
                  payload: { session_id: session.id, title: title.trim() },
                },
                { onSuccess: () => setEditing(false) },
              );
          }}
        >
          <input
            aria-label="Session title"
            value={title}
            maxLength={120}
            onChange={(event) => setTitle(event.target.value)}
            autoFocus
          />
          <button
            className="studio-icon-button"
            aria-label="Save session title"
            disabled={!title.trim() || update.isPending}
          >
            <Check size={15} />
          </button>
          <button
            className="studio-icon-button"
            type="button"
            aria-label="Cancel rename"
            onClick={() => setEditing(false)}
          >
            <X size={15} />
          </button>
        </form>
      ) : (
        <div className="studio-title-line">
          <h2>{session.title}</h2>
          <button
            type="button"
            className="studio-icon-button"
            aria-label="Rename session"
            onClick={() => {
              setTitle(session.title);
              setEditing(true);
            }}
          >
            <Pencil size={14} />
          </button>
        </div>
      )}
      <div className="studio-conversation-meta">
        <span>{ROLE_LABELS[session.role]}</span>
        <span>·</span>
        <span>
          <ScopeLabel scope={session.scope} />
        </span>
        {session.playbook ? (
          <>
            <span>·</span>
            <span>{session.playbook.replaceAll("-", " ")}</span>
          </>
        ) : null}
        {session.archived ? <span>· Archived</span> : null}
      </div>
      {update.error ? <ErrorNotice error={update.error} /> : null}
    </div>
  );
}
export function Conversation({
  sessionId,
  overview,
  onBack,
}: {
  sessionId: string;
  overview: StudioOverview;
  onBack: () => void;
}) {
  const query = useSession(sessionId);
  const send = useStudioAction();
  const cancel = useStudioAction();
  const update = useStudioAction();
  const latest = query.data?.pages[0];
  const messages = useMemo(
    () => mergeMessages(query.data?.pages ?? []),
    [query.data],
  );
  const byId = useMemo(
    () => new Map(messages.map((message) => [message.id, message])),
    [messages],
  );
  const activeJob = latest?.jobs.find((job) => isActive(job.status));
  const activeId = activeJob?.id ?? latest?.session.active_job_id;
  const isRunning = Boolean(activeId) || send.isPending;
  const onNew = useCallback(
    async (message: AppendMessage) => {
      const content = message.content
        .filter((part) => part.type === "text")
        .map((part) => part.text)
        .join("\n")
        .trim();
      if (!content || content.length > 8000)
        throw new MessageNotSentError(
          "Messages must contain 1 to 8,000 characters.",
        );
      try {
        await send.mutateAsync({
          name: "message_send",
          payload: { session_id: sessionId, content },
        });
      } catch (error) {
        throw new MessageNotSentError(errorMessage(error));
      }
    },
    [send.mutateAsync, sessionId],
  );
  const onCancel = useCallback(async () => {
    if (activeId)
      await cancel.mutateAsync({
        name: "job_cancel",
        payload: { job_id: activeId },
      });
  }, [activeId, cancel.mutateAsync]);
  const loadEarlier = useCallback(async () => {
    await query.fetchNextPage();
  }, [query.fetchNextPage]);
  const runtime = useExternalStoreRuntime({
    messages,
    convertMessage,
    isRunning,
    isLoading: query.isPending,
    isDisabled: Boolean(latest?.session.archived) || query.isError,
    isSendDisabled: send.isPending,
    onNew,
    ...(activeId ? { onCancel } : {}),
    hasEarlier: query.hasNextPage,
    onLoadEarlier: loadEarlier,
  });
  if (query.isPending)
    return (
      <div className="panel studio-conversation">
        <Loading>Loading conversation…</Loading>
      </div>
    );
  if (!latest)
    return (
      <div className="panel studio-conversation">
        <button className="studio-back button" onClick={onBack}>
          <ArrowLeft size={15} />
          Sessions
        </button>
        <ErrorNotice
          error={query.error ?? new Error("Conversation unavailable.")}
          retry={() => void query.refetch()}
        />
      </div>
    );
  const failedJobs = latest.jobs.filter(
    (job) =>
      job.status === "failed" ||
      job.status === "interrupted" ||
      job.status === "cancelled",
  );
  return (
    <>
      <section className="panel studio-conversation" aria-label="Conversation">
        <header className="studio-conversation-header">
          <button
            className="studio-back studio-icon-button"
            aria-label="Back to sessions"
            onClick={onBack}
          >
            <ArrowLeft size={17} />
          </button>
          <SessionTitle session={latest.session} />
          <button
            className="studio-icon-button"
            aria-label={
              latest.session.archived ? "Restore session" : "Archive session"
            }
            title={
              latest.session.archived ? "Restore session" : "Archive session"
            }
            disabled={update.isPending || isRunning}
            onClick={() =>
              update.mutate({
                name: "session_update",
                payload: {
                  session_id: sessionId,
                  archived: !latest.session.archived,
                },
              })
            }
          >
            <Archive size={16} />
          </button>
        </header>
        <AssistantRuntimeProvider runtime={runtime}>
          <ThreadPrimitive.Root className="studio-thread">
            <ThreadPrimitive.Viewport
              className="studio-thread-viewport"
              autoScroll
            >
              <div className="studio-thread-content">
                {query.hasNextPage ? (
                  <ThreadPrimitive.LoadEarlier
                    className="button studio-load-earlier"
                    disabled={query.isFetchingNextPage}
                  >
                    {query.isFetchingNextPage
                      ? "Loading…"
                      : "Load older messages"}
                  </ThreadPrimitive.LoadEarlier>
                ) : null}
                <ThreadPrimitive.Empty>
                  <div className="studio-conversation-empty">
                    <h3>
                      {ROLE_LABELS[latest.session.role]} is ready for a question
                    </h3>
                    <p>
                      Ask about recorded services, incidents, query
                      observations, or repair history. The agent uses the saved
                      scope and your workflow preferences.
                    </p>
                  </div>
                </ThreadPrimitive.Empty>
                <ThreadPrimitive.Messages>
                  {({ message }) => (
                    <ConversationMessage
                      record={byId.get(message.id)}
                      role={message.role}
                    />
                  )}
                </ThreadPrimitive.Messages>
                {failedJobs.map((job) => (
                  <div className="studio-job-error" key={job.id}>
                    <StatusBadge status={job.status} />
                    <p>
                      {job.error ||
                        "The local worker stopped before this job completed."}
                    </p>
                    <span className="studio-small muted">
                      {formatTime(job.updated)}
                    </span>
                  </div>
                ))}
              </div>
            </ThreadPrimitive.Viewport>
            <div className="studio-composer-wrap">
              {activeJob ? (
                <div className="studio-active-job">
                  <StatusBadge status={activeJob.status} />
                  <span className="studio-small muted">
                    {activeJob.status === "queued"
                      ? "Saved. Waiting for the local worker."
                      : "The local worker is running this job."}
                  </span>
                </div>
              ) : null}
              {query.error ? (
                <ErrorNotice
                  error={query.error}
                  retry={() => void query.refetch()}
                />
              ) : null}
              {send.error ? <ErrorNotice error={send.error} /> : null}
              {cancel.error ? <ErrorNotice error={cancel.error} /> : null}
              {update.error ? <ErrorNotice error={update.error} /> : null}
              {latest.session.archived ? (
                <p className="studio-small muted">
                  Restore this session to continue the conversation.
                </p>
              ) : (
                <>
                  <ComposerPrimitive.Root className="studio-composer">
                    <ComposerPrimitive.Input
                      className="studio-composer-input"
                      aria-label="Message"
                      placeholder="Ask about your applications…"
                      maxLength={8000}
                      rows={1}
                      maxRows={7}
                      addAttachmentOnPaste={false}
                    />
                    <div className="studio-composer-actions">
                      {isRunning && activeId ? (
                        <ComposerPrimitive.Cancel
                          className="studio-send-button"
                          aria-label="Cancel job"
                          disabled={cancel.isPending}
                        >
                          <Square size={16} />
                        </ComposerPrimitive.Cancel>
                      ) : (
                        <ComposerPrimitive.Send
                          className="studio-send-button"
                          aria-label="Send message"
                        >
                          <ArrowUp size={19} />
                        </ComposerPrimitive.Send>
                      )}
                    </div>
                  </ComposerPrimitive.Root>
                  <p className="studio-composer-footnote">
                    Investigation uses recorded context. Repairs require an
                    explicit action.
                  </p>
                </>
              )}
            </div>
          </ThreadPrimitive.Root>
        </AssistantRuntimeProvider>
      </section>
      <ContextPanel
        context={latest.context ?? overview.context}
        preferences={overview.preferences}
      />
    </>
  );
}
