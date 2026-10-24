import { Fragment, useMemo, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  createPaginatedRowModel,
  rowPaginationFeature,
  tableFeatures,
  useTable,
} from '@tanstack/react-table';
import { ArrowRight, ChevronDown, Search, Settings2, X } from 'lucide-react';
import { action, useSnapshot } from '../../api';
import {
  age,
  Avatar,
  Badge,
  Empty,
  number,
  ServiceEvidence,
  timestamp,
} from '../../components';
import type { Project, Service } from '../../types';

type VisibleProject = Project & { visible: Service[] };
type MonitoringChange = {
  scope: 'projects' | 'services';
  id: string;
  enabled: boolean;
};
const features = tableFeatures({
  rowPaginationFeature,
  paginatedRowModel: createPaginatedRowModel(),
});
const columns = [{ accessorKey: 'name', header: 'Project' }];
const emptyProjects: Project[] = [];

export function ProjectsPage() {
  const query = useSnapshot();
  const data = query.data;
  const client = useQueryClient();
  const [tab, setTab] = useState<'monitored' | 'inactive'>('monitored');
  const [search, setSearch] = useState('');
  const [provider, setProvider] = useState('');
  const [health, setHealth] = useState('');
  const [pagination, setPagination] = useState({ pageIndex: 0, pageSize: 10 });
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<Service | null>(null);
  const [evidence, setEvidence] = useState<Service | null>(null);
  const [feedback, setFeedback] = useState('');
  const [feedbackError, setFeedbackError] = useState(false);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const selectedTrigger = useRef<HTMLButtonElement | null>(null);
  const detailsRef = useRef<HTMLElement>(null);
  const projects = data?.projects || emptyProjects;
  const all = useMemo(
    () => projects.flatMap((project) => project.services),
    [projects],
  );
  const providers = useMemo(
    () => [...new Set(all.map((service) => service.provider))].sort(),
    [all],
  );
  const groups = useMemo<VisibleProject[]>(() => {
    const term = search.trim().toLowerCase();
    return projects
      .map((project) => ({
        ...project,
        visible: project.services.filter(
          (service) =>
            (tab === 'inactive'
              ? service.enabled === false
              : service.enabled !== false) &&
            `${project.name} ${service.name} ${service.id} ${service.provider}`
              .toLowerCase()
              .includes(term) &&
            (!provider || service.provider === provider) &&
            (tab === 'inactive' ||
              !health ||
              (health === 'attention'
                ? ['warning', 'critical'].includes(service.status)
                : health === 'unknown'
                  ? ['unknown', 'stale'].includes(service.status)
                  : service.status === health)),
        ),
      }))
      .filter((project) => project.visible.length)
      .sort((a, b) => {
        const rank = (project: VisibleProject) =>
          project.visible.some((service) =>
            ['warning', 'critical'].includes(service.status),
          )
            ? 0
            : 1;
        return rank(a) - rank(b) || a.name.localeCompare(b.name);
      });
  }, [projects, search, tab, provider, health]);
  const pages = Math.max(1, Math.ceil(groups.length / pagination.pageSize));
  const effectivePagination = useMemo(
    () => ({
      ...pagination,
      pageIndex: Math.min(pagination.pageIndex, pages - 1),
    }),
    [pagination, pages],
  );
  const table = useTable({
    features,
    data: groups,
    columns,
    getRowId: (project) => project.id,
    state: { pagination: effectivePagination },
    onPaginationChange: setPagination,
    autoResetPageIndex: false,
  });
  const mutation = useMutation({
    mutationFn: (change: MonitoringChange) =>
      action<{ targets: string[] }>(
        `/api/${change.scope}/${encodeURIComponent(change.id)}/monitoring`,
        { enabled: change.enabled },
      ),
    onMutate: () => {
      setFeedback('');
      setFeedbackError(false);
    },
    onSuccess: async (result, change) => {
      await client.invalidateQueries({ queryKey: ['snapshot'] });
      const refreshError =
        client.getQueryState(['snapshot'])?.status === 'error';
      setFeedback(
        `${change.enabled ? 'Enabled' : 'Paused'} monitoring for ${result.targets.length} service${result.targets.length === 1 ? '' : 's'}. ${change.enabled ? "Find enabled services in Monitored. Checks follow each service's configured schedule." : 'Find paused services in Inactive. Recorded evidence is retained.'}${refreshError ? ' The display could not refresh. Press Refresh to reload the saved state.' : ''}`,
      );
      setFeedbackError(refreshError);
    },
    onError: (error) => {
      setFeedbackError(true);
      setFeedback(
        error instanceof TypeError || error.name === 'TimeoutError'
          ? 'Could not confirm the change. Refresh to check the saved monitoring state.'
          : error.message,
      );
    },
  });
  const unavailable =
    mutation.isPending || query.isError || !data?.selection_editable;
  const monitored = all.filter((service) => service.enabled !== false).length;
  const count = groups.reduce(
    (total, project) => total + project.visible.length,
    0,
  );
  const allCollapsed =
    groups.length > 0 && groups.every((project) => collapsed.has(project.id));
  const selectedProject = projects.find((project) =>
    project.services.some((service) => service.id === selected?.id),
  );
  const current =
    selectedProject?.services.find((service) => service.id === selected?.id) ||
    selected;
  const selectTab = (next: 'monitored' | 'inactive') => {
    setTab(next);
    setPagination({ pageIndex: 0, pageSize: 10 });
    setSelected(null);
  };
  const resetPage = () => {
    setPagination({ pageIndex: 0, pageSize: 10 });
    setSelected(null);
  };
  const closeDetails = () => {
    setSelected(null);
    selectedTrigger.current?.focus();
  };
  const pendingFor = (scope: string, id: string) =>
    mutation.isPending &&
    mutation.variables?.scope === scope &&
    mutation.variables.id === id;
  const toggle = (service: Service) => {
    const projectPending =
      mutation.variables?.scope === 'projects' &&
      projects.some(
        (project) =>
          project.id === mutation.variables?.id &&
          project.services.some((item) => item.id === service.id),
      );
    const pending =
      mutation.isPending &&
      (pendingFor('services', service.id) || projectPending);
    return (
      <button
        className="monitoring-switch"
        role="switch"
        aria-checked={service.enabled !== false}
        aria-label={`Monitoring for ${service.name}`}
        aria-busy={pending || undefined}
        disabled={unavailable}
        onClick={() =>
          mutation.mutate({
            scope: 'services',
            id: service.id,
            enabled: service.enabled === false,
          })
        }
      >
        <span className="switch-track" aria-hidden="true">
          <span />
        </span>
        <span>
          {pending ? 'Saving…' : service.enabled !== false ? 'On' : 'Off'}
        </span>
      </button>
    );
  };
  if (!data) return null;
  return (
    <>
      <section className="panel projects-panel" aria-label="Project monitoring">
        <div className="service-tabs-bar">
          <div
            className="service-tabs"
            role="tablist"
            aria-label="Monitoring status"
          >
            {(['monitored', 'inactive'] as const).map((item, index) => (
              <button
                key={item}
                ref={(node) => {
                  tabRefs.current[index] = node;
                }}
                id={`tab-${item}`}
                role="tab"
                className="service-tab"
                aria-selected={tab === item}
                aria-controls="service-list"
                tabIndex={tab === item ? 0 : -1}
                onClick={() => selectTab(item)}
                onKeyDown={(event) => {
                  if (
                    ['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(
                      event.key,
                    )
                  ) {
                    event.preventDefault();
                    const next =
                      event.key === 'Home'
                        ? 0
                        : event.key === 'End'
                          ? 1
                          : tab === 'monitored'
                            ? 1
                            : 0;
                    selectTab(next ? 'inactive' : 'monitored');
                    tabRefs.current[next]?.focus();
                  }
                }}
              >
                {item === 'monitored' ? 'Monitored' : 'Inactive'}
                <span>
                  {number(
                    item === 'monitored' ? monitored : all.length - monitored,
                  )}
                </span>
              </button>
            ))}
          </div>
          <button
            className="text-button"
            disabled={!groups.length}
            onClick={() =>
              setCollapsed((previous) => {
                const next = new Set(previous);
                for (const project of groups) {
                  if (allCollapsed) next.delete(project.id);
                  else next.add(project.id);
                }
                return next;
              })
            }
          >
            {allCollapsed ? 'Expand all' : 'Collapse all'}
          </button>
        </div>
        <p className="service-tab-description">
          {tab === 'inactive'
            ? 'Monitoring is off for these services. Enable a service to resume checks.'
            : 'Services with monitoring enabled. Paused services are in Inactive.'}
        </p>
        <div className="filters service-filters">
          <label className="search">
            <Search size={16} aria-hidden="true" />
            <input
              type="search"
              aria-label="Search projects or services"
              placeholder="Search projects or services…"
              value={search}
              onChange={(event) => {
                setSearch(event.target.value);
                resetPage();
              }}
            />
          </label>
          <select
            aria-label="Filter by provider"
            value={provider}
            onChange={(event) => {
              setProvider(event.target.value);
              resetPage();
            }}
          >
            <option value="">All providers</option>
            {providers.map((item) => (
              <option key={item}>{item}</option>
            ))}
          </select>
          {tab === 'monitored' ? (
            <select
              aria-label="Filter by health"
              value={health}
              onChange={(event) => {
                setHealth(event.target.value);
                resetPage();
              }}
            >
              <option value="">All health</option>
              <option value="attention">Needs attention</option>
              <option value="healthy">Healthy</option>
              <option value="unknown">Unknown or stale</option>
            </select>
          ) : null}
        </div>
        {feedback ? (
          <p
            className={`selection-feedback ${feedbackError ? 'is-error' : ''}`}
            role={feedbackError ? 'alert' : 'status'}
          >
            {feedback}
          </p>
        ) : null}
        {query.isError || !data.selection_editable ? (
          <p className="service-tab-description">
            {query.isError
              ? 'Connection lost. Refresh the dashboard before changing monitoring.'
              : 'Monitoring controls are read-only for this workspace.'}
          </p>
        ) : null}
        <div
          className={`services-workspace ${current ? 'has-service-settings' : ''}`}
        >
          <div
            id="service-list"
            className="service-list"
            role="tabpanel"
            aria-labelledby={`tab-${tab}`}
          >
            {groups.length ? (
              <div className="table-wrap">
                <table className="service-management-table">
                  <thead>
                    <tr>
                      <th scope="col">Service</th>
                      <th scope="col">Provider</th>
                      <th scope="col">Health</th>
                      <th scope="col">Response</th>
                      <th scope="col">Last checked</th>
                      <th scope="col">Monitoring</th>
                    </tr>
                  </thead>
                  {table.getRowModel().rows.map(({ original: project }) => (
                    <Fragment key={project.id}>
                      <tbody className="project-group">
                        <tr className="project-heading-row">
                          <td colSpan={6}>
                            <div className="project-heading">
                              <button
                                className="project-disclosure"
                                aria-expanded={!collapsed.has(project.id)}
                                aria-controls={`project-services-${project.id}`}
                                onClick={() =>
                                  setCollapsed((previous) => {
                                    const next = new Set(previous);
                                    if (next.has(project.id))
                                      next.delete(project.id);
                                    else next.add(project.id);
                                    return next;
                                  })
                                }
                              >
                                <ChevronDown size={15} aria-hidden="true" />
                                <strong>{project.name}</strong>
                                <span className="project-count">
                                  {project.visible.length} {tab}
                                </span>
                                <span className="project-coverage">
                                  {
                                    project.services.filter(
                                      (service) => service.enabled !== false,
                                    ).length
                                  }{' '}
                                  of {project.services.length} monitored
                                </span>
                              </button>
                              <button
                                className="button button-compact monitoring-button"
                                aria-label={`${tab === 'monitored' ? 'Pause' : 'Enable'} all ${project.services.length} services in ${project.name}`}
                                aria-busy={
                                  pendingFor('projects', project.id) ||
                                  undefined
                                }
                                disabled={unavailable}
                                onClick={() =>
                                  mutation.mutate({
                                    scope: 'projects',
                                    id: project.id,
                                    enabled: tab === 'inactive',
                                  })
                                }
                              >
                                {pendingFor('projects', project.id)
                                  ? 'Saving…'
                                  : `${tab === 'monitored' ? 'Pause' : 'Enable'} all ${project.services.length}`}
                              </button>
                            </div>
                          </td>
                        </tr>
                      </tbody>
                      <tbody
                        id={`project-services-${project.id}`}
                        hidden={collapsed.has(project.id)}
                      >
                        {project.visible.map((service) => (
                          <tr
                            key={service.id}
                            id={`project-services-${project.id}-${service.id}`}
                            className={`managed-service ${service.enabled === false ? 'service-disabled' : ''} ${current?.id === service.id ? 'is-selected' : ''}`}
                          >
                            <td>
                              <button
                                className="service-button"
                                aria-label={`Configure ${service.name}`}
                                aria-expanded={current?.id === service.id}
                                aria-controls="service-settings"
                                onClick={(event) => {
                                  selectedTrigger.current = event.currentTarget;
                                  setSelected(service);
                                  setTimeout(
                                    () => detailsRef.current?.focus(),
                                    0,
                                  );
                                }}
                              >
                                <Avatar service={service} />
                                <span>
                                  <span className="service-name">
                                    {service.name}
                                  </span>
                                  <span className="service-sub">
                                    {service.environment}
                                  </span>
                                </span>
                              </button>
                            </td>
                            <td className="provider">{service.provider}</td>
                            <td>
                              <Badge
                                status={service.status}
                                label={
                                  service.enabled === false
                                    ? 'Paused'
                                    : undefined
                                }
                              />
                            </td>
                            <td
                              className={`latency numeric ${service.latency_ms != null && service.latency_ms > service.threshold_ms ? 'slow' : ''}`}
                            >
                              {service.latency_ms != null ? (
                                <>
                                  {number(service.latency_ms)}{' '}
                                  <span className="inline-unit">ms</span>
                                </>
                              ) : (
                                <span className="muted">No sample</span>
                              )}
                            </td>
                            <td
                              className="time-cell"
                              title={timestamp(service.checked, data.timezone)}
                            >
                              {service.enabled === false
                                ? 'Paused'
                                : age(service.checked)}
                            </td>
                            <td>
                              <div className="service-controls">
                                {toggle(service)}
                                <button
                                  className="service-settings-button icon-button"
                                  aria-label={`Open details for ${service.name}`}
                                  aria-expanded={current?.id === service.id}
                                  aria-controls="service-settings"
                                  onClick={(event) => {
                                    selectedTrigger.current =
                                      event.currentTarget;
                                    setSelected(service);
                                    setTimeout(
                                      () => detailsRef.current?.focus(),
                                      0,
                                    );
                                  }}
                                >
                                  <Settings2 size={16} />
                                </button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </Fragment>
                  ))}
                </table>
              </div>
            ) : (
              <>
                <Empty
                  title={
                    search || provider || (health && tab === 'monitored')
                      ? 'No matching services'
                      : tab === 'inactive'
                        ? 'No inactive services'
                        : 'No monitored services'
                  }
                >
                  {tab === 'inactive'
                    ? 'All configured services may already be monitored. Try another search or provider.'
                    : 'Try another search or filter, or enable a service from Inactive.'}
                </Empty>
                <div className="service-empty-action">
                  <button
                    className="button button-compact"
                    onClick={() => {
                      setSearch('');
                      setProvider('');
                      setHealth('');
                      resetPage();
                    }}
                  >
                    Clear filters
                  </button>
                  <button
                    className="button button-compact"
                    onClick={() =>
                      selectTab(tab === 'monitored' ? 'inactive' : 'monitored')
                    }
                  >
                    View {tab === 'monitored' ? 'Inactive' : 'Monitored'}
                  </button>
                </div>
              </>
            )}
            <div className="service-list-footer">
              <span>
                {number(count)} {tab} service{count === 1 ? '' : 's'} across{' '}
                {number(groups.length)} project{groups.length === 1 ? '' : 's'}
              </span>
              {pages > 1 ? (
                <div
                  className="service-pagination"
                  role="group"
                  aria-label="Project pages"
                >
                  <button
                    className="button button-compact"
                    aria-label="Previous project page"
                    disabled={!table.getCanPreviousPage()}
                    onClick={() => table.previousPage()}
                  >
                    Previous
                  </button>
                  <span className="numeric">
                    {effectivePagination.pageIndex + 1} / {pages}
                  </span>
                  <button
                    className="button button-compact"
                    aria-label="Next project page"
                    disabled={!table.getCanNextPage()}
                    onClick={() => table.nextPage()}
                  >
                    Next
                  </button>
                </div>
              ) : null}
            </div>
          </div>
          {current ? (
            <aside
              id="service-settings"
              ref={detailsRef}
              className="service-settings"
              aria-label="Service details"
              tabIndex={-1}
              onKeyDown={(event) => {
                if (event.key === 'Escape') closeDetails();
              }}
            >
              <div className="service-settings-heading">
                <h2>Service details</h2>
                <button
                  className="icon-button"
                  onClick={closeDetails}
                  aria-label="Close service details"
                >
                  <X size={18} />
                </button>
              </div>
              <h3 className="service-settings-title">{current.name}</h3>
              <p className="service-settings-project">
                {selectedProject?.name} · {current.environment}
              </p>
              <section className="service-setting-section">
                <div className="service-setting-label">
                  <h3>Monitoring</h3>
                  {toggle(current)}
                </div>
                <p>
                  {current.enabled !== false
                    ? "Checks run on this service's configured schedule."
                    : 'Checks, new alerts, and new automatic repairs are paused. Recorded evidence is retained.'}
                </p>
                {(current.enabled !== false) !== (tab === 'monitored') ? (
                  <button
                    className="text-link"
                    onClick={() =>
                      selectTab(
                        current.enabled !== false ? 'monitored' : 'inactive',
                      )
                    }
                  >
                    View in{' '}
                    {current.enabled !== false ? 'Monitored' : 'Inactive'}
                    <ArrowRight size={14} />
                  </button>
                ) : null}
              </section>
              <section className="service-setting-section">
                <h3>
                  {current.enabled !== false
                    ? 'Recorded health'
                    : 'Monitoring paused'}
                </h3>
                <div className="service-settings-status">
                  <Badge
                    status={current.status}
                    label={current.enabled === false ? 'Paused' : undefined}
                  />
                </div>
                <dl className="service-settings-facts">
                  <div>
                    <dt>Provider</dt>
                    <dd>{current.provider}</dd>
                  </div>
                  <div>
                    <dt>Environment</dt>
                    <dd>{current.environment}</dd>
                  </div>
                  <div>
                    <dt>Last checked</dt>
                    <dd>
                      {current.enabled === false
                        ? 'Paused'
                        : age(current.checked)}
                    </dd>
                  </div>
                  <div>
                    <dt>Response</dt>
                    <dd>
                      {current.latency_ms != null
                        ? `${number(current.latency_ms)} ms`
                        : 'No sample'}
                    </dd>
                  </div>
                  {current.enabled !== false ? (
                    <div>
                      <dt>Open incidents</dt>
                      <dd>
                        {current.open_incidents ??
                          data.incidents.filter(
                            (item) =>
                              item.target === current.id &&
                              item.status === 'open',
                          ).length}
                      </dd>
                    </div>
                  ) : null}
                </dl>
              </section>
              <section className="service-setting-section">
                <h3>Checks &amp; evidence</h3>
                <p>
                  {current.enabled !== false
                    ? 'Inspect recorded observations and related incidents.'
                    : 'Enable monitoring to collect current readings. Existing records stay saved.'}
                </p>
                <button
                  className="button button-compact"
                  onClick={() =>
                    setEvidence({
                      ...current,
                      checks: current.checks.length
                        ? current.checks
                        : selected?.checks || [],
                    })
                  }
                >
                  View recorded evidence
                  <ArrowRight size={14} />
                </button>
              </section>
            </aside>
          ) : null}
        </div>
      </section>
      {evidence ? (
        <ServiceEvidence
          service={evidence}
          snapshot={data}
          onClose={() => setEvidence(null)}
        />
      ) : null}
    </>
  );
}
