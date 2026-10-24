import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { snapshotFixture } from '../../test/fixtures';
import { IncidentsPage } from './incidents';
import { RepairsPage } from './repairs';

function mount(page: React.ReactNode, data = snapshotFixture()) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity },
      mutations: { retry: false },
    },
  });
  client.setQueryData(['snapshot'], data);
  return render(
    <QueryClientProvider client={client}>{page}</QueryClientProvider>,
  );
}
afterEach(() => vi.unstubAllGlobals());

it('starts a security investigation directly without copying Telegram commands', async () => {
  const user = userEvent.setup();
  const data = snapshotFixture();
  data.incidents[0].kind = 'security';
  data.incidents[0].repair = {
    available: false,
    reason: 'Security review is needed.',
  };
  data.incidents[0].next_step = 'Review IP evidence. Use /diagnose.';
  document.head.innerHTML =
    '<meta name="opswatch-csrf" content="test-token" />';
  const posts: unknown[] = [];
  vi.stubGlobal('fetch', async (_path: string, init?: RequestInit) => {
    if (init?.method === 'POST') {
      posts.push(JSON.parse(String(init.body)));
      return new Response(
        JSON.stringify({
          session: { id: 'saved-session' },
          job: { id: 'saved-job', status: 'queued', updated: 1793000001 },
        }),
      );
    }
    return new Response(JSON.stringify(data));
  });
  mount(<IncidentsPage />, data);
  expect(
    screen.queryByRole('button', { name: 'Copy command' }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByText(/\/diagnose|Hermes Telegram/),
  ).not.toBeInTheDocument();
  await user.click(
    screen.getByRole('button', { name: 'Investigate with agent' }),
  );
  await waitFor(() =>
    expect(posts).toEqual([
      { action: 'incident_investigate', payload: { incident_ref: 'OW-0001' } },
    ]),
  );
  expect(
    await screen.findByRole('link', { name: 'Open investigation' }),
  ).toHaveAttribute('href', '/studio?session=saved-session');
  expect(
    screen.queryByRole('button', { name: 'Investigate with agent' }),
  ).not.toBeInTheDocument();
});

it('shows a completed investigation as findings rather than a production fix', () => {
  const data = snapshotFixture();
  data.investigations = [
    {
      id: 'job',
      incident: 'OW-0001',
      session_id: 'session',
      status: 'succeeded',
      summary: 'Review the edge policy. No production changes applied.',
      updated: 1793000001,
    },
  ];
  mount(<IncidentsPage />, data);
  expect(screen.getByText(/No production changes applied/)).toBeVisible();
  expect(
    screen.getByRole('link', { name: 'Open investigation' }),
  ).toHaveAttribute('href', '/studio?session=session');
  expect(screen.getByText('Findings ready')).toBeVisible();
});

it('includes running incident investigations in Agent work', () => {
  const data = snapshotFixture();
  data.investigations = [
    {
      id: 'job',
      incident: 'OW-0001',
      session_id: 'session',
      status: 'running',
      summary: '',
      updated: 1793000001,
      title: 'Repeated probes',
    },
  ];
  mount(<RepairsPage />, data);
  expect(screen.getByText('Repeated probes')).toBeVisible();
  expect(
    screen.getByRole('link', { name: 'Open investigation' }),
  ).toHaveAttribute('href', '/studio?session=session');
});
