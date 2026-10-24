import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { devBackendOrigin, devCsrfPlugin } from './dev/backend.ts';

export default defineConfig(({ command }) => {
  const backend = devBackendOrigin(
    command === 'serve' ? process.env.OPSWATCH_DEV_BACKEND : undefined,
  );
  const proxy = { target: backend, changeOrigin: false };
  return {
    plugins: [react(), devCsrfPlugin(backend)],
    base: '/',
    build: {
      outDir: '../src/opswatch/dashboard_assets/web',
      emptyOutDir: true,
      cssCodeSplit: false,
      license: { fileName: 'assets/THIRD-PARTY-LICENSES.txt' },
      rolldownOptions: {
        output: {
          entryFileNames: 'assets/app.js',
          chunkFileNames: 'assets/[name]-[hash].js',
          assetFileNames: (asset) =>
            asset.names.some((name) => name.endsWith('.css'))
              ? 'assets/app.css'
              : 'assets/[name]-[hash][extname]',
          codeSplitting: {
            groups: [
              {
                name: 'react',
                test: /node_modules[\\/](?:react|react-dom|scheduler)[\\/]/,
                priority: 30,
              },
              {
                name: 'assistant-ui',
                test: /node_modules[\\/]@assistant-ui[\\/]react[\\/]/,
                priority: 20,
              },
              {
                name: 'markdown',
                test: /node_modules[\\/](?:react-markdown|@assistant-ui[\\/]react-markdown)[\\/]/,
                priority: 10,
              },
            ],
          },
        },
      },
    },
    server: {
      proxy: {
        '/api': proxy,
        '/favicons': proxy,
        '/assets/geist-sans.woff2': proxy,
        '/assets/geist-mono.woff2': proxy,
        '/assets/THIRD-PARTY-LICENSES.txt': proxy,
      },
    },
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
      restoreMocks: true,
      globals: true,
    },
  };
});
