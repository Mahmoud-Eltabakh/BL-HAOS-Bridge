import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { DiscoveryModal } from './DiscoveryModal';
import { apiClient } from '../api/client';

vi.mock('../api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/client')>();
  return {
    ...original,
    apiClient: {
      ...original.apiClient,
      startScan: vi.fn(),
      stopScan: vi.fn(),
      pairDevice: vi.fn(),
      removeDevice: vi.fn(),
    },
  };
});

const props = {
  devices: [],
  isScanning: false,
  onRefresh: vi.fn(),
  onClose: vi.fn(),
};

describe('DiscoveryModal', () => {
  beforeEach(() => {
    vi.mocked(apiClient.startScan).mockResolvedValue({ status: 'ok', scanning: true });
    vi.mocked(apiClient.stopScan).mockResolvedValue({ status: 'ok', scanning: false });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('opens after being rendered closed without violating hook order', async () => {
    const { rerender } = render(<DiscoveryModal {...props} isOpen={false} />);

    expect(screen.queryByText('Add Bluetooth Speaker')).not.toBeInTheDocument();

    rerender(<DiscoveryModal {...props} isOpen />);

    expect(screen.getByText('Add Bluetooth Speaker')).toBeInTheDocument();
  });

  it('auto-starts discovery once the modal opens', async () => {
    render(<DiscoveryModal {...props} isOpen />);

    await waitFor(() => {
      expect(apiClient.startScan).toHaveBeenCalledTimes(1);
    });
  });

  it('does not auto-start discovery when a scan is already running', async () => {
    render(<DiscoveryModal {...props} isOpen isScanning />);

    await waitFor(() => {
      expect(screen.getByText('Add Bluetooth Speaker')).toBeInTheDocument();
    });
    expect(apiClient.startScan).not.toHaveBeenCalled();
  });

  it('connects a discovered speaker from the device list', async () => {
    vi.mocked(apiClient.pairDevice).mockResolvedValue({ status: 'ok', paired: true });
    const device = {
      path: '/org/bluez/hci0/dev_AA_BB_CC_DD_EE_01',
      adapter_name: 'hci0',
      address: 'aa:bb:cc:dd:ee:01',
      name: 'Kitchen Speaker',
      alias: 'Kitchen Speaker',
      paired: false,
      trusted: false,
      connected: false,
      is_audio_sink: true,
      device_type: 'speaker',
    };
    render(<DiscoveryModal {...props} devices={[device]} isOpen />);

    fireEvent.click(screen.getByRole('button', { name: 'Connect to Kitchen Speaker' }));

    await waitFor(() => {
      expect(apiClient.pairDevice).toHaveBeenCalledWith('aa:bb:cc:dd:ee:01', '0000');
    });
    expect(await screen.findByText('Connected to aa:bb:cc:dd:ee:01.')).toBeInTheDocument();
    expect(props.onRefresh).toHaveBeenCalled();
  });

  it('surfaces the bridge reason when pairing fails', async () => {
    vi.mocked(apiClient.pairDevice).mockRejectedValue(
      new Error('Pairing failed: org.bluez.Error.Failed br-connection-page-timeout'),
    );
    const device = {
      path: '/org/bluez/hci0/dev_AA_BB_CC_DD_EE_02',
      adapter_name: 'hci0',
      address: 'aa:bb:cc:dd:ee:02',
      name: 'Garden Speaker',
      alias: 'Garden Speaker',
      paired: false,
      trusted: false,
      connected: false,
      is_audio_sink: true,
      device_type: 'speaker',
    };
    render(<DiscoveryModal {...props} devices={[device]} isOpen />);

    fireEvent.click(screen.getByRole('button', { name: 'Connect to Garden Speaker' }));

    expect(
      await screen.findByText(/Ensure the speaker is in pairing mode \(blinking LED\)/),
    ).toBeInTheDocument();
  });
});
