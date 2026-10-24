# OpsWatch

OpsWatch is one developer's workspace for monitoring production applications and supervising AI-assisted repairs. It combines recorded application, provider, log, and database signals with Telegram diagnosis and an isolated repair worker that prepares draft pull requests.

The primary task is to identify what needs attention, inspect evidence, and choose the next action. Application health, monitoring coverage, and agent work must remain distinguishable. Missing or stale observations never establish that an application is healthy.

The local monitor and workers own provider access and production evidence. The private cloud dashboard receives redacted snapshots and queues supported commands through the existing bridge. Monitoring depends on the development computer remaining online. Repairs do not merge or deploy themselves.

All displayed counts, statuses, jobs, links, and measurements must come from recorded data. Generated design references contain illustrative data and proposed interactions; they are not operational evidence.

Agent Studio keeps durable sessions scoped to the workspace, a project, or a service.
Investigation, reporting, and improvement jobs use pinned evidence and explicit owner
preferences through the configured Hermes or Codex engine. The MIT-licensed assistant-ui
runtime supplies the chat interface. Reports retain their sources and coverage limits;
improvement runs create reviewable proposals. Feedback informs later runs without
silently changing preferences. Pstack playbooks and principles are versioned guidance,
with upstream provenance and the read-only runtime adaptation visible in Workflow.

The frontend uses React and TanStack Router, Query, Table, and Form. FastAPI serves
the local app and the private cloud relay. Agent jobs execute locally and survive
browser navigation; serverless requests only transport supported commands and results.
