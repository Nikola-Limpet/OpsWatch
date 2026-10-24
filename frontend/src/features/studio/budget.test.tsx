import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { WorkerState } from './components';

it('distinguishes the hourly budget wait from an offline worker', () => {
  render(<WorkerState worker={{status:'running',last_cycle:100,active_job_id:null,queued:2,budget:{limit:6,used:6,available:0,next_available_at:13500}}} />);
  expect(screen.getByText(/6\/6 hourly runs used/)).toBeVisible();
  expect(screen.getByText(/Worker online/)).toBeVisible();
});
