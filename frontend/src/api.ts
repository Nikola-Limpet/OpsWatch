import { useQuery } from '@tanstack/react-query';
import type { Snapshot } from './types';

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
  const response = await fetch(path, {
    ...options,
    method,
    headers,
    cache: 'no-store',
    credentials: 'same-origin',
    signal: options.signal ?? AbortSignal.timeout(10_000),
  });
  let result: unknown;
  try {
    result = response.status === 204 ? undefined : await response.json();
  } catch {
    throw new Error(
      response.ok
        ? 'OpsWatch returned an unreadable response.'
        : `OpsWatch request failed (${response.status}).`,
    );
  }
  if (!response.ok) {
    const error = result as { error?: unknown; detail?: unknown } | undefined;
    throw new Error(
      typeof error?.error === 'string'
        ? error.error
        : typeof error?.detail === 'string'
          ? error.detail
          : `OpsWatch request failed (${response.status}).`,
    );
  }
  return result as T;
}

export async function action<T>(path: string, body: unknown): Promise<T> {
  const result = await request<T & { command_id?: string }>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!result?.command_id) return result;
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 700));
    const command = await request<{
      state: string;
      result?: T;
      error?: string;
    }>(`/api/commands/${encodeURIComponent(result.command_id)}`);
    if (command.state === 'done') return command.result as T;
    if (['error', 'expired'].includes(command.state)) {
      throw new Error(
        command.error ||
          'The request expired before your computer processed it.',
      );
    }
  }
  throw new Error(
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
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
    retry: 1,
  });
}
