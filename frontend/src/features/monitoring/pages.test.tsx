import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { snapshotFixture } from '../../test/fixtures';
import type { Snapshot } from '../../types';
import {
  ProjectsPage,
  IncidentsPage,
  QueriesPage,
  OverviewPage,
  RepairsPage,
} from './pages';

function mount(page: React.ReactNode, data = snapshotFixture()) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity },
      mutations: { retry: false },
    },
  });
  client.setQueryData(['snapshot'], data);
  return {
    ...render(
      <QueryClientProvider client={client}>{page}</QueryClientProvider>,
    ),
    client,
  };
}
afterEach(() => vi.unstubAllGlobals());

describe('React project monitoring workspace', () => {
  it('collapses project rows without changing monitored totals', async () => {
    const user = userEvent.setup();
    mount(<ProjectsPage />);
    await user.click(screen.getByRole('button', { name: 'Collapse all' }));
    expect(
      screen.queryByRole('button', { name: 'Configure Store API' }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Monitored/ })).toHaveTextContent(
      '2',
    );
    expect(
      screen.getByText('2 monitored services across 1 project'),
    ).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Expand all' }));
    expect(
      screen.getByRole('button', { name: 'Configure Store API' }),
    ).toBeVisible();
  });
  it('keeps paused services in Inactive and supports keyboard tab selection', async () => {
    const user = userEvent.setup();
    mount(<ProjectsPage />);
    expect(
      screen.getByRole('button', { name: 'Configure Store API' }),
    ).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'Configure Store database' }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: /Inactive/ }));
    expect(
      screen.getByRole('button', { name: 'Configure Store database' }),
    ).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Configure Demo API' }),
    ).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'Configure Store API' }),
    ).not.toBeInTheDocument();
    await user.keyboard('{ArrowLeft}');
    expect(screen.getByRole('tab', { name: /Monitored/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(
      screen.getByRole('button', { name: 'Configure Store API' }),
    ).toBeVisible();
  });

  it('composes project-name, provider and health filters and pauses the whole project', async () => {
    const user = userEvent.setup();
    const data = snapshotFixture();
    const calls: { path: string; body: unknown; headers: Headers }[] = [];
    document.head.innerHTML =
      '<meta name="opswatch-csrf" content="test-token" />';
    vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
      if (init?.method === 'POST') {
        calls.push({
          path,
          body: JSON.parse(String(init.body)),
          headers: new Headers(init.headers),
        });
        const saved = structuredClone(data);
        saved.projects[0].services.forEach((s) => {
          s.enabled = false;
        });
        data.projects = saved.projects;
        return new Response(JSON.stringify({ targets: ['api', 'web', 'db'] }));
      }
      return new Response(JSON.stringify(data));
    });
    mount(<ProjectsPage />, data);
    await user.type(
      screen.getByRole('searchbox', { name: 'Search projects or services' }),
      'Store',
    );
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'Filter by provider' }),
      'Railway',
    );
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'Filter by health' }),
      'attention',
    );
    expect(
      screen.getByRole('button', { name: 'Configure Store API' }),
    ).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'Configure Store web' }),
    ).not.toBeInTheDocument();
    await user.click(
      screen.getByRole('button', { name: 'Pause all 3 services in Store' }),
    );
    await screen.findByText(/Paused monitoring for 3 services/);
    expect(calls).toHaveLength(1);
    expect(calls[0].path).toBe('/api/projects/store/monitoring');
    expect(calls[0].body).toEqual({ enabled: false });
    expect(calls[0].headers.get('X-OpsWatch-CSRF')).toBe('test-token');
    expect(calls[0].headers.get('X-OpsWatch-Request-ID')).toMatch(
      /^[\da-f-]{36}$/,
    );
  });

  it('preserves service details after pausing and keeps saved evidence readable', async () => {
    const user = userEvent.setup();
    const data = snapshotFixture();
    document.head.innerHTML =
      '<meta name="opswatch-csrf" content="test-token" />';
    vi.stubGlobal('fetch', async (_path: string, init?: RequestInit) => {
      if (init?.method === 'POST') {
        data.projects[0].services[0].enabled = false;
        return new Response(JSON.stringify({ targets: ['api'] }));
      }
      return new Response(JSON.stringify(data));
    });
    mount(<ProjectsPage />, data);
    await user.click(
      screen.getByRole('button', { name: 'Configure Store API' }),
    );
    const details = screen.getByRole('complementary', {
      name: 'Service details',
    });
    await user.click(
      within(details).getByRole('switch', { name: 'Monitoring for Store API' }),
    );
    await screen.findByText(/Paused monitoring for 1 service/);
    expect(
      within(details).getByRole('heading', { name: 'Store API' }),
    ).toBeVisible();
    expect(within(details).getByRole('switch')).toHaveAttribute(
      'aria-checked',
      'false',
    );
    await user.click(
      within(details).getByRole('button', { name: 'View recorded evidence' }),
    );
    const dialog = screen.getByRole('dialog', { name: 'Store API' });
    await user.click(within(dialog).getByText(/Show evidence/));
    expect(within(dialog).getByText(/"latency_ms": 890/)).toBeVisible();
  });

  it('pages projects while search still finds services on a later page', async () => {
    const user = userEvent.setup();
    const data = snapshotFixture();
    data.projects = Array.from({ length: 12 }, (_, index) => ({
      id: `p${index}`,
      name: `Project ${String(index).padStart(2, '0')}`,
      enabled_count: 1,
      services: [
        { ...data.services[1], id: `s${index}`, name: `Service ${index}` },
      ],
    }));
    mount(<ProjectsPage />, data);
    expect(
      screen.queryByRole('button', { name: 'Configure Service 10' }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Next project page' }));
    expect(
      screen.getByRole('button', { name: 'Configure Service 10' }),
    ).toBeVisible();
    await user.type(screen.getByRole('searchbox'), 'Project 11');
    expect(
      screen.getByRole('button', { name: 'Configure Service 11' }),
    ).toBeVisible();
    expect(
      screen.getByText(/1 monitored service across 1 project/),
    ).toBeVisible();
  });
});

