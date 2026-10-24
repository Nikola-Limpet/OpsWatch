import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  ChevronRight,
  Search,
} from 'lucide-react';
import { action, request, useSnapshot } from '../../api';
import {
  age,
  Badge,
  Empty,
  External,
  incidentState,
  jobPresentation,
  number,
  RepairProgress,
  timestamp,
} from '../../components';
import type { Incident, Repair, Snapshot } from '../../types';
import { IncidentAgent, investigationActive } from './incident-agent';

type IncidentGroup = { id: string; items: Incident[] };
function groupsFor(items: Incident[], data: Snapshot): IncidentGroup[] {
  const groups = new Map<string, IncidentGroup>();
  const services = new Map(
    data.services.map((service) => [service.id, service]),
  );
  for (const item of items) {
    const service = services.get(item.target);
    const key =
      item.kind === 'monitoring' &&
      item.status === 'open' &&
      typeof item.evidence.error === 'string' &&
      service?.account
        ? JSON.stringify([
            service.provider,
            service.account,
            item.evidence.error,
          ])
        : item.ref;
    if (!groups.has(key)) groups.set(key, { id: item.ref, items: [] });
    groups.get(key)?.items.push(item);
  }
  return [...groups.values()].sort((a, b) => {
    const rank = (group: IncidentGroup) =>
      (group.items[0].status === 'resolved' ? 10 : 0) +
      (group.items[0].kind === 'monitoring' ? 2 : 0) +
      (group.items[0].severity === 'critical' ? 0 : 1);
    return rank(a) - rank(b) || b.items[0].updated - a.items[0].updated;
  });
}
function originalEvidence(item: Incident) {
  const evidence = { ...item.evidence };
  delete evidence.recovery;
  return (
    Object.keys(evidence).length > 0 &&
    !(
      Object.keys(evidence).length === 1 &&
      Object.hasOwn(evidence, 'quiet_seconds')
    )
  );
}

