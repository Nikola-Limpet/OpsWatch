import type { Plugin } from 'vite';

export function devBackendOrigin(value = 'http://127.0.0.1:8767'): string {
  const invalid = () =>
    new Error(
      'OPSWATCH_DEV_BACKEND must be a loopback HTTP(S) origin, such as http://127.0.0.1:8767.',
    );
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw invalid();
  }
  const loopback =
    url.hostname === 'localhost' ||
    url.hostname === '[::1]' ||
    /^127(?:\.\d{1,3}){3}$/.test(url.hostname);
  if (
    !loopback ||
    !['http:', 'https:'].includes(url.protocol) ||
    url.username ||
    url.password ||
    url.pathname !== '/' ||
    url.search ||
    url.hash
  )
    throw invalid();
  return url.origin;
}

export function devCsrfPlugin(backend: string) {
  const origin = devBackendOrigin(backend);
  return {
    name: 'opswatch-dev-csrf',
    apply: 'serve',
    async transformIndexHtml(html: string) {
      if (!html.includes('__OPSWATCH_CSRF__')) return html;
      try {
        const response = await fetch(`${origin}/api/csrf`, {
          headers: { Accept: 'application/json' },
          cache: 'no-store',
          redirect: 'error',
          signal: AbortSignal.timeout(3000),
        });
        if (!response.ok)
          throw new Error(`Token request returned HTTP ${response.status}.`);
        const result: unknown = await response.json();
        const token =
          result && typeof result === 'object' && 'token' in result
            ? result.token
            : undefined;
        if (typeof token !== 'string' || !/^[A-Za-z0-9_-]{32,128}$/.test(token))
          throw new Error('The backend returned an invalid CSRF token.');
        return html.replaceAll('__OPSWATCH_CSRF__', () => token);
      } catch (cause) {
        throw new Error(
          `Could not load the OpsWatch CSRF token. Start opswatch dashboard at ${origin}, then reload this page. Set OPSWATCH_DEV_BACKEND if the dashboard uses another loopback port.`,
          { cause },
        );
      }
    },
  } satisfies Plugin;
}
