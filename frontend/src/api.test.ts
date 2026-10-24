import { afterEach, expect, it, vi } from 'vitest';
import { action, request } from './api';
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it('refuses to send mutation credentials to an external URL', async () => {
  const fetcher = vi.fn();
  vi.stubGlobal('fetch', fetcher);
  await expect(
    action('https://example.com/api/studio/actions', {}),
  ).rejects.toThrow(/same.origin/i);
  expect(fetcher).not.toHaveBeenCalled();
});

it('resolves relay commands to the saved action result', async () => {
  document.head.innerHTML = '<meta name="opswatch-csrf" content="token" />';
  const fetcher = vi.fn(
    async (path: string) =>
      new Response(
        JSON.stringify(
          path === '/api/studio/actions'
            ? { command_id: 'command-1' }
            : { state: 'done', result: { session: { id: 'session-1' } } },
        ),
      ),
  );
  vi.stubGlobal('fetch', fetcher);
  expect(
    await action('/api/studio/actions', {
      action: 'session_create',
      payload: {},
    }),
  ).toEqual({ session: { id: 'session-1' } });
  expect(fetcher.mock.calls.map((call) => call[0])).toEqual([
    '/api/studio/actions',
    '/api/commands/command-1',
  ]);
});

it('surfaces server errors and does not treat an HTTP failure as saved data', async () => {
  vi.stubGlobal(
    'fetch',
    async () =>
      new Response(JSON.stringify({ error: 'Worker unavailable' }), {
        status: 503,
      }),
  );
  await expect(request('/api/studio')).rejects.toThrow('Worker unavailable');
});

it('preserves a supplied request UUID when retrying the same owner action', async () => {
  document.head.innerHTML = '<meta name="opswatch-csrf" content="token" />';
  let received: string | null = null;
  vi.stubGlobal('fetch', async (_path: string, options: RequestInit) => {
    received = new Headers(options.headers).get('X-OpsWatch-Request-ID');
    return new Response('{}');
  });
  await request('/api/studio/actions', {
    method: 'POST',
    headers: {
      'X-OpsWatch-Request-ID': '352b8768-6f23-471a-8e11-c7c1c49d40c9',
    },
    body: '{}',
  });
  expect(received).toBe('352b8768-6f23-471a-8e11-c7c1c49d40c9');
});
