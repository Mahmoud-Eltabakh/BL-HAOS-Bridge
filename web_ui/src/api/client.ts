/**
 * Dynamic Ingress URL Resolver and REST Client
 */

import {
  API_ADAPTERS,
  API_DIAGNOSTICS_NATIVE,
  API_HEALTH,
  API_SCAN_START,
  API_SCAN_STOP,
  API_SETTINGS,
  API_DEVICES_PAIR,
  DEFAULT_PIN,
  ERROR_MESSAGES,
  HTTP_NOT_FOUND,
  HTTP_SERVER_ERROR,
  HTTP_UNAUTHORIZED,
  JSON_CONTENT_TYPE,
  PAYLOAD_DETAIL_KEY,
  WS_PUBLIC_PATH,
  apiAdapterPower,
  apiDevice,
  apiDeviceConnect,
  apiDeviceDisconnect,
  apiDeviceVolume,
  apiDevices,
  apiSpeakerSettings,
} from '../constants';

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
  return `${proto}//${window.location.host}${base}${WS_PUBLIC_PATH}`;
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

export interface PlaybackInfo {
  state: string;
  /** Sink volume as a 0-1 ratio; the card renders it as a percentage. */
  volume?: number | null;
  position?: number | null;
  duration?: number | null;
  position_updated_at?: number | null;
  title?: string | null;
  artist?: string | null;
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
  /**
   * The record outlived its BlueZ object: the speaker is still the operator's,
   * but BlueZ no longer exposes it (typically switched off). The card stays and
   * shows "Offline" instead of vanishing.
   */
  detached?: boolean;
  rssi?: number | null;
  is_audio_sink: boolean;
  device_type: string;
  last_seen?: number | null;
  /** Live playback state for a connected audio sink (bridge-reported). */
  playback?: PlaybackInfo | null;
}

export interface NativeDiagnostics {
  bridge_credential_present: boolean;
  bridge_version: number;
  native_transport_ready: boolean;
  native_client_count: number;
  trusted_speaker_count: number;
  connected_trusted_speaker_count: number;
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
    const detail = typeof payload?.[PAYLOAD_DETAIL_KEY] === 'string' ? payload.detail.trim() : '';
    const message =
      detail ||
      (res.status === HTTP_UNAUTHORIZED
        ? ERROR_MESSAGES.authenticationRequired
        : res.status === HTTP_NOT_FOUND
          ? ERROR_MESSAGES.resourceNotFound
          : res.status >= HTTP_SERVER_ERROR
            ? ERROR_MESSAGES.bridgeUnavailable
            : ERROR_MESSAGES.requestFailed);
    const error = new Error(message) as Error & { status?: number };
    error.status = res.status;
    throw error;
  }
  return payload as T;
}

export const apiClient = {
  async getHealth() {
    return requestJson(getApiUrl(API_HEALTH));
  },
  async getNativeDiagnostics(): Promise<NativeDiagnostics> {
    const payload = await requestJson<NativeDiagnostics>(getApiUrl(API_DIAGNOSTICS_NATIVE));
    if (!payload || typeof payload !== 'object') {
      throw new Error(ERROR_MESSAGES.nativeDiagnosticsUnavailable);
    }
    return payload;
  },
  async getAdapters(): Promise<AdapterInfo[]> {
    return requestJson<AdapterInfo[]>(getApiUrl(API_ADAPTERS));
  },
  async setAdapterPower(name: string, powered: boolean) {
    return requestJson(getApiUrl(apiAdapterPower(name)), {
      method: 'POST',
      headers: { 'Content-Type': JSON_CONTENT_TYPE },
      body: JSON.stringify({ powered }),
    });
  },
  async startScan(adapterName?: string) {
    return requestJson(getApiUrl(API_SCAN_START), {
      method: 'POST',
      headers: { 'Content-Type': JSON_CONTENT_TYPE },
      body: JSON.stringify({ adapter_name: adapterName }),
    });
  },
  async stopScan(adapterName?: string) {
    return requestJson(getApiUrl(API_SCAN_STOP), {
      method: 'POST',
      headers: { 'Content-Type': JSON_CONTENT_TYPE },
      body: JSON.stringify({ adapter_name: adapterName }),
    });
  },
  async getDevices(audioOnly = true): Promise<DeviceInfo[]> {
    return requestJson<DeviceInfo[]>(getApiUrl(apiDevices(audioOnly)));
  },
  async pairDevice(address: string, pin = DEFAULT_PIN) {
    return requestJson(getApiUrl(API_DEVICES_PAIR), {
      method: 'POST',
      headers: { 'Content-Type': JSON_CONTENT_TYPE },
      body: JSON.stringify({ address, pin }),
    });
  },
  async connectDevice(address: string) {
    return requestJson(getApiUrl(apiDeviceConnect(address)), {
      method: 'POST',
    });
  },
  async disconnectDevice(address: string) {
    return requestJson(getApiUrl(apiDeviceDisconnect(address)), {
      method: 'POST',
    });
  },
  async setDeviceVolume(address: string, volume: number) {
    return requestJson(getApiUrl(apiDeviceVolume(address)), {
      method: 'POST',
      headers: { 'Content-Type': JSON_CONTENT_TYPE },
      body: JSON.stringify({ volume }),
    });
  },
  async removeDevice(address: string) {
    return requestJson(getApiUrl(apiDevice(address)), {
      method: 'DELETE',
    });
  },
  async getSettings() {
    return requestJson(getApiUrl(API_SETTINGS));
  },
  async updateSpeaker(address: string, settings: any) {
    return requestJson(getApiUrl(apiSpeakerSettings(address)), {
      method: 'PUT',
      headers: { 'Content-Type': JSON_CONTENT_TYPE },
      body: JSON.stringify(settings),
    });
  },
};
