import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowRight, Bot } from 'lucide-react';
import { action } from '../../api';
import { Badge } from '../../components';
import type { Incident, Investigation, Snapshot } from '../../types';

export function investigationActive(job?: Investigation) {
  return Boolean(job && ['queued', 'running'].includes(job.status));
}
export function InvestigationStatus({ job }: { job: Investigation }) {
  const labels: Record<string, string> = {
    queued: 'Queued',
    running: 'Investigating',
    succeeded: 'Findings ready',
    failed: 'Needs attention',
    interrupted: 'Interrupted',
    cancelled: 'Cancelled',
  };
  return (
    <Badge
      status={
        job.status === 'succeeded'
          ? 'healthy'
          : investigationActive(job)
            ? 'unknown'
            : 'warning'
      }
      label={labels[job.status] || job.status}
    />
  );
}
export function InvestigationLink({ job }: { job: Investigation }) {
  return (
    <a
      className="button button-compact"
      href={`/studio?session=${encodeURIComponent(job.session_id)}`}
    >
      Open investigation <ArrowRight size={14} aria-hidden="true" />
    </a>
  );
}
export function IncidentAgent({
  item,
  data,
  failed,
  hasEvidence,
}: {
  item: Incident;
  data: Snapshot;
  failed: boolean;
  hasEvidence: boolean;
}) {
  const client = useQueryClient();
  const job = data.investigations?.find(
    (candidate) => candidate.incident === item.ref,
  );
  const active = investigationActive(job);
  const start = useMutation({
    retry: false,
    mutationFn: () =>
      action<{
        session: { id: string };
        job: { id: string; status: string; updated: number };
      }>('/api/studio/actions', {
        action: 'incident_investigate',
        payload: { incident_ref: item.ref },
      }),
    onSuccess: (result) => {
      const queued: Investigation = {
        id: result.job.id,
        incident: item.ref,
        session_id: result.session.id,
        status: result.job.status,
        updated: result.job.updated,
        summary: '',
        title: item.title,
      };
      client.setQueryData<Snapshot>(['snapshot'], (previous) =>
        previous
          ? {
              ...previous,
              investigations: [
                queued,
                ...(previous.investigations || []).filter(
                  (candidate) => candidate.id !== queued.id,
                ),
              ],
            }
          : previous,
      );
      void client.invalidateQueries({ queryKey: ['studio'] });
    },
  });
  return (
    <section className="detail-section" aria-label="Agent investigation">
      <div className="section-heading">
        <h3>Agent investigation</h3>
        {job ? <InvestigationStatus job={job} /> : null}
      </div>
      <p>
        {job?.summary ||
          (job?.status === 'succeeded'
            ? 'Investigation complete. Open the saved findings below.'
            : active
            ? 'The local agent will save its findings here and in Agent Studio. You can leave this page while it works.'
            : 'Start an agent with this incident’s evidence. It will investigate the cause and save a proposed fix and verification steps in the dashboard.')}
      </p>
      {data.monitor.status !== 'running' ? (
        <p className="detail-note">
          The monitor is not current. The agent can review recorded evidence but
          cannot confirm present production health.
        </p>
      ) : null}
      {!hasEvidence ? (
        <p className="detail-note">
          The original failure evidence is unavailable. A new observation is
          needed before investigation.
        </p>
      ) : null}
      <div className="detail-links">
        {!active && hasEvidence ? (
          <button
            className={`button ${item.repair?.available ? '' : 'button-primary'}`}
            disabled={start.isPending || failed}
            aria-busy={start.isPending || undefined}
            onClick={() => {
              if (!start.isPending) start.mutate();
            }}
          >
            <Bot size={14} aria-hidden="true" />
            {start.isPending
              ? 'Starting agent…'
              : job
                ? 'Investigate again'
                : 'Investigate with agent'}
          </button>
        ) : null}
        {job ? <InvestigationLink job={job} /> : null}
        {job ? (
          <a className="button button-compact" href="/agents">
            All agent work <ArrowRight size={14} aria-hidden="true" />
          </a>
        ) : null}
      </div>
      {start.error ? (
        <p className="repair-error" role="alert">
          {start.error.message} Refresh to check for saved agent work before
          retrying.
        </p>
      ) : null}
      <p className="detail-note">
        Investigations save findings. Mapped code repairs run checks and prepare
        a draft PR for review.
      </p>
    </section>
  );
}
