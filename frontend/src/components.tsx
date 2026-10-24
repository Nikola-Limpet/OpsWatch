import { useEffect, useId, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { ExternalLink, X } from 'lucide-react';
import type { Health, Incident, Repair, Service, Snapshot } from './types';

export function number(value: number, digits = 0) {
  return new Intl.NumberFormat(undefined, {
    maximumFractionDigits: digits,
  }).format(value);
}
export function age(value: number | null | undefined) {
  if (!value) return 'Not checked';
  const elapsed = Math.max(0, Date.now() / 1000 - value);
  if (elapsed < 10) return 'Just now';
  if (elapsed < 60) return `${Math.floor(elapsed)}s ago`;
  if (elapsed < 3600) return `${Math.floor(elapsed / 60)}m ago`;
  if (elapsed < 86400) return `${Math.floor(elapsed / 3600)}h ago`;
  return `${Math.floor(elapsed / 86400)}d ago`;
}
export function timestamp(value: number | null | undefined, timezone?: string) {
  if (!value) return 'No recorded check';
  try {
    return new Intl.DateTimeFormat(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      timeZone: timezone,
    }).format(new Date(value * 1000));
  } catch {
    return new Date(value * 1000).toLocaleString();
  }
}
const labels: Record<Health, string> = {
  healthy: 'Healthy',
  warning: 'Warning',
  critical: 'Critical',
  unknown: 'Unknown',
  stale: 'Stale',
  resolved: 'Recovered',
  quiet: 'Quiet',
  disabled: 'Disabled',
};
export function Badge({ status, label }: { status?: string; label?: string }) {
  const known =
    status && Object.hasOwn(labels, status) ? (status as Health) : 'unknown';
  return (
    <span className={`badge ${known}`}>
      <span className={`dot ${known}`} />
      {label || labels[known]}
    </span>
  );
}
export function Empty({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      <p>{children}</p>
    </div>
  );
}
export function safeURL(value?: string | null) {
  try {
    const url = new URL(value || '');
    return ['http:', 'https:'].includes(url.protocol) &&
      !url.username &&
      !url.password
      ? url.href
      : null;
  } catch {
    return null;
  }
}
export function External({
  href,
  children,
  className = 'button button-compact',
}: {
  href?: string | null;
  children: ReactNode;
  className?: string;
}) {
  const safe = safeURL(href);
  return safe ? (
    <a
      className={className}
      href={safe}
      target="_blank"
      rel="noopener noreferrer"
    >
      {children}
      <ExternalLink size={13} aria-hidden="true" />
    </a>
  ) : null;
}
export function Avatar({ service }: { service: Service }) {
  const [failed, setFailed] = useState(false);
  const local =
    service.favicon?.startsWith('/favicons/') &&
    !service.favicon.startsWith('//');
  return (
    <span className="service-icon" aria-hidden="true">
      <span>{service.name.slice(0, 2).toUpperCase()}</span>
      {local && !failed ? (
        <img
          src={service.favicon || undefined}
          alt=""
          loading="lazy"
          decoding="async"
          onError={() => setFailed(true)}
        />
      ) : null}
    </span>
  );
}
export function incidentState(item: Incident): Health {
  return item.status === 'resolved'
    ? ['application error', 'security'].includes(item.kind)
      ? 'quiet'
      : 'resolved'
    : item.severity;
}
export function jobPresentation(job?: Repair) {
  const states: Record<string, [string, Health, number, boolean]> = {
    queued: ['Queued', 'unknown', 0, true],
    preparing: ['Preparing workspace', 'unknown', 0, true],
    coding: ['Investigating & fixing', 'unknown', 1, true],
    validating: ['Running checks', 'unknown', 2, true],
    publishing: ['Preparing draft PR', 'unknown', 2, true],
    pr_open: ['Draft PR ready', 'healthy', 3, false],
    blocked: ['Fix blocked', 'warning', -1, false],
    interrupted: ['Needs manual review', 'warning', -1, false],
    cancelled: ['Cancelled', 'unknown', -1, false],
    cancel_requested: ['Stopping fix', 'warning', -1, true],
  };
  const [label, tone, phase, active] = states[job?.status || ''] || [
    'Unknown status',
    'unknown',
    -1,
    false,
  ];
  return { label, tone, phase, active };
}
export function RepairProgress({ job }: { job: Repair }) {
  const status = jobPresentation(job);
  return (
    <ol className="repair-progress" aria-label="Repair progress">
      {['Workspace', 'Investigate & fix', 'Validate', 'Draft PR'].map(
        (label, index) => (
          <li
            key={label}
            className={
              index < status.phase
                ? 'is-complete'
                : index === status.phase
                  ? 'is-current'
                  : ''
            }
            aria-current={index === status.phase ? 'step' : undefined}
          >
            <span className="step-number">
              {index < status.phase ? '✓' : index + 1}
            </span>
            <span>{label}</span>
          </li>
        ),
      )}
    </ol>
  );
}
export function Dialog({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose(): void;
  children: ReactNode;
}) {
  const id = useId();
  const ref = useRef<HTMLDialogElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const dialog = ref.current;
    if (dialog && !dialog.open) {
      if (dialog.showModal) dialog.showModal();
      else dialog.setAttribute('open', '');
    }
    close.current?.focus();
    return () => previous?.focus();
  }, []);
  return (
    <dialog
      ref={ref}
      aria-labelledby={id}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="dialog-top">
        <span className="eyebrow">RECORDED EVIDENCE</span>
        <button
          ref={close}
          className="icon-button"
          onClick={onClose}
          aria-label="Close details"
        >
          <X size={19} />
        </button>
      </div>
      <h2 id={id} className="dialog-title">
        {title}
      </h2>
      {children}
    </dialog>
  );
}
export function ServiceEvidence({
  service,
  snapshot,
  onClose,
}: {
  service: Service;
  snapshot: Snapshot;
  onClose(): void;
}) {
  const incidents = snapshot.incidents.filter(
    (item) => item.target === service.id && item.status === 'open',
  );
  return (
    <Dialog title={service.name} onClose={onClose}>
      <div className="evidence-service-heading">
        <Badge status={service.status} />
        <p className="muted">
          {service.provider} · {service.environment} · Last checked{' '}
          {timestamp(service.checked, snapshot.timezone)}
        </p>
        <External href={service.url}>Open app</External>
      </div>
      {incidents.length ? (
        <section className="detail-section">
          <h3>Open incidents</h3>
          {incidents.map((item) => (
            <a
              key={item.ref}
              className="evidence-incident-link"
              href={`/incidents?ref=${encodeURIComponent(item.ref)}`}
            >
              <span className="mono">{item.ref}</span>{' '}
              {item.title}
              <ExternalLink size={14} />
            </a>
          ))}
        </section>
      ) : null}
      <section className="detail-section">
        <h3>Recorded checks</h3>
        {service.checks.map((check, index) => (
          <article
            className="check-item"
            key={`${check.kind}-${check.key}-${index}`}
          >
            <div className="check-title">
              <h3>{check.title}</h3>
              <Badge
                status={
                  check.stale || snapshot.monitor.status !== 'running'
                    ? 'stale'
                    : check.healthy === null
                      ? 'unknown'
                      : check.healthy
                        ? 'healthy'
                        : 'warning'
                }
              />
            </div>
            <details>
              <summary>
                {check.kind} · {timestamp(check.checked, snapshot.timezone)} ·
                Show evidence
              </summary>
              <pre>{JSON.stringify(check.evidence, null, 2)}</pre>
            </details>
          </article>
        ))}
        {!service.checks.length ? (
          <Empty title="No recorded checks">
            The monitor has no checks for this service in the current snapshot.
          </Empty>
        ) : null}
      </section>
    </Dialog>
  );
}
