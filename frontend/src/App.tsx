import { useEffect } from 'react';
import {
  createRootRoute,
  createRoute,
  createRouter,
  lazyRouteComponent,
  Link,
  Outlet,
  RouterProvider,
  useRouterState,
} from '@tanstack/react-router';
import type { RouterHistory } from '@tanstack/react-router';
import { useQueryClient } from '@tanstack/react-query';
import {
  Activity,
  AlertTriangle,
  Bot,
  ChartNoAxesColumn,
  Database,
  Folder,
  House,
  MessageSquare,
  RefreshCw,
  Settings2,
  ShieldCheck,
} from 'lucide-react';
import { useSnapshot } from './api';
import { age, jobPresentation, number } from './components';
import {
  IncidentsPage,
  OverviewPage,
  ProjectsPage,
  QueriesPage,
  RepairsPage,
} from './features/monitoring/pages';
import type { Snapshot } from './types';

const pages = {
  '/': {
    title: 'Overview',
    subtitle: 'A clear view of your production stack.',
    eyebrow: 'OPERATIONS AT A GLANCE',
  },
  '/projects': {
    title: 'Projects',
    subtitle: 'Control monitoring for your projects and services.',
    eyebrow: 'MONITORING COVERAGE',
  },
  '/incidents': {
    title: 'Incidents',
    subtitle: 'Understand the issue. Choose the next action.',
    eyebrow: 'EVIDENCE & RESPONSE',
  },
  '/agents': {
    title: 'Agent work',
    subtitle: 'Follow each fix, from investigation to a draft PR.',
    eyebrow: 'REPAIR WORKER',
  },
  '/queries': {
    title: 'Database queries',
    subtitle: 'Measured query timings from your connected databases.',
    eyebrow: 'RECORDED PERFORMANCE',
  },
  '/studio': { title: 'Agent Studio', subtitle: '', eyebrow: '' },
  '/reports': { title: 'Reports', subtitle: '', eyebrow: '' },
  '/workflow': { title: 'Workflow', subtitle: '', eyebrow: '' },
} as const;
const navigation = [
  { to: '/', label: 'Overview', icon: House },
  { to: '/projects', label: 'Projects', icon: Folder },
  { to: '/incidents', label: 'Incidents', icon: AlertTriangle },
  { to: '/agents', label: 'Agent work', icon: Bot },
  { to: '/queries', label: 'Database queries', icon: Database },
  { to: '/studio', label: 'Agent Studio', icon: MessageSquare },
  { to: '/reports', label: 'Reports', icon: ChartNoAxesColumn },
  { to: '/workflow', label: 'Workflow', icon: Settings2 },
] as const;

