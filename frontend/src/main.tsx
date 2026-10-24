import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App, createAppRouter, redirectLegacyHash } from './App';
import './styles.css';

redirectLegacyHash();
const client = new QueryClient({
  defaultOptions: { mutations: { retry: false } },
});
const router = createAppRouter();
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <App router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
