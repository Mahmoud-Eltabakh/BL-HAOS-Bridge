import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { RecoveryPanel } from './RecoveryPanel';
import { apiClient, RecoveryContract } from '../api/client';

vi.mock('../api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/client')>();
  return {
    ...original,
    apiClient: {
      ...original.apiClient,
      executeRecovery: vi.fn(),
    },
  };
});

const contract: RecoveryContract = {
  contract_version: 1,
  current_status: 'degraded',
  correlation_id: 'recovery-0001',
  guidance: {
    failure_class: 'reconnect_exhausted',
    diagnosis: 'Automatic reconnect attempts reached their bounded limit.',
    next_steps: ['Move the speaker closer and retry one bounded reconnect.'],
    prerequisites: ['Speaker remains powered on'],
    actions: ['retry_reconnect', 'refresh_device'],
    expected_outcome: 'The speaker reconnects or returns a classified failure.',
    status: 'available',
  },
};

describe('RecoveryPanel', () => {
  it('renders guidance actions from the contract', () => {
    render(<RecoveryPanel contract={contract} target="aa:bb:cc:dd:ee:01" onComplete={() => {}} />);
    expect(screen.getByText('Automatic reconnect attempts reached their bounded limit.')).toBeInTheDocument();
    expect(screen.getByText('retry reconnect')).toBeInTheDocument();
    expect(screen.getByText('refresh device')).toBeInTheDocument();
  });

  it('shows unavailable message when no contract exists', () => {
    render(<RecoveryPanel contract={null} target={undefined} onComplete={() => {}} />);
    expect(screen.getByText('Recovery guidance is unavailable.')).toBeInTheDocument();
  });

  it('reports success after an action completes', async () => {
    vi.mocked(apiClient.executeRecovery).mockResolvedValue({
      action_id: 'retry_reconnect',
      target: 'aa:bb:cc:dd:ee:01',
      correlation_id: 'recovery-0002',
      result: 'succeeded',
      detail: 'Bounded recovery action completed',
    });
    render(<RecoveryPanel contract={contract} target="aa:bb:cc:dd:ee:01" onComplete={() => {}} />);
    fireEvent.click(screen.getByText('retry reconnect'));

    await waitFor(() => {
      expect(screen.getByText(/Recovery completed/)).toBeInTheDocument();
    });
  });
});
