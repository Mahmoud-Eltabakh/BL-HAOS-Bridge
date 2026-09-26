import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { SettingsModal } from './SettingsModal';
import { apiClient, DeviceInfo } from '../api/client';

vi.mock('../api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/client')>();
  return {
    ...original,
    apiClient: {
      ...original.apiClient,
      getSettings: vi.fn(),
      updateSpeaker: vi.fn(),
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
  playback: { state: 'idle', volume: 0.35 },
};

const offlineDevice: DeviceInfo = {
  ...connectedDevice,
  connected: false,
  detached: true,
  playback: null,
};

describe('SettingsModal', () => {
  beforeEach(() => {
    vi.mocked(apiClient.getSettings).mockResolvedValue({ speakers: {} } as any);
    vi.mocked(apiClient.updateSpeaker).mockResolvedValue({} as any);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('opens the volume slider at the level the speaker is really at', () => {
    // The bridge reads the sink's real volume back from the audio server, so the
    // settings slider shows the speaker's actual level instead of a fixed 70%.
    render(
      <SettingsModal
        isOpen
        onClose={() => {}}
        selectedDevice={connectedDevice}
        adapters={[]}
        onSaved={() => {}}
      />
    );

    expect(screen.getByRole('slider', { name: 'Speaker volume setting' })).toHaveValue('35');
  });

  it('shows the stored codec pin and saves the operator pick', async () => {
    // The per-speaker codec is how an operator answers "it breaks up / the quality
    // changes": pin something steadier than the negotiated codec.
    vi.mocked(apiClient.getSettings).mockResolvedValue({
      speakers: { [connectedDevice.address]: { default_volume: 70, codec_override: 'ldac' } },
    } as any);

    render(
      <SettingsModal
        isOpen
        onClose={() => {}}
        selectedDevice={connectedDevice}
        adapters={[]}
        onSaved={() => {}}
      />
    );

    const codecSelect = screen.getByRole('combobox', { name: 'Audio Codec' });
    await waitFor(() => expect(codecSelect).toHaveValue('ldac'));

    fireEvent.change(codecSelect, { target: { value: 'sbc_xq' } });
    fireEvent.click(screen.getByRole('button', { name: /save settings/i }));

    await waitFor(() => {
      expect(apiClient.updateSpeaker).toHaveBeenCalledWith(
        connectedDevice.address,
        expect.objectContaining({ codec_override: 'sbc_xq' })
      );
    });
  });

  it('falls back to the stored startup volume for a switched-off speaker', async () => {
    vi.mocked(apiClient.getSettings).mockResolvedValue({
      speakers: { [offlineDevice.address]: { default_volume: 55 } },
    } as any);

    render(
      <SettingsModal
        isOpen
        onClose={() => {}}
        selectedDevice={offlineDevice}
        adapters={[]}
        onSaved={() => {}}
      />
    );

    await waitFor(() => {
      expect(screen.getByRole('slider', { name: 'Speaker volume setting' })).toHaveValue('55');
    });
  });

  it('saves the level the operator picked so the bridge can apply it', async () => {
    render(
      <SettingsModal
        isOpen
        onClose={() => {}}
        selectedDevice={connectedDevice}
        adapters={[]}
        onSaved={() => {}}
      />
    );

    const slider = screen.getByRole('slider', { name: 'Speaker volume setting' });
    fireEvent.change(slider, { target: { value: '42' } });
    fireEvent.click(screen.getByRole('button', { name: /save settings/i }));

    await waitFor(() => {
      expect(apiClient.updateSpeaker).toHaveBeenCalledWith(
        connectedDevice.address,
        expect.objectContaining({ default_volume: 42 })
      );
    });
  });
});
