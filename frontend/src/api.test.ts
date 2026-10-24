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

function useTransportTimers() {
  vi.useFakeTimers();
  vi.spyOn(AbortSignal, 'timeout').mockImplementation((milliseconds) => {
    const controller = new AbortController();
    setTimeout(
      () => controller.abort(new DOMException('The operation timed out.', 'TimeoutError')),
      milliseconds,
    );
    return controller.signal;
  });
  document.head.innerHTML = '<meta name="opswatch-csrf" content="token" />';
}

it('allows a slow HTTP read to finish after ten seconds', async () => {
  useTransportTimers();
  vi.stubGlobal('fetch', (_path: string, options: RequestInit) =>
    new Promise((resolve, reject) => {
      const timer = setTimeout(() => resolve(new Response('{"sessions":[]}')), 15_000);
      options.signal?.addEventListener('abort', () => {
        clearTimeout(timer);
        reject(options.signal?.reason);
      }, { once: true });
    }),
  );
  const result = request('/api/studio');
  const assertion = expect(result).resolves.toEqual({ sessions: [] });
  await Promise.all([assertion, vi.advanceTimersByTimeAsync(15_000)]);
});

it('waits through a slow bridge cycle for the saved report read', async () => {
  useTransportTimers();
  const started = Date.now();
  vi.stubGlobal('fetch', async (path: string) => new Response(JSON.stringify(
    path === '/api/studio/actions'
      ? { command_id: 'report-read' }
      : Date.now() - started < 45_000
        ? { state: 'running', result: null, error: null }
        : { state: 'done', result: { report: { id: 'report-1', content: 'Saved report' } }, error: null },
  )));
  const assertion = expect(action('/api/studio/actions', {
    action: 'report_read', payload: { report_id: 'report-1' },
  })).resolves.toEqual({ report: { id: 'report-1', content: 'Saved report' } });
  await Promise.all([assertion, vi.advanceTimersByTimeAsync(46_000)]);
});

it.each([
  ['HTTP timeout', () => { throw new DOMException('The operation timed out.', 'TimeoutError'); }],
  ['network disconnect', () => { throw new TypeError('Failed to fetch'); }],
  ['temporary 502', () => new Response('Bad gateway', { status: 502 })],
  ['temporary 503', () => new Response('{"error":"Relay data is temporarily unavailable."}', { status: 503 })],
  ['temporary 504', () => new Response('Gateway timeout', { status: 504 })],
  ['response body timeout', () => {
    const response = new Response('{}');
    vi.spyOn(response, 'json').mockRejectedValue(
      new DOMException('The operation timed out.', 'TimeoutError'),
    );
    return response;
  }],
])('recovers a saved action after a command-status %s', async (_label, fail) => {
  useTransportTimers();
  let polls = 0;
  let submissions = 0;
  vi.stubGlobal('fetch', async (path: string) => {
    if (path === '/api/studio/actions') {
      submissions++;
      return new Response('{"command_id":"saved-action"}');
    }
    if (polls++ === 0) return fail();
    return new Response('{"state":"done","result":{"session":{"id":"saved-session"}},"error":null}');
  });
  const assertion = expect(action('/api/studio/actions', {
    action: 'session_create', payload: {},
  })).resolves.toEqual({ session: { id: 'saved-session' } });
  await Promise.all([assertion, vi.advanceTimersByTimeAsync(5_000)]);
  expect(submissions).toBe(1);
});

it('reuses the saved Studio submission UUID and body after its response is lost', async () => {
  useTransportTimers();
  const saved = new Map<string, string>();
  const submissions: { id: string; body: string }[] = [];
  let creates = 0;
  vi.stubGlobal('fetch', async (path: string, options: RequestInit) => {
    if (path !== '/api/studio/actions')
      return new Response('{"state":"done","result":{"report":{"id":"saved-report"}},"error":null}');
    const id = new Headers(options.headers).get('X-OpsWatch-Request-ID')!;
    const body = String(options.body);
    submissions.push({ id, body });
    if (!saved.has(id)) { saved.set(id, body); creates++; }
    if (submissions.length === 1)
      throw new DOMException('The operation timed out.', 'TimeoutError');
    return new Response(JSON.stringify({ command_id: id }));
  });
  const assertion = expect(action('/api/studio/actions', {
    action: 'report_create', payload: { title: 'Saved once' },
  })).resolves.toEqual({ report: { id: 'saved-report' } });
  await Promise.all([assertion, vi.advanceTimersByTimeAsync(5_000)]);
  expect(creates).toBe(1);
  expect(submissions).toHaveLength(2);
  expect(submissions[0].id).toMatch(/^[0-9a-f-]{36}$/i);
  expect(submissions[1]).toEqual(submissions[0]);
});

