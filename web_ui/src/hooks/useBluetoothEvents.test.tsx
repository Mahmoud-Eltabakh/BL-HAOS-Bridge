import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { useBluetoothEvents } from './useBluetoothEvents';
import { apiClient, DeviceInfo } from '../api/client';

vi.mock('../api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/client')>();
  return {
    ...original,
    apiClient: {
      ...original.apiClient,
      getAdapters: vi.fn(),
      getDevices: vi.fn(),
    },
  };
});

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;

  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }

  close() {
    /* nothing to release in the fake */
  }

  emit(event: string, data: unknown) {
    this.onmessage?.({ data: JSON.stringify({ event, data }) });
  }
}

const speaker: DeviceInfo = {
  path: '/org/bluez/hci0/dev_AA_BB_CC_DD_EE_01',
  adapter_name: 'hci0',
  address: 'AA:BB:CC:DD:EE:01',
  name: 'Kitchen Speaker',
  alias: 'Kitchen Speaker',
  paired: true,
  trusted: true,
  connected: true,
  is_audio_sink: true,
  device_type: 'Speaker',
  playback: { state: 'idle', volume: 0.7 },
};

describe('useBluetoothEvents playback updates', () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket as unknown as typeof WebSocket);
    vi.mocked(apiClient.getAdapters).mockResolvedValue([]);
    vi.mocked(apiClient.getDevices).mockResolvedValue([speaker]);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it('applies a bridge playback_updated event to the matching speaker', async () => {
    const { result } = renderHook(() => useBluetoothEvents());
    // The hook loads the snapshot when the socket opens, so open it first.
    const socket = FakeWebSocket.instances[0];
    act(() => {
      socket.onopen?.();
    });
    await waitFor(() => expect(result.current.devices).toHaveLength(1));

    act(() => {
      // What the bridge broadcasts after Home Assistant sets the volume. The
      // bridge may report the address in a different case than BlueZ does.
      socket.emit('playback_updated', {
        address: 'aa:bb:cc:dd:ee:01',
        playback: { state: 'playing', volume: 0.25 },
      });
    });

    await waitFor(() => expect(result.current.devices[0].playback?.volume).toBe(0.25));
    expect(result.current.devices[0].playback?.state).toBe('playing');
  });

  it('keeps the known playback state when a BlueZ property event arrives', async () => {
    // Raw device events carry no playback block; a connect/reconnect event must
    // not clear the volume the card is showing.
    const { result } = renderHook(() => useBluetoothEvents());
    const socket = FakeWebSocket.instances[0];
    act(() => {
      socket.onopen?.();
    });
    await waitFor(() => expect(result.current.devices).toHaveLength(1));

    act(() => {
      socket.emit('playback_updated', {
        address: 'aa:bb:cc:dd:ee:01',
        playback: { state: 'playing', volume: 0.25 },
      });
    });
    await waitFor(() => expect(result.current.devices[0].playback?.volume).toBe(0.25));

    act(() => {
      socket.emit('device_updated', { ...speaker, playback: null, rssi: -55 });
    });

    await waitFor(() => expect(result.current.devices[0].rssi).toBe(-55));
    expect(result.current.devices[0].playback?.volume).toBe(0.25);
  });

  it('ignores a playback event for an unknown speaker', async () => {
    const { result } = renderHook(() => useBluetoothEvents());
    const socket = FakeWebSocket.instances[0];
    act(() => {
      socket.onopen?.();
    });
    await waitFor(() => expect(result.current.devices).toHaveLength(1));

    act(() => {
      socket.emit('playback_updated', {
        address: 'aa:bb:cc:dd:ee:ff',
        playback: { state: 'idle', volume: 0.1 },
      });
    });

    expect(result.current.devices).toHaveLength(1);
    expect(result.current.devices[0].playback?.volume).toBe(0.7);
  });
});
