import { afterEach, describe, expect, it, vi } from 'vitest';
import { devBackendOrigin, devCsrfPlugin } from './backend';

const html =
  '<html><head><meta name="opswatch-csrf" content="__OPSWATCH_CSRF__" /></head></html>';
const token = 'uMGF3ub0zW6KGPJ4FtsCqPEFSADGFnwwg3d5cFQ3D-0';
afterEach(() => vi.unstubAllGlobals());

describe('Vite development CSRF injection', () => {
  it('injects the current backend token so the API client can authorize mutations', async () => {
    const calls: string[] = [];
    vi.stubGlobal('fetch', async (url: string) => {
      calls.push(url);
      return new Response(JSON.stringify({ token }));
    });
    const transformed = await devCsrfPlugin(
      'http://127.0.0.1:9471',
    ).transformIndexHtml(html);
    const document = new DOMParser().parseFromString(transformed, 'text/html');
    expect(
      document
        .querySelector('meta[name="opswatch-csrf"]')
        ?.getAttribute('content'),
    ).toBe(token);
    expect(calls).toEqual(['http://127.0.0.1:9471/api/csrf']);
    expect(transformed).not.toContain('__OPSWATCH_CSRF__');
  });

  it('fails with a backend startup instruction when no token can be fetched', async () => {
    vi.stubGlobal('fetch', async () => {
      throw new TypeError('Connection refused');
    });
    await expect(
      devCsrfPlugin('http://127.0.0.1:9471').transformIndexHtml(html),
    ).rejects.toThrow(/Start.*opswatch dashboard.*9471/);
  });

  it('refuses malformed backend token data instead of inserting HTML', async () => {
    vi.stubGlobal(
      'fetch',
      async () =>
        new Response(JSON.stringify({ token: '\"><script>unsafe</script>' })),
    );
    await expect(
      devCsrfPlugin('http://127.0.0.1:9471').transformIndexHtml(html),
    ).rejects.toThrow(/CSRF token/);
  });

  it('does not reuse a token across backend restarts', async () => {
    const tokens = [token, 'OxvjH_BXaQlLW6GsCjbPNsRMw3eRaYuxeq4Ej_0HfCw'];
    vi.stubGlobal(
      'fetch',
      async () => new Response(JSON.stringify({ token: tokens.shift() })),
    );
    const plugin = devCsrfPlugin('http://127.0.0.1:9471');
    expect(await plugin.transformIndexHtml(html)).toContain(token);
    expect(await plugin.transformIndexHtml(html)).toContain(
      'OxvjH_BXaQlLW6GsCjbPNsRMw3eRaYuxeq4Ej_0HfCw',
    );
  });
});

describe('development backend origin', () => {
  it('defaults to the documented dashboard port and accepts isolated loopback ports', () => {
    expect(devBackendOrigin()).toBe('http://127.0.0.1:8767');
    expect(devBackendOrigin('http://localhost:9471/')).toBe(
      'http://localhost:9471',
    );
    expect(devBackendOrigin('http://[::1]:9472')).toBe('http://[::1]:9472');
  });

  it.each([
    'https://example.com',
    'http://localhost.example.com:9471',
    'http://user:password@127.0.0.1:9471',
    'http://127.0.0.1:9471/api',
    'http://127.0.0.1:9471/?token=secret',
    'file:///tmp/backend',
  ])('rejects non-loopback or non-origin backend configuration %s', (value) => {
    expect(() => devBackendOrigin(value)).toThrow(
      /OPSWATCH_DEV_BACKEND.*loopback.*origin/,
    );
  });
});
