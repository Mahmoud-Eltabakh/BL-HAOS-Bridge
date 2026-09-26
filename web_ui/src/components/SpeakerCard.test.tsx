import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { SpeakerCard } from './SpeakerCard';
import { apiClient, DeviceInfo } from '../api/client';

vi.mock('../api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/client')>();
  return {
    ...original,
    apiClient: {
      ...original.apiClient,
      setDeviceVolume: vi.fn(),
      connectDevice: vi.fn(),
      disconnectDevice: vi.fn(),
      removeDevice: vi.fn(),
    },
  };
});

const connectedDevice: DeviceInfo = {
  path: '/org/bluez/hci0/dev_AA_BB_CC_DD_EE_01',
  adapter_name: 'hci0',
  address: 'aa:bb:cc:dd:ee:01',
  name: 'Kitchen Speaker',
  alias: 'Kitchen Speaker',
  paired: true,
  trusted: true,
  connected: true,
  rssi: -50,
  is_audio_sink: true,
  device_type: 'Speaker',
};

describe('SpeakerCard', () => {
  beforeEach(() => {
    vi.mocked(apiClient.setDeviceVolume).mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('renders device name and connected badge', () => {
    render(<SpeakerCard device={connectedDevice} onSettingsClick={() => {}} onRefresh={() => {}} />);
    expect(screen.getByText('Kitchen Speaker')).toBeInTheDocument();
    expect(screen.getByText('Connected')).toBeInTheDocument();
  });

  it('keeps a switched-off speaker visible with an offline badge', () => {
    // The bridge keeps a trusted speaker that BlueZ no longer reports, marked
    // detached: the card must stay (with an offline badge) instead of vanishing.
    render(
      <SpeakerCard
        device={{ ...connectedDevice, connected: false, detached: true }}
        onSettingsClick={() => {}}
        onRefresh={() => {}}
      />
    );
    expect(screen.getByText('Kitchen Speaker')).toBeInTheDocument();
    expect(screen.getByText('Offline')).toBeInTheDocument();
    expect(screen.queryByText('Disconnected')).not.toBeInTheDocument();
  });

  it('commits volume to the bridge on pointer release', async () => {
    render(<SpeakerCard device={connectedDevice} onSettingsClick={() => {}} onRefresh={() => {}} />);
    const slider = screen.getByRole('slider', { name: 'Speaker volume' });
    fireEvent.change(slider, { target: { value: '42' } });
    fireEvent.pointerUp(slider);

    await waitFor(() => {
      expect(apiClient.setDeviceVolume).toHaveBeenCalledWith('aa:bb:cc:dd:ee:01', 42);
    });
  });

  it('follows a volume change made elsewhere, e.g. from Home Assistant', () => {
    // The dashboard slider must track the bridge, whichever surface changed the
    // volume: HA updates the media_player, the bridge publishes playback_updated.
    const { rerender } = render(
      <SpeakerCard device={connectedDevice} onSettingsClick={() => {}} onRefresh={() => {}} />
    );
    const slider = screen.getByRole('slider', { name: 'Speaker volume' });
    expect(slider).toHaveValue('70');

    rerender(
      <SpeakerCard
        device={{ ...connectedDevice, playback: { state: 'idle', volume: 0.42 } }}
        onSettingsClick={() => {}}
        onRefresh={() => {}}
      />
    );

    expect(slider).toHaveValue('42');
  });

  it('surfaces an error message when the volume commit fails', async () => {
    vi.mocked(apiClient.setDeviceVolume).mockRejectedValue(new Error('bridge offline'));
    render(<SpeakerCard device={connectedDevice} onSettingsClick={() => {}} onRefresh={() => {}} />);
    const slider = screen.getByRole('slider', { name: 'Speaker volume' });
    fireEvent.change(slider, { target: { value: '10' } });
    fireEvent.pointerUp(slider);

    await waitFor(() => {
      expect(screen.getByText('bridge offline')).toBeInTheDocument();
    });
  });
});
