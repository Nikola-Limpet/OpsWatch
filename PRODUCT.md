# OpsWatch

OpsWatch is one developer's workspace for monitoring production applications and supervising AI-assisted repairs. It combines recorded application, provider, log, and database signals with Telegram diagnosis and an isolated repair worker that prepares draft pull requests.

The primary task is to identify what needs attention, inspect evidence, and choose the next action. Application health, monitoring coverage, and agent work must remain distinguishable. Missing or stale observations never establish that an application is healthy.

The local monitor and workers own provider access and production evidence. The private cloud dashboard receives redacted snapshots and queues supported commands through the existing bridge. Monitoring depends on the development computer remaining online. Repairs do not merge or deploy themselves.

All displayed counts, statuses, jobs, links, and measurements must come from recorded data. Generated design references contain illustrative data and proposed interactions; they are not operational evidence.