export function IncidentsPage({ initialRef }: { initialRef?: string } = {}) {
  const query = useSnapshot();
  const data = query.data;
  const [filter, setFilter] = useState('open');
  const [search, setSearch] = useState('');
  const [selectedRef, setSelectedRef] = useState<string | null>(
    initialRef || null,
  );
  const [pinned, setPinned] = useState(Boolean(initialRef));
  const [mobileDetail, setMobileDetail] = useState(Boolean(initialRef));
  const [limit, setLimit] = useState(30);
  const latestJobs = useMemo(() => {
    const latest = new Map<string, Repair>();
    for (const job of data?.repairs || [])
      if (!latest.has(job.incident)) latest.set(job.incident, job);
    return latest;
  }, [data?.repairs]);
  const services = useMemo(
    () =>
      new Map((data?.services || []).map((service) => [service.id, service])),
    [data?.services],
  );
  const filtered = useMemo(
    () =>
      (data?.incidents || []).filter(
        (item) =>
          (filter === 'all' ||
            (filter === 'active'
              ? jobPresentation(latestJobs.get(item.ref)).active ||
                Boolean(
                  data?.investigations?.some(
                    (job) =>
                      job.incident === item.ref && investigationActive(job),
                  ),
                )
              : item.status === filter)) &&
          `${item.ref} ${item.title} ${services.get(item.target)?.name || item.target} ${item.kind}`
            .toLowerCase()
            .includes(search.trim().toLowerCase()),
      ),
    [
      data?.incidents,
      data?.investigations,
      filter,
      search,
      latestJobs,
      services,
    ],
  );
  const groups = useMemo(
    () => (data ? groupsFor(filtered, data) : []),
    [filtered, data],
  );
  const allGroups = useMemo(
    () => (data ? groupsFor(data.incidents, data) : []),
    [data],
  );
  const selected =
    selectedRef && (pinned || filtered.some((item) => item.ref === selectedRef))
      ? selectedRef
      : groups[0]?.items[0].ref;
  const known = [
    ...(data?.incidents || []),
    ...(data?.repair_incidents || []),
  ].find((item) => item.ref === selected);
  const older = useQuery({
    queryKey: ['incident', selected],
    queryFn: () =>
      request<Incident>(`/api/incidents/${encodeURIComponent(selected || '')}`),
    enabled: Boolean(data && selected && !known),
    staleTime: 30_000,
    retry: false,
  });
  const item = known || older.data;
  const select = (ref: string) => {
    setSelectedRef(ref);
    setPinned(!filtered.some((incident) => incident.ref === ref));
    setMobileDetail(true);
  };
  const changeFilter = (value: string) => {
    setFilter(value);
    setSelectedRef(null);
    setPinned(false);
    setMobileDetail(false);
    setLimit(30);
  };
  if (!data) return null;
  const openCount = data.incidents.filter(
    (incident) => incident.status === 'open',
  ).length;
  const historyCount = data.incidents.filter(
    (incident) => incident.status === 'resolved',
  ).length;
  const activeCount = data.incidents.filter(
    (incident) =>
      jobPresentation(latestJobs.get(incident.ref)).active ||
      Boolean(
        data.investigations?.some(
          (job) => job.incident === incident.ref && investigationActive(job),
        ),
      ),
  ).length;
  return (
    <section
      className={`incident-workspace ${mobileDetail ? 'show-detail' : ''}`}
      aria-label="Incident workspace"
    >
      <div className="workspace-toolbar">
        <div className="incident-tabs" role="group" aria-label="Incident views">
          {[
            { value: 'open', label: 'Open', count: openCount },
            { value: 'active', label: 'Agent active', count: activeCount },
            { value: 'all', label: 'History' },
          ].map((tab) => (
            <button
              key={tab.value}
              className="view-tab"
              aria-pressed={filter === tab.value}
              onClick={() => changeFilter(tab.value)}
            >
              {tab.label}
              {tab.count != null ? <span>{number(tab.count)}</span> : null}
            </button>
          ))}
        </div>
        <label className="search">
          <Search size={16} aria-hidden="true" />
          <input
            type="search"
            placeholder="Search incidents, apps or IDs…"
            aria-label="Search incidents"
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setSelectedRef(null);
              setPinned(false);
              setMobileDetail(false);
              setLimit(30);
            }}
          />
        </label>
        <select
          aria-label="Filter incident status"
          value={filter}
          onChange={(event) => changeFilter(event.target.value)}
        >
          <option value="open">Open incidents</option>
          <option value="active">Agent active</option>
          <option value="all">All incidents</option>
          <option value="resolved">Recovered or quiet</option>
        </select>
      </div>
      <p className="workspace-caption">
        {number(filtered.length)} matching incidents · {number(groups.length)}{' '}
        issues · All {number(openCount)} open incidents and{' '}
        {number(historyCount)} recent recovered or quiet records available
      </p>
      <div className="workspace-columns">
        <div className="incident-list-pane">
          <div className="incidents-list">
            {groups.slice(0, limit).map((group) => {
              const incident = group.items[0];
              const service = services.get(incident.target);
              const isSelected = group.items.some(
                (candidate) => candidate.ref === selected,
              );
              const job = latestJobs.get(incident.ref);
              const monitoring =
                incident.kind === 'monitoring' && incident.status === 'open';
              const label =
                group.items.length > 1
                  ? `${service?.provider || 'Provider'} collection`
                  : service?.name || incident.target;
              return (
                <button
                  className={`triage-row ${isSelected ? 'is-selected' : ''}`}
                  key={group.id}
                  aria-label={`View ${label} incident ${incident.ref}`}
                  aria-current={isSelected ? 'true' : undefined}
                  onClick={() =>
                    select(isSelected && selected ? selected : incident.ref)
                  }
                >
                  <span className={`issue-symbol ${incidentState(incident)}`}>
                    <AlertTriangle size={18} aria-hidden="true" />
                  </span>
                  <span className="triage-copy">
                    <strong>{label}</strong>
                    <span>
                      {group.items.length > 1
                        ? 'Production signals unavailable'
                        : incident.title}
                    </span>
                    <span className="triage-meta">
                      <span className="mono">
                        {incident.ref}
                        {group.items.length > 1
                          ? ` +${group.items.length - 1}`
                          : ''}
                      </span>
                      <span>
                        {group.items.length > 1
                          ? `${group.items.length} affected services`
                          : incident.kind}
                      </span>
                      <time title={timestamp(incident.updated, data.timezone)}>
                        {age(incident.updated)}
                      </time>
                    </span>
                  </span>
                  <span className="triage-status">
                    {job ? (
                      <Badge
                        status={jobPresentation(job).tone}
                        label={jobPresentation(job).label}
                      />
                    ) : (
                      <Badge
                        status={
                          monitoring ? 'warning' : incidentState(incident)
                        }
                        label={monitoring ? 'Monitoring gap' : undefined}
                      />
                    )}
                  </span>
                  <ChevronRight
                    size={16}
                    className="row-arrow"
                    aria-hidden="true"
                  />
                </button>
              );
            })}
            {!groups.length ? (
              <Empty title="No matching incidents">
                Change your search or filter to see other recorded evidence.
              </Empty>
            ) : null}
            {groups.length > limit ? (
              <div className="load-more">
                <button
                  className="button"
                  onClick={() => setLimit((value) => value + 30)}
                >
                  Show more issues
                </button>
                <span>
                  {limit} of {groups.length} shown
                </span>
              </div>
            ) : null}
          </div>
        </div>
        <aside
          className="incident-detail"
          aria-label="Selected incident"
          tabIndex={-1}
        >
          {item ? (
            <>
              <button
                className="back-to-incidents button button-compact"
                onClick={() => setMobileDetail(false)}
              >
                <ArrowLeft size={14} />
                Back to incidents
              </button>
              <IncidentDetail
                key={item.ref}
                item={item}
                data={data}
                failed={query.isError}
                group={allGroups.find((group) =>
                  group.items.some((candidate) => candidate.ref === item.ref),
                )}
                onSelect={select}
              />
            </>
          ) : older.isFetching ? (
            <div className="loading" role="status">
              Loading recorded incident…
            </div>
          ) : older.isError ? (
            <div className="notice error" role="alert">
              {older.error.message}
              <button
                className="text-link"
                onClick={() => void older.refetch()}
              >
                Try again
              </button>
            </div>
          ) : (
            <Empty title="A clear view of the issue">
              Select an incident to inspect its evidence, next step, and repair
              activity.
            </Empty>
          )}
        </aside>
      </div>
    </section>
  );
}