describe('incident evidence and recorded queries', () => {
  it('groups collection errors only when the provider account is shared', async () => {
    const user = userEvent.setup();
    const data = snapshotFixture();
    data.services[1] = { ...data.services[0], id: 'web', name: 'Store web' };
    data.services.push({
      ...data.services[0],
      id: 'separate',
      name: 'Separate account',
      account: 'team-account',
    });
    data.incidents = [
      {
        ...data.incidents[0],
        kind: 'monitoring',
        evidence: { error: 'Provider token expired' },
        repair: { available: false, reason: 'Review provider credentials.' },
      },
      {
        ...data.incidents[0],
        ref: 'OW-0002',
        target: 'web',
        kind: 'monitoring',
        evidence: { error: 'Provider token expired' },
      },
      {
        ...data.incidents[0],
        ref: 'OW-0003',
        target: 'separate',
        kind: 'monitoring',
        evidence: { error: 'Provider token expired' },
      },
    ];
    mount(<IncidentsPage />, data);
    expect(screen.getByText(/3 matching incidents · 2 issues/)).toBeVisible();
    await user.click(
      screen.getByRole('button', {
        name: 'View Railway collection incident OW-0001',
      }),
    );
    const detail = screen.getByRole('complementary', {
      name: 'Selected incident',
    });
    expect(
      within(detail).getByRole('heading', { name: 'Affected services' }),
    ).toBeVisible();
    expect(
      within(detail).getByRole('button', { name: 'Store web OW-0002' }),
    ).toBeVisible();
    expect(
      within(detail).queryByRole('button', { name: /Separate account/ }),
    ).not.toBeInTheDocument();
    expect(
      within(detail).getByText(
        /does not establish that the application is down/,
      ),
    ).toBeVisible();
    expect(
      within(detail).queryByRole('button', { name: 'Start fix' }),
    ).not.toBeInTheDocument();
  });

  it('labels resolved application events as quiet and retains the failure and recovery evidence', async () => {
    const user = userEvent.setup();
    const data = snapshotFixture();
    data.incidents[0] = {
      ...data.incidents[0],
      status: 'resolved',
      kind: 'application error',
      evidence: {
        message: 'Connection pool exhausted',
        recovery: { quiet_seconds: 600 },
      },
    };
    mount(<IncidentsPage />, data);
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'Filter incident status' }),
      'resolved',
    );
    const detail = screen.getByRole('complementary', {
      name: 'Selected incident',
    });
    expect(within(detail).getByText('Quiet')).toBeVisible();
    expect(
      within(detail).getByText(
        /Production recovery and a code fix have not been verified/,
      ),
    ).toBeVisible();
    expect(within(detail).getByText('Connection pool exhausted')).toBeVisible();
    await user.click(within(detail).getByText('Recovery observation'));
    expect(within(detail).getByText(/"quiet_seconds": 600/)).toBeVisible();
    expect(
      within(detail).queryByRole('button', { name: 'Start fix' }),
    ).not.toBeInTheDocument();
  });
  it('uses the latest repair attempt when filtering active incidents', async () => {
    const user = userEvent.setup();
    const data = snapshotFixture();
    data.repairs = [
      {
        id: 'new-attempt',
        incident: 'OW-0001',
        status: 'queued',
        updated: 1_793_000_100,
      },
      {
        id: 'old-attempt',
        incident: 'OW-0001',
        status: 'blocked',
        updated: 1_793_000_000,
      },
    ];
    mount(<IncidentsPage />, data);
    await user.click(screen.getByRole('button', { name: /Agent active/ }));
    expect(
      screen.getByRole('button', { name: 'View Store API incident OW-0001' }),
    ).toBeVisible();
    expect(
      screen.getByRole('complementary', { name: 'Selected incident' }),
    ).toHaveTextContent('new-attempt');
  });

  it('renders evidence as text, removes unsafe links, and blocks a new repair on stale data', async () => {
    const user = userEvent.setup();
    const data = snapshotFixture();
    data.monitor.status = 'stale';
    mount(<IncidentsPage />, data);
    expect(screen.getByText('<script>unsafe</script>')).toBeVisible();
    expect(
      screen.queryByRole('link', { name: /Unsafe link/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Start fix' }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(/Fresh monitor readings are required/),
    ).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Copy command' }));
    expect(
      screen.getByText(/command copied|Copy the displayed command/i),
    ).toBeVisible();
    expect(document.querySelector('script')).toBeNull();
  });

  it('starts an eligible repair once and displays the returned durable progress', async () => {
    const user = userEvent.setup();
    const data = snapshotFixture();
    const posts: string[] = [];
    document.head.innerHTML =
      '<meta name="opswatch-csrf" content="test-token" />';
    vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
      if (init?.method === 'POST') {
        posts.push(path);
        return new Response(
          JSON.stringify({
            id: 'repair-1',
            incident: 'OW-0001',
            status: 'queued',
            updated: 1_793_000_001,
          }),
        );
      }
      return new Response(JSON.stringify(data));
    });
    mount(<IncidentsPage />, data);
    await user.click(screen.getByRole('button', { name: 'Start fix' }));
    await waitFor(() => expect(screen.getByText(/repair-1/)).toBeVisible());
    expect(
      screen.queryByRole('button', { name: 'Start fix' }),
    ).not.toBeInTheDocument();
    expect(posts).toEqual(['/api/incidents/OW-0001/fix']);
    expect(screen.getByRole('list', { name: 'Repair progress' })).toBeVisible();
  });

  it('displays interval query measurements without executing SQL', async () => {
    const user = userEvent.setup();
    const data: Snapshot = snapshotFixture();
    data.queries = [
      {
        name: 'Orders',
        kind: 'query performance',
        title: 'Orders',
        checked: 1_793_000_000,
        healthy: false,
        evidence: {
          interval_mean_ms: 2820.25,
          interval_calls: 7,
          query: 'select * from orders where state = $1',
        },
      },
    ];
    mount(<QueriesPage />, data);
    expect(screen.getByText('2,820.25 ms')).toBeVisible();
    await user.click(screen.getByText(/View SQL/));
    expect(
      screen.getByText('select * from orders where state = $1'),
    ).toBeVisible();
    expect(
      screen.queryByRole('button', { name: /execute|run SQL/i }),
    ).not.toBeInTheDocument();
  });

  it('shows recorded overview samples and exposes their service evidence', async () => {
    const user = userEvent.setup();
    mount(<OverviewPage />);
    expect(
      screen.getByRole('progressbar', {
        name: 'Store API response in milliseconds',
      }),
    ).toHaveAttribute('value', '890');
    expect(
      screen.getByText(/Each bar shows one recorded sample/),
    ).toBeVisible();
    await user.click(
      screen.getByRole('button', { name: 'View Store API evidence' }),
    );
    expect(screen.getByRole('dialog', { name: 'Store API' })).toBeVisible();
    expect(
      screen.getByRole('link', { name: /OW-0001 Slow API response/ }),
    ).toHaveAttribute('href', '/incidents?ref=OW-0001');
  });

  it('keeps draft PR review work and older incident links accessible', async () => {
    const user = userEvent.setup();
    const data = snapshotFixture();
    data.repairs = [
      {
        id: 'review-1',
        incident: 'OW-0009',
        status: 'pr_open',
        updated: 1_793_000_000,
        service: 'Store API',
        title: 'Repair older connection failure',
        summary: 'Draft PR opened after checks passed.',
        pr_url: 'https://github.com/example/store/pull/9',
      },
    ];
    mount(<RepairsPage />, data);
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'Filter agent work' }),
      'review',
    );
    expect(
      screen.getByRole('link', { name: 'Review draft PR' }),
    ).toHaveAttribute('href', 'https://github.com/example/store/pull/9');
    expect(
      screen.getByRole('link', { name: 'Inspect incident' }),
    ).toHaveAttribute('href', '/incidents?ref=OW-0009');
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'Filter agent work' }),
      'active',
    );
    expect(
      screen.queryByRole('link', { name: 'Review draft PR' }),
    ).not.toBeInTheDocument();
  });
});