it.each(['error', 'expired'])('preserves a terminal command %s without resubmitting', async (state) => {
  useTransportTimers();
  const fetcher = vi.fn(async (path: string) => new Response(JSON.stringify(
    path === '/api/studio/actions'
      ? { command_id: 'failed-action' }
      : { state, result: null, error: 'The worker rejected this command.' },
  )));
  vi.stubGlobal('fetch', fetcher);
  const assertion = expect(action('/api/studio/actions', {})).rejects.toThrow('The worker rejected this command.');
  await Promise.all([assertion, vi.advanceTimersByTimeAsync(5_000)]);
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it.each([403, 409, 429, 503])('surfaces a Studio submission HTTP %s without retrying', async (status) => {
  useTransportTimers();
  const fetcher = vi.fn(async () => new Response('{"error":"Cannot accept this action."}', { status }));
  vi.stubGlobal('fetch', fetcher);
  await expect(action('/api/studio/actions', {})).rejects.toThrow('Cannot accept this action.');
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it('does not retry a legacy mutation that lacks Studio UUID deduplication', async () => {
  useTransportTimers();
  const fetcher = vi.fn(async () => { throw new DOMException('The operation timed out.', 'TimeoutError'); });
  vi.stubGlobal('fetch', fetcher);
  await expect(action('/api/incidents/OW-0001/fix', {})).rejects.toThrow(/timed out|too long/i);
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it('bounds command-status retries and tells the user to check saved state', async () => {
  useTransportTimers();
  const started = Date.now();
  const fetcher = vi.fn(async (path: string, options: RequestInit) => {
    if (path === '/api/studio/actions') return new Response('{"command_id":"unreachable"}');
    return new Promise<Response>((_resolve, reject) => {
      options.signal?.addEventListener('abort', () => reject(options.signal?.reason), { once: true });
    });
  });
  vi.stubGlobal('fetch', fetcher);
  let finished: number | undefined;
  const result = action('/api/studio/actions', {}).finally(() => { finished = Date.now() - started; });
  const assertion = expect(result).rejects.toThrow(/check the saved state before trying again/i);
  await Promise.all([assertion, vi.advanceTimersByTimeAsync(140_000)]);
  expect(finished).toBeGreaterThanOrEqual(120_000);
  expect(finished).toBeLessThanOrEqual(130_000);
});

it('shows a readable timeout error after a slow request exhausts its HTTP budget', async () => {
  useTransportTimers();
  vi.stubGlobal('fetch', async (_path: string, options: RequestInit) =>
    new Promise<Response>((_resolve, reject) => {
      options.signal?.addEventListener('abort', () => reject(options.signal?.reason), { once: true });
    }),
  );
  const outcome = request('/api/studio').catch((error: unknown) => error);
  await vi.advanceTimersByTimeAsync(30_000);
  const error = await outcome;
  expect(error).toBeInstanceOf(Error);
  expect((error as Error).message).toMatch(/too long.*check the saved state/i);
});

it('recovers when the HTTP timeout aborts the command response body', async () => {
  useTransportTimers();
  let polls = 0;
  vi.stubGlobal('fetch', async (path: string, options: RequestInit) => {
    if (path === '/api/studio/actions') return new Response('{"command_id":"body-timeout"}');
    if (polls++ > 0) return new Response('{"state":"done","result":{"session":{"id":"saved-session"}},"error":null}');
    const response = new Response();
    vi.spyOn(response, 'json').mockImplementation(() => new Promise((_resolve, reject) => {
      options.signal?.addEventListener('abort', () => {
        reject(new DOMException('The operation was aborted.', 'AbortError'));
      }, { once: true });
    }));
    return response;
  });
  const assertion = expect(action('/api/studio/actions', {})).resolves.toEqual({ session: { id: 'saved-session' } });
  await Promise.all([assertion, vi.advanceTimersByTimeAsync(35_000)]);
});

it.each([
  [404, '{"error":"The command does not exist."}', 'The command does not exist.'],
  [200, '{broken-json', 'unreadable response'],
])('preserves a permanent command-status failure with HTTP %s', async (status, response, message) => {
  useTransportTimers();
  const fetcher = vi.fn(async (path: string) => path === '/api/studio/actions'
    ? new Response('{"command_id":"missing-action"}')
    : new Response(response, { status }));
  vi.stubGlobal('fetch', fetcher);
  const assertion = expect(action('/api/studio/actions', {})).rejects.toThrow(message);
  await Promise.all([assertion, vi.advanceTimersByTimeAsync(5_000)]);
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it('bounds initial Studio submission retries without minting another UUID', async () => {
  useTransportTimers();
  const ids = new Set<string | null>();
  vi.stubGlobal('fetch', async (_path: string, options: RequestInit) => {
    ids.add(new Headers(options.headers).get('X-OpsWatch-Request-ID'));
    return new Promise<Response>((_resolve, reject) => {
      options.signal?.addEventListener('abort', () => reject(options.signal?.reason), { once: true });
    });
  });
  let finished: number | undefined;
  const started = Date.now();
  const result = action('/api/studio/actions', {}).finally(() => { finished = Date.now() - started; });
  const assertion = expect(result).rejects.toThrow(/check the saved state before trying again/i);
  await Promise.all([assertion, vi.advanceTimersByTimeAsync(140_000)]);
  expect(finished).toBeGreaterThanOrEqual(120_000);
  expect(finished).toBeLessThanOrEqual(130_000);
  expect(ids.size).toBe(1);
});