function IncidentDetail({
  item,
  data,
  failed,
  group,
  onSelect,
}: {
  item: Incident;
  data: Snapshot;
  failed: boolean;
  group?: IncidentGroup;
  onSelect(ref: string): void;
}) {
  const service = data.services.find(
    (candidate) => candidate.id === item.target,
  );
  const quiet = incidentState(item) === 'quiet';
  const evidence = { ...item.evidence };
  const recovery = evidence.recovery;
  delete evidence.recovery;
  const lead =
    evidence.error || evidence.message || evidence.exception || evidence.reason;
  return (
    <>
      <div className="detail-topline">
        <span>{service?.name || item.target}</span>
        <span className="mono incident-ref">{item.ref}</span>
        <Badge
          status={incidentState(item)}
          label={
            item.kind === 'monitoring' && item.status === 'open'
              ? 'Monitoring gap'
              : undefined
          }
        />
      </div>
      <h2 className="incident-title">{item.title}</h2>
      <p className="detail-intro">
        {item.kind === 'monitoring'
          ? 'The monitor could not verify all production signals. This does not establish that the application is down.'
          : quiet
            ? 'Matching events have stopped. Production recovery and a code fix have not been verified.'
            : item.status === 'resolved'
              ? 'Recovery was confirmed by repeated checks. Review the recorded observations below.'
              : 'Review the recorded evidence and next step before starting a repair.'}
      </p>
      <dl className="incident-facts">
        <div>
          <dt>First seen</dt>
          <dd>{timestamp(item.opened, data.timezone)}</dd>
        </div>
        <div>
          <dt>Last observed</dt>
          <dd>{timestamp(item.updated, data.timezone)}</dd>
        </div>
        <div>
          <dt>Observations</dt>
          <dd>{number(item.occurrences || 1)}</dd>
        </div>
        <div>
          <dt>Source</dt>
          <dd>
            {service?.provider || item.kind} ·{' '}
            {service?.environment || 'production'}
          </dd>
        </div>
      </dl>
      {group && group.items.length > 1 ? (
        <section className="detail-section">
          <h3>Affected services</h3>
          <p>
            These incidents share a provider account and the same collection
            error.
          </p>
          <div className="affected-services">
            {group.items.map((incident) => (
              <button
                key={incident.ref}
                className={`affected-service ${incident.ref === item.ref ? 'is-selected' : ''}`}
                onClick={() => onSelect(incident.ref)}
              >
                <span>
                  {data.services.find(
                    (candidate) => candidate.id === incident.target,
                  )?.name || incident.target}
                </span>{' '}
                <span className="mono">{incident.ref}</span>
              </button>
            ))}
          </div>
        </section>
      ) : null}
      {item.repair?.available ||
      data.repairs.some((job) => job.incident === item.ref) ? (
        <RepairDetails item={item} data={data} failed={failed} />
      ) : null}
      <IncidentAgent
        item={item}
        data={data}
        failed={failed}
        hasEvidence={Boolean(originalEvidence(item))}
      />
      <section className="detail-section evidence-section">
        <div className="section-heading">
          <h3>{recovery ? 'Failure evidence' : 'Recorded evidence'}</h3>
          <span className="muted">{item.kind}</span>
        </div>
        {lead && typeof lead === 'string' ? (
          <p className="evidence-lead">{lead}</p>
        ) : null}
        {!originalEvidence(item) ? (
          <p className="evidence-warning">
            The original failure was not retained for this older incident.
            Inspect provider logs before requesting a repair.
          </p>
        ) : null}
        <pre>{JSON.stringify(evidence, null, 2)}</pre>
        {recovery ? (
          <details>
            <summary>Recovery observation</summary>
            <pre>{JSON.stringify(recovery, null, 2)}</pre>
          </details>
        ) : null}
      </section>
      <section className="detail-section">
        <h3>Recommended next step</h3>
        <p>
          {(item.next_step || 'Review the recorded evidence.')
            .replace(
              /(?:Use|Run|Send)\s+\/(?:diagnose|fix|incident)\b(?:\s+OW-[0-9]+)?/gi,
              'Start an agent investigation',
            )
            .trim()}
        </p>
        {item.action && item.action !== 'No action taken' ? (
          <p>Recorded action: {item.action}</p>
        ) : null}
      </section>
      <div className="detail-links">
        {(item.links || []).map((link) => (
          <External key={link.url} href={link.url}>
            {link.text}
          </External>
        ))}
      </div>
    </>
  );
}

