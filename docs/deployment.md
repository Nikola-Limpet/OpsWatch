# Private dashboard deployment

OpsWatch has two execution environments. The local monitor and repair worker own operational credentials and actions. Vercel runs only the dashboard relay. Deploying the web app does not move the monitor or repair worker into a serverless function.

## Vercel configuration

Connect the repository to your existing Vercel project and use the repository root as its Root Directory. Select the Other framework preset. The committed `vercel.json` routes dashboard requests to `api/index.py`, includes the UI assets, and excludes local runtime data and development artifacts. Python is pinned to 3.12.

The current configuration uses Singapore (`sin1`). If you deploy your own instance, choose a suitable region near the dedicated relay database.

Configure these as sensitive environment variables on the dashboard project:

| Variable | Purpose |
| --- | --- |
| `OPSWATCH_RELAY_DATABASE_URL` | PostgreSQL connection for the dedicated relay |
| `OPSWATCH_RELAY_BRIDGE_TOKEN` | Authenticate the local bridge |
| `OPSWATCH_RELAY_CSRF_TOKEN` | Stable, distinct browser action token |

Use different values for the bridge token and CSRF token. Keep provider credentials, monitoring DSNs, and Telegram credentials out of the Vercel project. Preserve Vercel Authentication for all deployments and any existing firewall restrictions.

Configure Production to deploy from `main`. Other branches can produce protected Preview deployments. Preview deployments need their own initialized relay database and matching environment variables if they will be exercised as a separate workspace; never silently point an experimental instance at production.

Vercel's Git integration deploys repository commits. No Vercel API token needs to be stored in GitHub Actions. The included GitHub workflow runs tests, lint, formatting, and UI behavior checks; it does not change production resources.

## Initialize the relay

Provision a dedicated PostgreSQL database. Initialize its relay tables once from a trusted local environment with the relay DSN supplied privately:

```sh
uv run python -c 'import os; from opswatch.cloud_store import PostgresRelay; PostgresRelay(os.environ["OPSWATCH_RELAY_DATABASE_URL"]).initialize()'
```

Initialization creates only OpsWatch relay tables. Do not point this command at an application database.

## Connect the local bridge

Configure these on the machine running the monitor:

| Variable | Purpose |
| --- | --- |
| `OPSWATCH_RELAY_URL` | Dashboard HTTPS origin, without a path or query |
| `OPSWATCH_RELAY_BRIDGE_TOKEN` | Same bridge token configured on Vercel |
| `OPSWATCH_RELAY_BYPASS_TOKEN` | Vercel automation bypass token for the bridge, when authentication requires it |

The bypass token is sent as a request header, never in a URL or browser bundle. It remains local. The browser still signs in normally.

```sh
uv run opswatch install-cloud-service
systemctl --user daemon-reload
systemctl --user enable --now opswatch-cloud-bridge
```

The bridge publishes snapshots every 30 seconds and polls supported commands every 10 seconds. Cloud readings become stale after 60 seconds without a fresh publication. New cloud control requests are rejected while the bridge is offline. If protected open-incident or active-job data cannot fit into a snapshot, the bridge leaves the previous snapshot stale rather than publishing incomplete current coverage.

## Verify a deployment

1. Confirm Vercel reports the deployment as Ready.
2. Sign in to the protected dashboard and check that recorded health and worker status are current.
3. Inspect incident evidence, project groups, and Agent work without starting a repair.
4. Check local bridge logs if readings remain stale:

```sh
systemctl --user status opswatch-cloud-bridge
journalctl --user -u opswatch-cloud-bridge -n 30
```

## Optional allowlisted CLI bundle

For manual deployments, the existing builder creates a minimal source bundle under the ignored `data/` directory:

```sh
python scripts/build-cloud-dashboard.py
```

Link and deploy the printed directory to the intended Vercel project. The builder copies only the relay modules, dashboard assets, runtime requirements, and Vercel configuration. It does not copy the local configuration, SQLite database, provider sessions, or `.env`.

## Public repository boundaries

Local `.env` files, configuration, discovery inventories, databases, worktrees, Vercel links, live screenshots, saved dashboard HTML, and internal work notes are ignored by Git. Keep those exclusions when adapting the project. Before adding a screenshot or incident example to an issue or README, replace operational details with synthetic data and review it manually.

References: [Vercel Git deployments](https://vercel.com/docs/git), [Python runtime](https://vercel.com/docs/functions/runtimes/python), and [function configuration](https://vercel.com/docs/project-configuration/vercel-json).
