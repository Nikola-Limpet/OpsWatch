import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory } from '@tanstack/react-router';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { App, createAppRouter, redirectLegacyHash } from './App';
import { snapshotFixture } from './test/fixtures';

describe('React dashboard routing and freshness', () => {
  it('keeps the current navigation destination selected for saved detail URLs', async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    });
    client.setQueryData(['snapshot'], snapshotFixture());
    const router = createAppRouter(
      createMemoryHistory({ initialEntries: ['/projects?ref=OW-0024'] }),
    );
    render(
      <QueryClientProvider client={client}>
        <App router={router} />
      </QueryClientProvider>,
    );
    await screen.findByRole('heading', { level: 1, name: 'Projects' });
    expect(
      screen.getByRole('link', { name: 'Projects' }),
    ).toHaveAttribute('aria-current', 'page');
    expect(
      screen.getByRole('link', { name: 'Reports' }),
    ).not.toHaveAttribute('aria-current');
  });
  it('navigates between real React monitoring pages from the app sidebar', async () => {
    const user = userEvent.setup();
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    });
    client.setQueryData(['snapshot'], snapshotFixture());
    const router = createAppRouter(
      createMemoryHistory({ initialEntries: ['/projects'] }),
    );
    render(
      <QueryClientProvider client={client}>
        <App router={router} />
      </QueryClientProvider>,
    );
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Projects' }),
    ).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Configure Store API' }),
    ).toBeVisible();
    await user.click(screen.getByRole('link', { name: 'Database queries' }));
    expect(
      await screen.findByRole('heading', {
        level: 1,
        name: 'Database queries',
      }),
    ).toBeVisible();
    expect(screen.getByText(/No query samples recorded/)).toBeVisible();
    expect(router.state.location.pathname).toBe('/queries');
  });
  it('retains the last snapshot while reporting refresh failure and disabling changes', async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    });
    client.setQueryData(['snapshot'], snapshotFixture());
    const router = createAppRouter(
      createMemoryHistory({ initialEntries: ['/projects'] }),
    );
    render(
      <QueryClientProvider client={client}>
        <App router={router} />
      </QueryClientProvider>,
    );
    await screen.findByRole('button', { name: 'Configure Store API' });
    await act(async () => {
      try {
        await client.fetchQuery({
          queryKey: ['snapshot'],
          queryFn: async () => {
            throw new Error('Network unavailable');
          },
          retry: false,
          staleTime: 0,
        });
      } catch {}
    });
    await waitFor(() =>
      expect(
        screen.getByText(/Showing the last received snapshot/),
      ).toBeVisible(),
    );
    expect(
      screen.getByRole('button', { name: 'Configure Store API' }),
    ).toBeVisible();
    expect(
      screen.getByRole('switch', { name: 'Monitoring for Store API' }),
    ).toBeDisabled();
    expect(screen.getByText('Dashboard disconnected')).toBeVisible();
  });
  it('translates saved hash links to their React route without discarding an incident ref', () => {
    window.history.replaceState({}, '', '/?ref=OW-0024#services');
    redirectLegacyHash();
    expect(window.location.pathname).toBe('/projects');
    expect(window.location.search).toBe('?ref=OW-0024');
    expect(window.location.hash).toBe('');
    window.history.replaceState({}, '', '/#incidents');
    redirectLegacyHash();
    expect(window.location.pathname).toBe('/incidents');
    window.history.replaceState({}, '', '/');
  });
});