function RepairDetails({
  item,
  data,
  failed,
}: {
  item: Incident;
  data: Snapshot;
  failed: boolean;
}) {
  const client = useQueryClient();
  const job = data.repairs.find((candidate) => candidate.incident === item.ref);
  const status = jobPresentation(job);
  const fresh = !failed && data.monitor.status === 'running';
  const available =
    item.status === 'open' &&
    item.repair?.available &&
    fresh &&
    originalEvidence(item) &&
    (!job || ['blocked', 'cancelled'].includes(job.status));
  const mutation = useMutation({
    mutationFn: () =>
      action<Repair>(`/api/incidents/${encodeURIComponent(item.ref)}/fix`, {}),
    onSuccess: (result) => {
      client.setQueryData<Snapshot>(['snapshot'], (previous) =>
        previous
          ? {
              ...previous,
              repairs: [
                result,
                ...previous.repairs.filter(
                  (candidate) => candidate.id !== result.id,
                ),
              ],
            }
          : previous,
      );
    },
  });
  const message = job
    ? `${job.id} · ${status.label}. ${job.summary || 'The worker will record its progress here.'}`
    : !fresh
      ? 'Fresh monitor readings are required before starting a new fix. Refresh after monitoring reconnects.'
      : !originalEvidence(item)
        ? 'The original failure evidence is unavailable. Wait for a new recorded failure before requesting a repair.'
        : item.status !== 'open'
          ? 'This incident is no longer open. Review its recorded outcome before requesting further work.'
          : item.repair?.available
            ? 'Investigate in an isolated coding workspace, run checks, and prepare a draft PR for your review.'
            : item.repair?.reason ||
              'No verified repair mapping is available for this incident.';
  return (
    <section className="detail-section repair-section">
      <div className="section-heading">
        <h3>{job ? 'Repair activity' : 'AI-assisted repair'}</h3>
        {job ? <Badge status={status.tone} label={status.label} /> : null}
      </div>
      <p role="status">{message}</p>
      {job ? <RepairProgress job={job} /> : null}
      <div className="detail-links">
        {available ? (
          <button
            className="button button-primary"
            disabled={mutation.isPending}
            aria-busy={mutation.isPending || undefined}
            onClick={() => {
              if (!mutation.isPending) mutation.mutate();
            }}
          >
            {mutation.isPending
              ? 'Starting fix…'
              : job
                ? 'Retry fix'
                : 'Start fix'}
            <ArrowRight size={14} aria-hidden="true" />
          </button>
        ) : null}
        {job ? (
          <External className="button button-primary" href={job.pr_url}>
            Review draft PR
          </External>
        ) : null}
        {job ? (
          <a className="button" href="/agents">
            All agent work
            <ArrowRight size={14} />
          </a>
        ) : null}
      </div>
      {mutation.error ? (
        <p className="repair-error" role="alert">
          {mutation.error instanceof TypeError ||
          mutation.error.name === 'TimeoutError'
            ? 'Could not confirm the repair request. Refresh to check its status before trying again.'
            : mutation.error.message}
        </p>
      ) : null}
      {available || status.active ? (
        <p className="repair-note">
          Up to {data.automation?.daily_limit || 4} attempts per day. Fixes open
          a draft PR; they do not merge or deploy.
        </p>
      ) : null}
    </section>
  );
}
