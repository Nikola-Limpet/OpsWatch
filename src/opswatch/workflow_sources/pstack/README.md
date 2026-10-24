# pstack source subset

Upstream: https://github.com/cursor/plugins/tree/main/pstack

Revision: `9580c23696ac8d7abed0c166f2f9918800afb819`

Author: Lauren Tan. License: MIT; see `LICENSE` in this directory.

These files are an unmodified subset of the upstream plugin at the pinned
revision. `sources.json` records SHA-256 hashes and original paths. OpsWatch uses
the selected playbook and seven principle documents as saved, versioned guidance
for each agent job. The poteto-mode routing document is retained as reference.

OpsWatch's catalog summaries and UI steps are adaptations. This is not a Cursor
plugin host: it does not execute upstream commands, load the entire skill tree,
spawn the upstream model defaults, or edit shared skills. The configured local
Hermes/Codex engine remains responsible for a read-only response based on the
supplied evidence. The app exposes source links and saves the actual documents
used, so an owner can inspect the methodology behind a result.
