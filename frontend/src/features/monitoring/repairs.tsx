import { useState } from 'react';
import { ArrowRight } from 'lucide-react';
import { useSnapshot } from '../../api';
import {
  age,
  Badge,
  Empty,
  External,
  jobPresentation,
  RepairProgress,
} from '../../components';

export function RepairsPage() {
  const query = useSnapshot();
  const data = query.data;
  const [filter, setFilter] = useState('all');
  if (!data) return null;
  const jobs = data.repairs.filter(
    (job) =>
      filter === 'all' ||
      (filter === 'active'
        ? jobPresentation(job).active
        : filter === 'review'
          ? job.status === 'pr_open'
          : filter === 'blocked'
            ? ['blocked', 'interrupted'].includes(job.status)
            : ['pr_open', 'blocked', 'interrupted', 'cancelled'].includes(
                job.status,
              )),
  );
  const fresh = !query.isError && data.monitor.status === 'running';
  const workerOnline = fresh && data.repair_worker?.status === 'running';
  const incidents = [...data.incidents, ...(data.repair_incidents || [])];
  return (
    <section className="panel agent-workspace">
      <div className="panel-header">
        <div>
          <h2>Repair queue &amp; outcomes</h2>
          <p>
            {jobs.length} matching attempts ·{' '}
            {workerOnline
              ? 'Worker heartbeat is current'
              : 'Worker is not confirmed online'}{' '}
            · Active attempts and the latest 100 outcomes
          </p>
        </div>
        <select
          aria-label="Filter agent work"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        >
          <option value="all">All work</option>
          <option value="active">Active</option>
          <option value="review">Draft PRs to review</option>
          <option value="blocked">Blocked or interrupted</option>
          <option value="completed">Finished attempts</option>
        </select>
      </div>
      {jobs.map((job) => {
        const status = jobPresentation(job);
        const incident = incidents.find((item) => item.ref === job.incident);
        return (
          <article className="agent-row" key={job.id}>
            <div className="agent-row-heading">
              <div>
                <span className="agent-service">
                  {job.service ||
                    data.services.find(
                      (service) => service.id === incident?.target,
                    )?.name ||
                    incident?.target ||
                    'Recorded service'}
                </span>
                <h3>{job.title || incident?.title || job.incident}</h3>
              </div>
              <Badge status={status.tone} label={status.label} />
            </div>
            <div className="agent-row-meta">
              <span className="mono">{job.id}</span>
              <span className="mono">{job.incident}</span>
              <span>Updated {age(job.updated)}</span>
            </div>
            <p className="agent-summary">
              {job.summary || 'The worker has not recorded an outcome yet.'}
            </p>
            <RepairProgress job={job} />
            <div className="agent-actions">
              <a
                className="button button-compact"
                href={`/incidents?ref=${encodeURIComponent(job.incident)}`}
              >
                Inspect incident
                <ArrowRight size={14} />
              </a>
              <External
                className="button button-primary button-compact"
                href={job.pr_url}
              >
                Review draft PR
              </External>
            </div>
          </article>
        );
      })}
      {!jobs.length ? (
        <Empty title="No matching agent work">
          Start a fix from a mapped incident to investigate in an isolated
          workspace and prepare a draft PR. Existing attempts appear here when
          the worker records them.
        </Empty>
      ) : null}
    </section>
  );
}