function healthFor(data: Snapshot, failed: boolean) {
  const fresh = !failed && data.monitor.status === 'running';
  return {
    healthy: fresh ? data.summary.healthy : 0,
    attention: fresh ? data.summary.attention : 0,
    unknown: fresh ? data.summary.unknown : data.summary.total,
    gaps: new Set(
      data.incidents
        .filter((item) => item.status === 'open' && item.kind === 'monitoring')
        .map((item) => item.target),
    ).size,
    active: data.repairs.filter((job) => jobPresentation(job).active).length,
    review: data.repairs.filter((job) => job.status === 'pr_open').length,
    blocked: data.repairs.filter((job) =>
      ['blocked', 'interrupted'].includes(job.status),
    ).length,
    worker: fresh ? data.repair_worker?.status || 'unverified' : 'unverified',
  };
}
function HealthStrip({ data, failed }: { data: Snapshot; failed: boolean }) {
  const health = healthFor(data, failed);
  return (
    <section className="health-strip" aria-label="Monitoring summary">
      <div className="health-item">
        <Activity className="health-symbol" size={23} aria-hidden="true" />
        <div>
          <span className="health-heading">
            Recorded service health
            <span className="health-total">
              {number(data.summary.total)} monitored
            </span>
          </span>
          <p>
            <span>
              <strong className="healthy">{number(health.healthy)}</strong>{' '}
              healthy
            </span>
            <span>
              <strong className="warning">{number(health.attention)}</strong>{' '}
              need attention
            </span>
            <span>
              <strong>{number(health.unknown)}</strong> unverified
            </span>
          </p>
        </div>
      </div>
      <div className="health-item">
        <ShieldCheck className="health-symbol" size={23} aria-hidden="true" />
        <div>
          <span className="health-heading">Monitoring coverage</span>
          <p>
            {failed || data.monitor.status !== 'running' ? (
              <span className="warning">Current coverage unverified</span>
            ) : health.gaps ? (
              <span>
                <strong className="warning">{number(health.gaps)}</strong>{' '}
                services with collection errors
              </span>
            ) : (
              <span>
                <span className="dot healthy" />
                No collection errors recorded
              </span>
            )}
          </p>
        </div>
      </div>
      <Link className="health-item health-agent" to="/agents">
        <Bot className="health-symbol" size={23} aria-hidden="true" />
        <div>
          <span className="health-heading">
            Agent work
            <span className="health-total">
              {health.worker === 'running'
                ? 'Worker online'
                : health.worker === 'stale'
                  ? 'Worker heartbeat stale'
                  : health.worker === 'not_started'
                    ? 'Worker not started'
                    : 'Worker unverified'}
            </span>
          </span>
          <p>
            <span>
              <strong>{number(health.active)}</strong> active
            </span>
            <span>
              <strong className={health.review ? 'healthy' : ''}>
                {number(health.review)}
              </strong>{' '}
              to review
            </span>
            {health.blocked ? (
              <span>
                <strong className="warning">{number(health.blocked)}</strong>{' '}
                blocked
              </span>
            ) : null}
          </p>
        </div>
      </Link>
    </section>
  );
}
function HealthNotice({
  data,
  failed,
  incidents,
}: {
  data: Snapshot;
  failed: boolean;
  incidents: boolean;
}) {
  const { summary, monitor } = data;
  const health = healthFor(data, failed);
  if (
    !failed &&
    monitor.status === 'running' &&
    summary.total > 0 &&
    (incidents || (!summary.attention && !summary.unknown))
  )
    return null;
  const message = failed
    ? 'Connection lost. Showing the last received snapshot. Current service health is unverified.'
    : monitor.status === 'not_started'
      ? 'Waiting for monitor data. Run opswatch run to start collecting checks.'
      : monitor.status === 'stale'
        ? 'Monitor readings are stale. Check the local monitor before relying on these results.'
        : health.gaps
          ? 'Some production signals are unavailable. Collection failures need attention; application health may be unverified.'
          : summary.attention
            ? `${number(summary.attention)} service${summary.attention === 1 ? ' needs' : 's need'} attention. Review the recorded evidence before taking action.`
            : summary.unknown
              ? `${number(summary.unknown)} service${summary.unknown === 1 ? ' has' : 's have'} unverified health. Review missing checks and monitoring coverage.`
              : 'Your workspace is ready. Add enabled targets to your configuration to begin monitoring.';
  return (
    <div className="notice warning" role="status">
      <AlertTriangle size={17} aria-hidden="true" />
      <span>{message}</span>
      <Link
        className="text-link"
        to={
          summary.attention || summary.open_incidents
            ? '/incidents'
            : '/projects'
        }
      >
        Review<span aria-hidden="true">→</span>
      </Link>
    </div>
  );
}
function Shell() {
  const path = useRouterState({ select: (state) => state.location.pathname });
  const page = pages[path as keyof typeof pages] || {
    title: 'Page not found',
    subtitle: '',
    eyebrow: '',
  };
  const studio = ['/studio', '/reports', '/workflow'].includes(path);
  const query = useSnapshot();
  const client = useQueryClient();
  const data = query.data;
  const health = data ? healthFor(data, query.isError) : null;
  const monitorState = query.isError
    ? 'Dashboard disconnected'
    : !data
      ? 'Connecting'
      : data.monitor.status === 'running'
        ? 'Local monitor online'
        : data.monitor.status === 'stale'
          ? 'Monitor data is stale'
          : 'Monitor not started';
  const workerState =
    health?.worker === 'running'
      ? 'Repair worker online'
      : health?.worker === 'stale'
        ? 'Repair worker heartbeat stale'
        : health?.worker === 'not_started'
          ? 'Repair worker not started'
          : 'Repair worker unverified';
  useEffect(() => {
    document.title = `OpsWatch · ${page.title}`;
  }, [page.title]);
  const refresh = () => {
    void client.invalidateQueries();
  };
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to dashboard
      </a>
      <div className={`app-shell ${studio ? 'studio-shell' : ''}`}>
        <aside className="sidebar">
          <Link className="brand" to="/" aria-label="OpsWatch overview">
            <span className="brand-mark">
              <Activity size={20} aria-hidden="true" />
            </span>
            OpsWatch
          </Link>
          <div className="workspace">
            <span className="workspace-name">Personal workspace</span>
            <small>Production monitoring</small>
          </div>
          <nav aria-label="Main navigation">
            {navigation.map((item, index) => {
              const Icon = item.icon;
              const count =
                item.to === '/projects'
                  ? data?.projects.length
                  : item.to === '/incidents'
                    ? data?.summary.open_incidents
                    : item.to === '/agents' && health
                      ? health.active + health.review + health.blocked
                      : undefined;
              return (
                <Link
                  key={item.to}
                  to={item.to}
                  className={`nav-link ${index === 5 ? 'nav-section-start' : ''}`}
                  activeProps={{ className: 'active', 'aria-current': 'page' }}
                  activeOptions={{ exact: true, includeSearch: false }}
                >
                  <Icon size={18} aria-hidden="true" />
                  <span>{item.label}</span>
                  {count != null ? (
                    <span className="nav-count" aria-hidden="true">
                      {number(count)}
                    </span>
                  ) : null}
                </Link>
              );
            })}
          </nav>
          <div className="sidebar-bottom">
            <div className="local-label">
              <span
                className={`dot ${!query.isError && data?.monitor.status === 'running' ? 'healthy' : 'warning'}`}
              />
              <span>{monitorState}</span>
            </div>
            <p>
              {data?.monitor.last_cycle
                ? `Last cycle ${age(data.monitor.last_cycle)}`
                : 'Waiting for the first cycle'}
            </p>
            <div className="local-foot">
              <Settings2 size={14} aria-hidden="true" />
              {workerState}
            </div>
          </div>
        </aside>
        <main id="main" tabIndex={-1}>
          <div className="breadcrumb">
            Workspace<span>/</span>
            <strong>{page.title}</strong>
            <span className="production-label">
              <span
                className={`dot ${!query.isError && data?.monitor.status === 'running' ? 'healthy' : 'warning'}`}
              />
              {!query.isError && data?.monitor.status === 'running'
                ? 'Monitor connected'
                : 'Monitoring unverified'}
            </span>
            {studio ? (
              <button
                className="icon-button breadcrumb-refresh"
                aria-label="Refresh workspace"
                title="Refresh workspace"
                disabled={query.isFetching}
                onClick={refresh}
              >
                <RefreshCw
                  size={15}
                  className={query.isFetching ? 'spin' : ''}
                />
              </button>
            ) : null}
          </div>
          {!studio && data ? (
            <HealthStrip data={data} failed={query.isError} />
          ) : null}
          {!studio ? (
            <header className="page-header">
              <div>
                <p className="eyebrow">{page.eyebrow}</p>
                <h1>{page.title}</h1>
                <p className="subtitle">{page.subtitle}</p>
              </div>
              <div className="header-actions">
                <div className="updated">
                  <span>
                    {data
                      ? `${query.isError ? 'Last received' : 'Updated'} ${age(data.generated_at)}`
                      : 'Waiting for data'}
                  </span>
                  <small>Auto-refresh every 30 seconds</small>
                </div>
                <button
                  className="button"
                  disabled={query.isFetching}
                  onClick={refresh}
                >
                  <RefreshCw
                    size={15}
                    className={query.isFetching ? 'spin' : ''}
                    aria-hidden="true"
                  />
                  Refresh
                </button>
              </div>
            </header>
          ) : null}
          {query.isError && !data ? (
            <div className="notice error" role="alert">
              <AlertTriangle size={17} aria-hidden="true" />
              <span>
                Unable to read the monitor. Check that the local dashboard is
                running, then refresh.
              </span>
              <button className="text-link" onClick={refresh}>
                Try again
              </button>
            </div>
          ) : null}
          {!studio && query.isPending ? (
            <div className="loading" role="status">
              Connecting to your local monitor…
            </div>
          ) : null}
          {!studio && data ? (
            <HealthNotice
              data={data}
              failed={query.isError}
              incidents={path === '/incidents'}
            />
          ) : null}
          {data || studio ? <Outlet /> : null}
          <footer className="page-footer">
            <span>
              <Activity size={13} aria-hidden="true" />
              OpsWatch<span className="footer-divider">/</span>Local monitoring,
              clear evidence.
            </span>
            <span className="footer-meta"><a href="/assets/THIRD-PARTY-LICENSES.txt" target="_blank" rel="noopener noreferrer">Open-source licenses</a><span>{data?.timezone.replaceAll('_', ' ')}</span></span>
          </footer>
        </main>
      </div>
    </>
  );
}
const rootRoute = createRootRoute({
  component: Shell,
  notFoundComponent: () => (
    <div className="empty">
      <h1>Page not found</h1>
      <p>This page is not part of your OpsWatch workspace.</p>
      <Link className="button" to="/">
        Back to Overview
      </Link>
    </div>
  ),
  errorComponent: ({ error, reset }) => (
    <div className="notice error" role="alert">
      <span>
        {error instanceof Error
          ? error.message
          : 'This page could not be loaded.'}
      </span>
      <button className="button" onClick={reset}>
        Try again
      </button>
    </div>
  ),
});
const overviewRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  component: OverviewPage,
});
const projectsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/projects',
  component: ProjectsPage,
});
const incidentsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/incidents',
  validateSearch: (search: Record<string, unknown>): { ref?: string } =>
    typeof search.ref === 'string' && /^OW-\d{1,17}$/.test(search.ref)
      ? { ref: search.ref }
      : {},
  component: function IncidentRoute() {
    const { ref } = incidentsRoute.useSearch();
    return <IncidentsPage key={ref} initialRef={ref} />;
  },
});
const agentsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/agents',
  component: RepairsPage,
});
const queriesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/queries',
  component: QueriesPage,
});
const studioRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/studio',
  component: lazyRouteComponent(
    () => import('./features/studio/pages'),
    'StudioPage',
  ),
});
const reportsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/reports',
  component: lazyRouteComponent(
    () => import('./features/studio/pages'),
    'ReportsPage',
  ),
});
const workflowRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/workflow',
  component: lazyRouteComponent(
    () => import('./features/studio/pages'),
    'WorkflowPage',
  ),
});
const routeTree = rootRoute.addChildren([
  overviewRoute,
  projectsRoute,
  incidentsRoute,
  agentsRoute,
  queriesRoute,
  studioRoute,
  reportsRoute,
  workflowRoute,
]);
export function createAppRouter(history?: RouterHistory) {
  return createRouter({
    routeTree,
    history,
    defaultPreload: 'intent',
    scrollRestoration: true,
  });
}
export function App({
  router,
}: {
  router: ReturnType<typeof createAppRouter>;
}) {
  return <RouterProvider router={router} />;
}
declare module '@tanstack/react-router' {
  interface Register {
    router: ReturnType<typeof createAppRouter>;
  }
}

export function redirectLegacyHash() {
  const legacy: Record<string, string> = {
    overview: '/',
    services: '/projects',
    projects: '/projects',
    incidents: '/incidents',
    agents: '/agents',
    queries: '/queries',
    studio: '/studio',
    reports: '/reports',
    workflow: '/workflow',
  };
  const destination = legacy[window.location.hash.slice(1)];
  if (destination)
    window.history.replaceState(
      window.history.state,
      '',
      destination + window.location.search,
    );
}
