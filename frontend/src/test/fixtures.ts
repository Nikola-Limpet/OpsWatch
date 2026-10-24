import type { Snapshot, Service } from '../types';

const base: Service = {
  id: 'api',
  name: 'Store API',
  provider: 'Railway',
  account: 'personal',
  environment: 'production',
  status: 'warning',
  checked: 1_793_000_000,
  latency_ms: 890,
  threshold_ms: 500,
  enabled: true,
  checks: [
    {
      kind: 'http',
      title: 'HTTP response slow',
      checked: 1_793_000_000,
      healthy: false,
      evidence: { latency_ms: 890 },
    },
  ],
};

export function snapshotFixture(): Snapshot {
  const api = { ...base };
  const web: Service = {
    ...base,
    id: 'web',
    name: 'Store web',
    provider: 'HTTP',
    status: 'healthy',
    latency_ms: 80,
  };
  const db: Service = {
    ...base,
    id: 'db',
    name: 'Store database',
    provider: 'Neon',
    enabled: false,
    status: 'disabled',
    checked: null,
    latency_ms: null,
    checks: [],
  };
  const demo: Service = {
    ...base,
    id: 'demo',
    name: 'Demo API',
    enabled: false,
    status: 'disabled',
    latency_ms: null,
    checks: [],
  };
  return {
    generated_at: 1_793_000_000,
    timezone: 'UTC',
    monitor: { status: 'running', last_cycle: 1_793_000_000 },
    repair_worker: { status: 'running' },
    summary: {
      total: 2,
      healthy: 1,
      attention: 1,
      unknown: 0,
      open_incidents: 1,
    },
    services: [api, web],
    projects: [
      {
        id: 'store',
        name: 'Store',
        enabled_count: 2,
        services: [api, web, db],
      },
      { id: 'demo', name: 'Demo', enabled_count: 0, services: [demo] },
    ],
    selection_editable: true,
    incidents: [
      {
        ref: 'OW-0001',
        target: 'api',
        title: 'Slow API response',
        kind: 'availability',
        status: 'open',
        severity: 'warning',
        opened: 1_793_000_000,
        updated: 1_793_000_000,
        occurrences: 3,
        evidence: { latency_ms: 890, error: '<script>unsafe</script>' },
        next_step: 'Inspect recorded HTTP evidence.',
        repair: { available: true },
        links: [{ text: 'Unsafe link', url: 'javascript:alert(1)' }],
      },
    ],
    repairs: [],
    queries: [],
    automation: { daily_limit: 4 },
  };
}
