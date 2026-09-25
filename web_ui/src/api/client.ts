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
  last_seen?: number | null;
}

export interface NativeDiagnostics {
  bridge_credential_present: boolean;
  bridge_version: number;
  native_transport_ready: boolean;
  native_client_count: number;
  trusted_speaker_count: number;
  connected_trusted_speaker_count: number;
}

export interface OperatorDiagnostics {
  contract_version: number;
  status: string;
  lifecycle: string;
  demo_mode?: boolean;
  demo_scenario?: string;
  components: Record<string, { state: string; failure_class: string | null }>;
  last_failure: { classification: string; component: string; speaker?: string } | null;
  recent_failures: Array<{ classification: string; component: string; speaker?: string }>;
  event_count: number;
}

export interface RecoveryGuidance {
  failure_class: string;
  diagnosis: string;
  next_steps: string[];
  prerequisites: string[];
  actions: string[];
  expected_outcome: string;
  status: string;
}

export interface RecoveryContract {
  contract_version: number;
  guidance: RecoveryGuidance;
  current_status: string;
  correlation_id: string | null;
}

export interface RecoveryResult {
  action_id: string;
  target: string | null;
  correlation_id: string;
  result: 'succeeded' | 'failed' | 'conflict';
  detail: string;
  diagnostics?: OperatorDiagnostics;
}

async function requestJson<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, options);
  let payload: any;
  try {
    payload = await res.json();
  } catch {
    payload = null;
  }
  if (!res.ok) {
    // Prefer the bridge's own bounded, redacted detail so operators can see the
    // real reason a pairing/connection attempt failed instead of a generic
    // transport message.
    const detail = typeof payload?.detail === 'string' ? payload.detail.trim() : '';
    const message =
      detail ||
      (res.status === 401
        ? 'Authentication is required.'
        : res.status === 404
          ? 'The requested Bluetooth resource was not found.'
          : res.status >= 500
            ? 'The bridge is temporarily unavailable.'
            : 'The request could not be completed.');
    const error = new Error(message) as Error & { status?: number };
    error.status = res.status;
    throw error;
  }
  return payload as T;
}

export const apiClient = {
  async getHealth() {
    return requestJson(getApiUrl('/api/health'));
  },
  async getNativeDiagnostics(): Promise<NativeDiagnostics> {
    const payload = await requestJson<NativeDiagnostics>(getApiUrl('/api/diagnostics/native'));
    if (!payload || typeof payload !== 'object') {
      throw new Error('Native diagnostics are unavailable');
    }
    return payload;
  },
  async getDiagnostics(): Promise<OperatorDiagnostics> {
    return requestJson<OperatorDiagnostics>(getApiUrl('/api/diagnostics'));
  },
  async getRecovery(): Promise<RecoveryContract> {
    return requestJson<RecoveryContract>(getApiUrl('/api/recovery'));
  },
  async executeRecovery(actionId: string, target?: string): Promise<RecoveryResult> {
    return requestJson<RecoveryResult>(getApiUrl('/api/recovery/actions'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action_id: actionId, target }),
    });
  },
  async downloadSupportBundle(): Promise<void> {
    const response = await fetch(getApiUrl('/api/support/bundle'));
    if (!response.ok) throw new Error('Support export is unavailable');
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'bl-haos-support-bundle.json';
    anchor.click();
    URL.revokeObjectURL(url);
  },
  async getAdapters(): Promise<AdapterInfo[]> {
    return requestJson<AdapterInfo[]>(getApiUrl('/api/adapters'));
  },
  async setAdapterPower(name: string, powered: boolean) {
    return requestJson(getApiUrl(`/api/adapters/${name}/power`), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ powered }),
    });
  },
  async startScan(adapterName?: string) {
    return requestJson(getApiUrl('/api/scan/start'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ adapter_name: adapterName }),
    });
  },
  async stopScan(adapterName?: string) {
    return requestJson(getApiUrl('/api/scan/stop'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ adapter_name: adapterName }),
    });
  },
  async getDevices(audioOnly = true): Promise<DeviceInfo[]> {
    return requestJson<DeviceInfo[]>(getApiUrl(`/api/devices?audio_only=${audioOnly}`));
  },
  async pairDevice(address: string, pin = '0000') {
    return requestJson(getApiUrl('/api/devices/pair'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ address, pin }),
    });
  },
  async connectDevice(address: string) {
    return requestJson(getApiUrl(`/api/devices/${address}/connect`), {
      method: 'POST',
    });
  },
  async disconnectDevice(address: string) {
    return requestJson(getApiUrl(`/api/devices/${address}/disconnect`), {
      method: 'POST',
    });
  },
  async setDeviceVolume(address: string, volume: number) {
    return requestJson(getApiUrl(`/api/devices/${address}/volume`), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ volume }),
    });
  },
  async removeDevice(address: string) {
    return requestJson(getApiUrl(`/api/devices/${address}`), {
      method: 'DELETE',
    });
  },
  async getSettings() {
    return requestJson(getApiUrl('/api/settings'));
  },
  async updateSpeaker(address: string, settings: any) {
    return requestJson(getApiUrl(`/api/settings/speakers/${address}`), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    });
  },
};
