import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReportsPage, StudioPage, WorkflowPage } from "./pages";

const api = vi.hoisted(() => ({
  request: vi.fn(),
  action: vi.fn(),
  snapshot: {
    projects: [] as { id: string; name: string }[],
    services: [] as { id: string; name: string; environment: string }[],
  },
}));
vi.mock("../../api", () => ({
  ...api,
  useSnapshot: () => ({ data: api.snapshot }),
}));
const sessionId = "00000000-0000-4000-8000-000000000001";
const messageId = "00000000-0000-4000-8000-000000000002";
const preferences = {
  revision: 1,
  principles: ["Read the evidence"],
  stack: ["Python"],
  review_focus: [],
  verification: ["Run tests"],
  notes: "",
  workflow: ["evidence", "hypothesis", "plan", "verify", "report", "learn"],
  updated: 1,
};
const pstack = {
  name: "pstack",
  repository: "https://github.com/cursor/plugins",
  revision: "9580c23600000000000000000000000000000000",
  license: "MIT",
  adaptation:
    "Read-only workflow guidance adapted for OpsWatch investigations.",
  playbooks: [
    {
      id: "investigation",
      title: "Investigation",
      description: "Understand the recorded issue.",
      url: "https://github.com/cursor/plugins/tree/main/pstack",
      steps: ["Collect evidence", "Compare hypotheses"],
    },
    {
      id: "perf-issue",
      title: "Performance issue",
      description: "Measure before optimizing.",
      url: "https://github.com/cursor/plugins/tree/main/pstack",
      steps: ["Measure the slow path", "Verify the change"],
    },
    {
      id: "reflection",
      title: "Reflection",
      description: "Learn from the result.",
      url: "https://github.com/cursor/plugins/tree/main/pstack",
      steps: ["Review the evidence"],
    },
  ],
  principles: [
    {
      id: "evidence",
      title: "Use evidence",
      summary: "Explain conclusions with recorded observations.",
      url: "https://github.com/cursor/plugins/tree/main/pstack",
    },
  ],
};
const context = {
  generated_at: 1,
  scope: { kind: "workspace", id: null },
  coverage: {
    counts: { services: 0 },
    included: { services: 0 },
    historical_measurements: false,
    truncated: false,
  },
  sources: [],
  notes: [],
  assumptions: [],
  monitor: { status: "unknown", last_cycle: null },
};
const session = {
  id: sessionId,
  title: "Recorded investigation",
  role: "chat",
  scope: { kind: "workspace", id: null },
  created: 1,
  updated: 1,
  last_message: "",
  active_job_id: null,
  archived: false,
};
function overview(overrides = {}) {
  return {
    sessions: [session],
    reports: [],
    problems: [],
    preferences,
    pstack,
    worker: {
      status: "not_started",
      queued: 0,
      active_job_id: null,
      last_cycle: null,
    },
    context,
    counts: {},
    has_more: {},
    ...overrides,
  };
}
function mount(component: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>{component}</QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  window.matchMedia = vi.fn().mockReturnValue({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  });
  window.history.replaceState({}, "", "/studio");
  api.snapshot.projects = [];
  api.snapshot.services = [];
  api.request.mockResolvedValue(overview());
  api.action.mockImplementation(async (_path, body) => {
    if (body.action === "session_read")
      return {
        session,
        messages: [
          {
            id: messageId,
            session_id: sessionId,
            role: "assistant",
            content: "There is no recorded incident evidence yet.",
            status: "complete",
            job_id: null,
            created: 1,
            updated: 1,
            sources: [],
          },
        ],
        jobs: [],
        has_more: false,
        next_before: null,
        context,
      };
    return {};
  });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
describe("persistent Studio interfaces", () => {
  it("keeps the recorded feedback selected when a saved conversation is reopened", async () => {
    const user = userEvent.setup();
    let rating: "helpful" | "unhelpful" | null = null;
    api.action.mockImplementation(async (_path, body) => {
      if (body.action === "feedback") {
        rating = body.payload.rating;
        return { feedback: { rating } };
      }
      if (body.action === "session_read")
        return {
          session,
          messages: [
            {
              id: messageId,
              session_id: sessionId,
              role: "assistant",
              content: "A saved finding.",
              status: "complete",
              feedback: rating,
              job_id: null,
              created: 1,
              updated: 1,
              sources: [],
            },
          ],
          jobs: [],
          has_more: false,
          next_before: null,
          context,
        };
      return {};
    });
    const first = mount(<StudioPage />);
    await user.click(await screen.findByRole("button", { name: "Helpful" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Helpful" })).toHaveAttribute(
        "aria-pressed",
        "true",
      ),
    );
    first.unmount();
    mount(<StudioPage />);
    expect(
      await screen.findByRole("button", { name: "Helpful" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Unhelpful" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });
  it("resolves saved scope identities to recorded project and service names", async () => {
    api.snapshot.projects = [{ id: "project-hash", name: "Northstar" }];
    api.snapshot.services = [
      { id: "service-hash", name: "Checkout API", environment: "production" },
    ];
    const projectSession = {
      ...session,
      scope: { kind: "project", id: "project-hash" },
    };
    api.request.mockResolvedValue(overview({ sessions: [projectSession] }));
    api.action.mockResolvedValue({
      session: projectSession,
      messages: [],
      jobs: [],
      has_more: false,
      next_before: null,
      context: { ...context, scope: { kind: "service", id: "service-hash" } },
    });
    mount(<StudioPage />);
    expect(await screen.findByText("Project · Northstar")).toBeInTheDocument();
    expect(screen.getByText("Service · Checkout API")).toBeInTheDocument();
  });
  it("loads older saved sessions and includes them in the existing search", async () => {
    const user = userEvent.setup();
    const older = {
      ...session,
      id: "00000000-0000-4000-8000-000000000010",
      title: "Earlier scoped investigation",
      updated: 0.5,
    };
    api.request.mockResolvedValue(overview({ has_more: { sessions: true } }));
    api.action.mockImplementation(async (_path, body) => {
      if (body.action === "collection_read")
        return { items: [older], has_more: false, next_before: null };
      if (body.action === "session_read")
        return {
          session,
          messages: [],
          jobs: [],
          has_more: false,
          next_before: null,
          context,
        };
      return {};
    });
    mount(<StudioPage />);
    await user.click(
      await screen.findByRole("button", { name: "Load older sessions" }),
    );
    await screen.findByRole("button", { name: /Earlier scoped investigation/ });
    await user.type(
      screen.getByRole("textbox", { name: "Search sessions" }),
      "Earlier scoped",
    );
    expect(
      screen.getByRole("button", { name: /Earlier scoped investigation/ }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", {
        name: /Recorded investigation Investigator/,
      }),
    ).toBeNull();
    expect(api.action).toHaveBeenCalledWith("/api/studio/actions", {
      action: "collection_read",
      payload: { collection: "sessions", before: sessionId, limit: 20 },
    });
  });
  it.each(["reports", "problems"] as const)(
    "loads older %s without duplicating records already in the overview",
    async (collection) => {
      const user = userEvent.setup();
      const current = {
        id: "00000000-0000-4000-8000-000000000020",
        title: "Current saved work",
        updated: 2,
        created: 2,
        scope: session.scope,
        status: collection === "reports" ? "succeeded" : "proposed",
        summary: "Current proposal",
        priority: "medium",
        validation: "Check the next sample",
        revision: 1,
        notes: "",
        evidence_refs: [],
        source_report_id: null,
      };
      const older = {
        ...current,
        id: "00000000-0000-4000-8000-000000000021",
        title: "Earlier saved work",
        updated: 1,
        created: 1,
      };
      api.request.mockResolvedValue(
        overview({ [collection]: [current], has_more: { [collection]: true } }),
      );
      api.action.mockImplementation(async (_path, body) =>
        body.action === "collection_read"
          ? { items: [current, older], has_more: false, next_before: null }
          : {},
      );
      mount(collection === "reports" ? <ReportsPage /> : <WorkflowPage />);
      await user.click(
        await screen.findByRole("button", { name: `Load older ${collection}` }),
      );
      expect(
        await screen.findByRole("button", { name: /Earlier saved work/ }),
      ).toBeInTheDocument();
      expect(
        screen.getAllByRole("button", { name: /Current saved work/ }),
      ).toHaveLength(1);
      expect(api.action).toHaveBeenCalledWith("/api/studio/actions", {
        action: "collection_read",
        payload: { collection, before: current.id, limit: 20 },
      });
    },
  );
  it("shows which model inputs were included when a saved context is bounded", async () => {
    api.action.mockResolvedValue({
      session,
      messages: [],
      jobs: [],
      has_more: false,
      next_before: null,
      context: {
        ...context,
        coverage: {
          ...context.coverage,
          truncated: true,
          prompt: {
            limit_bytes: 60000,
            bytes: 49000,
            available: {
              history: 15,
              feedback: 8,
              existing_problems: 12,
              preference_items: 10,
              preference_notes_characters: 400,
            },
            selected: {
              history: 8,
              feedback: 8,
              existing_problems: 12,
              preference_items: 10,
              preference_notes_characters: 400,
            },
            included: {
              history: 4,
              feedback: 2,
              existing_problems: 3,
              preference_items: 6,
              preference_notes_characters: 200,
            },
            truncated: true,
            omissions: [
              "history",
              "feedback",
              "existing_problems",
              "preference review_focus",
              "preference notes",
            ],
          },
        },
      },
    });
    mount(<StudioPage />);
    const disclosure = (
      await screen.findByText("Model input coverage")
    ).closest("details");
    expect(disclosure).toHaveAttribute("open");
    expect(screen.getByText("Recent messages").parentElement).toHaveTextContent(
      "4 / 15",
    );
    expect(screen.getByText("Saved feedback").parentElement).toHaveTextContent(
      "2 / 8",
    );
    expect(screen.getByText("Open problems").parentElement).toHaveTextContent(
      "3 / 12",
    );
    expect(
      screen.getByText("Preference items").parentElement,
    ).toHaveTextContent("6 / 10");
    expect(screen.getByText("Conversation history")).toBeInTheDocument();
    expect(screen.getByText("Review focus preferences")).toBeInTheDocument();
    expect(screen.getByText("49,000 / 60,000 bytes")).toBeInTheDocument();
  });
  it("saves the selected pstack playbook with a new session", async () => {
    const user = userEvent.setup();
    api.action.mockImplementation(async (_path, body) => {
      if (body.action === "session_create")
        return {
          session: {
            ...session,
            role: body.payload.role,
            playbook: body.payload.playbook,
          },
        };
      if (body.action === "session_read")
        return {
          session,
          messages: [],
          jobs: [],
          has_more: false,
          next_before: null,
          context,
        };
      return {};
    });
    mount(<StudioPage />);
    await user.click(
      await screen.findByRole("radio", { name: "Improvement agent" }),
    );
    const playbook = screen.getByRole("combobox", { name: "Pstack playbook" });
    expect((playbook as HTMLSelectElement).value).toBe("reflection");
    await user.selectOptions(playbook, "perf-issue");
    await user.click(screen.getByRole("button", { name: "New session" }));
    await waitFor(() =>
      expect(api.action).toHaveBeenCalledWith("/api/studio/actions", {
        action: "session_create",
        payload: {
          role: "improvement",
          scope: { kind: "workspace", id: null },
          playbook: "perf-issue",
        },
      }),
    );
  });
  it("shows the pinned pstack source and actual playbook steps in Workflow", async () => {
    mount(<WorkflowPage />);
    const source = await screen.findByRole("link", { name: /Source revision/ });
    expect(source).toHaveAttribute(
      "href",
      `https://github.com/cursor/plugins/commit/${pstack.revision}`,
    );
    expect(screen.getByText("Collect evidence")).toBeInTheDocument();
    expect(
      screen.getByText("Explain conclusions with recorded observations."),
    ).toBeInTheDocument();
    expect(screen.getByText(pstack.adaptation)).toBeInTheDocument();
  });
  it("sends the typed message to the selected saved session and preserves server evidence", async () => {
    const user = userEvent.setup();
    mount(<StudioPage />);
    await screen.findByText("There is no recorded incident evidence yet.");
    await user.type(
      screen.getByRole("textbox", { name: "Message" }),
      "What is recorded?",
    );
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() =>
      expect(api.action).toHaveBeenCalledWith("/api/studio/actions", {
        action: "message_send",
        payload: { session_id: sessionId, content: "What is recorded?" },
      }),
    );
    expect(
      screen.queryByText(/looked at your application context/i),
    ).toBeNull();
  });
  it("keeps a failed message in the composer and does not retry its action", async () => {
    const user = userEvent.setup();
    api.action.mockImplementation(async (_path, body) => {
      if (body.action === "session_read")
        return {
          session,
          messages: [],
          jobs: [],
          has_more: false,
          next_before: null,
          context,
        };
      if (body.action === "message_send")
        throw new Error("The local worker is unavailable.");
      return {};
    });
    mount(<StudioPage />);
    const input = await screen.findByRole("textbox", { name: "Message" });
    await user.type(input, "Keep my question");
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await screen.findByText("The local worker is unavailable.");
    await waitFor(() =>
      expect((input as HTMLTextAreaElement).value).toBe("Keep my question"),
    );
    expect(
      api.action.mock.calls.filter(
        ([, body]) => body.action === "message_send",
      ),
    ).toHaveLength(1);
  });
  it("loads older saved messages without removing the latest response", async () => {
    const user = userEvent.setup();
    const olderId = "00000000-0000-4000-8000-000000000003";
    api.action.mockImplementation(async (_path, body) => {
      if (body.action === "session_read")
        return {
          session,
          messages: [
            {
              id: body.payload.before ? olderId : messageId,
              session_id: sessionId,
              role: "assistant",
              content: body.payload.before
                ? "Earlier recorded response."
                : "Latest recorded response.",
              status: "complete",
              job_id: null,
              created: body.payload.before ? 1 : 2,
              updated: 2,
              sources: [],
            },
          ],
          jobs: [],
          has_more: !body.payload.before,
          next_before: body.payload.before ? null : messageId,
          context,
        };
      return {};
    });
    mount(<StudioPage />);
    await screen.findByText("Latest recorded response.");
    await user.click(
      screen.getByRole("button", { name: "Load older messages" }),
    );
    await screen.findByText("Earlier recorded response.");
    expect(screen.getByText("Latest recorded response.")).toBeInTheDocument();
    expect(api.action).toHaveBeenCalledWith("/api/studio/actions", {
      action: "session_read",
      payload: { session_id: sessionId, limit: 20, before: messageId },
    });
  });
  it("cancels the selected session queued job through the persistent action", async () => {
    const user = userEvent.setup();
    const jobId = "00000000-0000-4000-8000-000000000004";
    api.action.mockImplementation(async (_path, body) => {
      if (body.action === "session_read")
        return {
          session: { ...session, active_job_id: jobId },
          messages: [
            {
              id: messageId,
              session_id: sessionId,
              role: "assistant",
              content: "",
              status: "queued",
              job_id: jobId,
              created: 1,
              updated: 1,
              sources: [],
            },
          ],
          jobs: [
            {
              id: jobId,
              session_id: sessionId,
              kind: "chat",
              status: "queued",
              error: null,
            },
          ],
          has_more: false,
          next_before: null,
          context,
        };
      return {};
    });
    mount(<StudioPage />);
    await user.click(await screen.findByRole("button", { name: "Cancel job" }));
    await waitFor(() =>
      expect(api.action).toHaveBeenCalledWith("/api/studio/actions", {
        action: "job_cancel",
        payload: { job_id: jobId },
      }),
    );
  });
  it("keeps edited preferences visible after a revision conflict", async () => {
    const user = userEvent.setup();
    api.action.mockRejectedValue(
      new Error("Preference revision conflict. Reload saved preferences."),
    );
    mount(<WorkflowPage />);
    const field = await screen.findByRole("textbox", {
      name: "Engineering principles",
    });
    await user.clear(field);
    await user.type(field, "Keep this draft");
    await user.click(screen.getByRole("button", { name: "Save preferences" }));
    await screen.findByText(/Preference revision conflict/);
    expect((field as HTMLTextAreaElement).value).toBe("Keep this draft");
    expect(api.action).toHaveBeenCalledWith(
      "/api/studio/actions",
      expect.objectContaining({
        action: "preferences_update",
        payload: expect.objectContaining({
          revision: 1,
          principles: ["Keep this draft"],
        }),
      }),
    );
  });
  it("reloads the current saved preference revision after a conflict", async () => {
    const user = userEvent.setup();
    api.action.mockRejectedValue(
      new Error(
        "Preferences changed. Reload their current revision before saving.",
      ),
    );
    mount(<WorkflowPage />);
    const field = await screen.findByRole("textbox", {
      name: "Engineering principles",
    });
    await user.clear(field);
    await user.type(field, "Unsaved draft");
    await user.click(screen.getByRole("button", { name: "Save preferences" }));
    await screen.findByText(/Preferences changed/);
    api.request.mockResolvedValue(
      overview({
        preferences: {
          ...preferences,
          revision: 2,
          principles: ["Current saved principle"],
        },
      }),
    );
    await user.click(
      screen.getByRole("button", { name: "Reload saved preferences" }),
    );
    await waitFor(() =>
      expect((field as HTMLTextAreaElement).value).toBe(
        "Current saved principle",
      ),
    );
    expect(screen.getByText("Revision 2")).toBeInTheDocument();
  });
  it("shows loading while a saved report summary waits for its full content", async () => {
    const user = userEvent.setup();
    const report = {
      id: "00000000-0000-4000-8000-000000000030",
      title: "Delayed saved report",
      scope: session.scope,
      session_id: sessionId,
      job_id: messageId,
      status: "succeeded",
      period: "day",
      window_start: 1,
      window_end: 2,
      created: 2,
      updated: 2,
      preference_revision: 1,
      error: null,
      sources: [],
    };
    let finishRead!: (value: unknown) => void;
    const detailRead = new Promise((resolve) => {
      finishRead = resolve;
    });
    api.request.mockResolvedValue(overview({ reports: [report] }));
    api.action.mockImplementation(async (_path, body) =>
      body.action === "report_read" ? detailRead : {},
    );
    mount(<ReportsPage />);
    await user.click(
      await screen.findByRole("button", { name: /Delayed saved report/ }),
    );
    expect(
      screen.queryByText("No report content was recorded for this job."),
    ).toBeNull();
    expect(screen.getByText("Loading report…")).toBeInTheDocument();
    await act(async () =>
      finishRead({
        report: { ...report, content: "The full saved findings." },
      }),
    );
    await screen.findByText("The full saved findings.");
  });
  it("keeps failed report reads unavailable and retries without claiming an empty report", async () => {
    const user = userEvent.setup();
    const report = {
      id: "00000000-0000-4000-8000-000000000031",
      title: "Unavailable saved report",
      scope: session.scope,
      session_id: sessionId,
      job_id: messageId,
      status: "succeeded",
      period: "day",
      window_start: 1,
      window_end: 2,
      created: 2,
      updated: 2,
      preference_revision: 1,
      error: null,
      sources: [],
    };
    api.request.mockResolvedValue(overview({ reports: [report] }));
    api.action.mockRejectedValue(new Error("Detail relay unavailable."));
    mount(<ReportsPage />);
    await user.click(
      await screen.findByRole("button", { name: /Unavailable saved report/ }),
    );
    await screen.findByText("Detail relay unavailable.");
    expect(
      screen.queryByText("No report content was recorded for this job."),
    ).toBeNull();
    expect(
      screen.getByRole("heading", { name: "Unavailable saved report" }),
    ).toBeInTheDocument();
    api.action.mockResolvedValue({
      report: { ...report, content: "Recovered saved findings." },
    });
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByText("Recovered saved findings.");
  });
  it("reports empty content only after a successful full report read explicitly returns it", async () => {
    const user = userEvent.setup();
    const report = {
      id: "00000000-0000-4000-8000-000000000032",
      title: "Empty saved report",
      scope: session.scope,
      session_id: sessionId,
      job_id: messageId,
      status: "succeeded",
      period: "day",
      window_start: 1,
      window_end: 2,
      created: 2,
      updated: 2,
      preference_revision: 1,
      error: null,
      sources: [],
    };
    api.request.mockResolvedValue(overview({ reports: [report] }));
    api.action.mockResolvedValue({ report: { ...report, content: null } });
    mount(<ReportsPage />);
    await user.click(
      await screen.findByRole("button", { name: /Empty saved report/ }),
    );
    await screen.findByText("No report content was recorded for this job.");
  });
  it("reads a saved report and records feedback on that report", async () => {
    const user = userEvent.setup();
    const reportId = "00000000-0000-4000-8000-000000000005";
    let report = {
      feedback: null as "helpful" | "unhelpful" | null,
      id: reportId,
      title: "Recorded coverage report",
      scope: session.scope,
      session_id: sessionId,
      job_id: messageId,
      status: "succeeded",
      period: "day",
      window_start: 1,
      window_end: 2,
      created: 2,
      updated: 2,
      preference_revision: 1,
      content: "## Coverage\nOnly the recorded check is available.",
      error: null,
      sources: ["check:recorded"],
    };
    api.request.mockResolvedValue(
      overview({ reports: [{ ...report, content: undefined }] }),
    );
    api.action.mockImplementation(async (_path, body) => {
      if (body.action === "feedback") {
        report = { ...report, feedback: body.payload.rating };
        return {};
      }
      return body.action === "report_read" ? { report } : {};
    });
    mount(<ReportsPage />);
    await user.click(
      await screen.findByRole("button", { name: /Recorded coverage report/ }),
    );
    await screen.findByText("Only the recorded check is available.");
    await user.click(screen.getByRole("button", { name: "Helpful" }));
    await screen.findByText("Feedback saved");
    expect(api.action).toHaveBeenCalledWith("/api/studio/actions", {
      action: "feedback",
      payload: {
        subject_type: "report",
        subject_id: reportId,
        rating: "helpful",
      },
    });
  });
  it("saves an explicit problem review with its current revision", async () => {
    const user = userEvent.setup();
    const problemId = "00000000-0000-4000-8000-000000000006";
    let problem = {
      id: problemId,
      title: "Review repeated errors",
      summary: "Two recorded incidents share an error.",
      priority: "high",
      status: "proposed",
      scope: session.scope,
      evidence_refs: ["incident:recorded"],
      validation: "Check whether the next observation repeats the error.",
      source_job_id: messageId,
      source_report_id: null,
      created: 1,
      updated: 1,
      revision: 1,
      notes: "",
    };
    api.request.mockImplementation(async () =>
      overview({ problems: [problem] }),
    );
    api.action.mockImplementation(async (_path, body) => {
      if (body.action === "problem_update") {
        problem = { ...problem, ...body.payload, revision: 2 };
        return { problem };
      }
      return {};
    });
    mount(<WorkflowPage />);
    await screen.findByRole("heading", { name: "Review repeated errors" });
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Review status" }),
      "accepted",
    );
    await user.type(
      screen.getByRole("textbox", { name: "Your review notes" }),
      "Investigate next",
    );
    await user.click(screen.getByRole("button", { name: "Save review" }));
    await screen.findByText("Review saved");
    expect(api.action).toHaveBeenCalledWith("/api/studio/actions", {
      action: "problem_update",
      payload: {
        problem_id: problemId,
        revision: 1,
        status: "accepted",
        notes: "Investigate next",
      },
    });
  });
  it("rejects an invalid custom report window before creating a job", async () => {
    const user = userEvent.setup();
    mount(<ReportsPage />);
    await screen.findByRole("heading", { name: "Reports" });
    await user.selectOptions(
      await screen.findByLabelText("Reporting period"),
      "custom",
    );
    await user.click(screen.getByRole("button", { name: "Generate report" }));
    await screen.findByText(/Choose a start and end time/);
    expect(api.action).not.toHaveBeenCalledWith(
      "/api/studio/actions",
      expect.objectContaining({ action: "report_create" }),
    );
  });
});
