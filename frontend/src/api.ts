import { useQuery } from '@tanstack/react-query';
import type { Snapshot } from './types';

const HTTP_TIMEOUT_MS = 30_000;
// Relay commands live for 120 seconds. Allow one final status read after expiry.
const ACTION_TIMEOUT_MS = 130_000;
const POLL_INTERVAL_MS = 700;

class HttpError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

class RequestTimeoutError extends Error {
  constructor() {
    super(
      'OpsWatch took too long to respond. Refresh to check the saved state before trying again.',
    );
    this.name = 'TimeoutError';
  }
}

function isTimeout(error: unknown): boolean {
  return (
    (error instanceof Error || error instanceof DOMException) &&
    error.name === 'TimeoutError'
  );
}

function isTransportFailure(error: unknown): boolean {
  return error instanceof TypeError || isTimeout(error);
}

export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const url = new URL(path, window.location.origin);
  if (url.origin !== window.location.origin || url.username || url.password) {
    throw new Error('OpsWatch requests must use the same origin.');
  }
  const method = (options.method || 'GET').toUpperCase();
  const headers = new Headers(options.headers);
  headers.set('Accept', 'application/json');
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const csrf = document.querySelector<HTMLMetaElement>(
      'meta[name="opswatch-csrf"]',
    )?.content;
    if (!csrf || csrf === '__OPSWATCH_CSRF__')
      throw new Error('Reload OpsWatch before saving changes.');
    headers.set('X-OpsWatch-CSRF', csrf);
    if (!headers.has('X-OpsWatch-Request-ID'))
      headers.set('X-OpsWatch-Request-ID', crypto.randomUUID());
  }
  const signal = options.signal ?? AbortSignal.timeout(HTTP_TIMEOUT_MS);
  try {
    const response = await fetch(path, {
      ...options,
      method,
      headers,
      cache: 'no-store',
      credentials: 'same-origin',
      signal,
    });
    let result: unknown;
    try {
      result = response.status === 204 ? undefined : await response.json();
    } catch (error) {
      if (signal.aborted || isTransportFailure(error)) throw error;
      throw new HttpError(
        response.ok
          ? 'OpsWatch returned an unreadable response.'
          : `OpsWatch request failed (${response.status}).`,
        response.status,
      );
    }
    if (!response.ok) {
      const error = result as { error?: unknown; detail?: unknown } | undefined;
      throw new HttpError(
        typeof error?.error === 'string'
          ? error.error
          : typeof error?.detail === 'string'
            ? error.detail
            : `OpsWatch request failed (${response.status}).`,
        response.status,
      );
    }
    return result as T;
  } catch (error) {
    if (signal.aborted) error = signal.reason;
    if (isTimeout(error)) {
      throw new RequestTimeoutError();
    }
    throw error;
  }
}

export async function action<T>(path: string, body: unknown): Promise<T> {
  const deadline = Date.now() + ACTION_TIMEOUT_MS;
  const headers = {
    'Content-Type': 'application/json',
    'X-OpsWatch-Request-ID': crypto.randomUUID(),
  };
  const serialized = JSON.stringify(body);
  // Both Studio backends deduplicate this UUID. Legacy local actions may not.
  const retrySubmission =
    new URL(path, window.location.origin).pathname === '/api/studio/actions';
  const signal = () =>
    AbortSignal.timeout(Math.min(HTTP_TIMEOUT_MS, deadline - Date.now()));
  const pause = () =>
    new Promise((resolve) =>
      setTimeout(
        resolve,
        Math.min(POLL_INTERVAL_MS, Math.max(0, deadline - Date.now())),
      ),
    );
  let result: T & { command_id?: string };
  while (true) {
    try {
      result = await request<T & { command_id?: string }>(path, {
        method: 'POST',
        headers,
        body: serialized,
        signal: signal(),
      });
      break;
    } catch (error) {
      if (!retrySubmission || !isTransportFailure(error)) throw error;
      if (Date.now() >= deadline) throw waitingForComputer();
      await pause();
      if (Date.now() >= deadline) throw waitingForComputer();
    }
  }
  if (!result?.command_id) return result;
  while (Date.now() < deadline) {
    await pause();
    if (Date.now() >= deadline) break;
    let command: { state: string; result?: T; error?: string };
    try {
      command = await request<typeof command>(
        `/api/commands/${encodeURIComponent(result.command_id)}`,
        { signal: signal() },
      );
    } catch (error) {
      if (
        isTransportFailure(error) ||
        (error instanceof HttpError && [502, 503, 504].includes(error.status))
      ) continue;
      throw error;
    }
    if (command.state === 'done') return command.result as T;
    if (['error', 'expired'].includes(command.state)) {
      throw new Error(
        command.error ||
          'The request expired before your computer processed it.',
      );
    }
  }
  throw waitingForComputer();
}

function waitingForComputer(): Error {
  return new Error(
    'Still waiting for your computer. Refresh to check the saved state before trying again.',
  );
}

export function useSnapshot() {
  return useQuery<Snapshot>({
    queryKey: ['snapshot'],
    queryFn: async () => {
      const data = await request<Snapshot>('/api/snapshot');
      if (!Array.isArray(data.services) || !data.summary || !data.monitor)
        throw new Error('OpsWatch returned an invalid monitoring snapshot.');
      return data;
    },
    staleTime: 20_000,
    refetchInterval: (query) =>
      query.state.data?.investigations?.some((job) =>
        ['queued', 'running'].includes(job.status),
      )
        ? 3000
        : 30_000,
    refetchOnWindowFocus: true,
    retry: 1,
  });
}
