/**
 * Dynamic Ingress URL Resolver and REST Client
 */

export function getBasePath(): string {
  // Strips trailing slash from current path
  return window.location.pathname.replace(/\/+$/, '');
}

export function getApiUrl(endpoint: string): string {
  const base = getBasePath();
  const cleanEndpoint = endpoint.startsWith('/') ? endpoint : `/${endpoint}`;
  return `${base}${cleanEndpoint}`;
}

export function getWebSocketUrl(): string {
  const isSecure = window.location.protocol === 'https:';
  const proto = isSecure ? 'wss:' : 'ws:';
  const base = getBasePath();
  return `${proto}//${window.location.host}${base}/ws`;
}

export interface AdapterInfo {
  path: string;
  interface: string;
  address: string;
  name: string;
  alias: string;
  powered: boolean;
  discovering: boolean;
}

export interface DeviceInfo {
  path: string;
  adapter_name: string;
  address: string;
  name: string | null;
  alias: string;
  icon?: string | null;
  paired: boolean;
  trusted: boolean;
  connected: boolean;
  rssi?: number | null;
  is_audio_sink: boolean;
  device_type: string;
}

export interface NativeDiagnostics {
  bridge_credential_present: boolean;
  bridge_version: number;
  native_transport_ready: boolean;
  native_client_count: number;
  trusted_speaker_count: number;
  connected_trusted_speaker_count: number;
}

export const apiClient = {
  async getHealth() {
    const res = await fetch(getApiUrl('/api/health'));
    return res.json();
  },
  async getNativeDiagnostics(): Promise<NativeDiagnostics> {
    const res = await fetch(getApiUrl('/api/diagnostics/native'));
    if (!res.ok) {
      throw new Error('Native diagnostics are unavailable');
    }
    const payload: unknown = await res.json();
    if (!payload || typeof payload !== 'object') {
      throw new Error('Native diagnostics response is invalid');
    }
    return payload as NativeDiagnostics;
  },
  async getAdapters(): Promise<AdapterInfo[]> {
    const res = await fetch(getApiUrl('/api/adapters'));
    return res.json();
  },
  async setAdapterPower(name: string, powered: boolean) {
    const res = await fetch(getApiUrl(`/api/adapters/${name}/power`), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ powered }),
    });
    return res.json();
  },
  async startScan(adapterName?: string) {
    const res = await fetch(getApiUrl('/api/scan/start'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ adapter_name: adapterName }),
    });
    return res.json();
  },
  async stopScan(adapterName?: string) {
    const res = await fetch(getApiUrl('/api/scan/stop'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ adapter_name: adapterName }),
    });
    return res.json();
  },
  async getDevices(audioOnly = true): Promise<DeviceInfo[]> {
    const res = await fetch(getApiUrl(`/api/devices?audio_only=${audioOnly}`));
    return res.json();
  },
  async pairDevice(address: string, pin = '0000') {
    const res = await fetch(getApiUrl('/api/devices/pair'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ address, pin }),
    });
    return res.json();
  },
  async connectDevice(address: string) {
    const res = await fetch(getApiUrl(`/api/devices/${address}/connect`), {
      method: 'POST',
    });
    return res.json();
  },
  async disconnectDevice(address: string) {
    const res = await fetch(getApiUrl(`/api/devices/${address}/disconnect`), {
      method: 'POST',
    });
    return res.json();
  },
  async removeDevice(address: string) {
    const res = await fetch(getApiUrl(`/api/devices/${address}`), {
      method: 'DELETE',
    });
    return res.json();
  },
  async getSettings() {
    const res = await fetch(getApiUrl('/api/settings'));
    return res.json();
  },
  async updateSpeaker(address: string, settings: any) {
    const res = await fetch(getApiUrl(`/api/settings/speakers/${address}`), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    });
    return res.json();
  },
};
