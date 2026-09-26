/**
 * Single source of truth for the dashboard's protocol contract.
 *
 * Endpoint paths, the WebSocket path, timeouts, retry policy, payload keys, and
 * operator-facing messages are declared here so no literal is duplicated across
 * the client, hooks, and components.
 */

// --- HTTP / WebSocket contract -------------------------------------------
export const API_PREFIX = '/api';
export const API_HEALTH = `${API_PREFIX}/health`;
export const API_DIAGNOSTICS_NATIVE = '/api/diagnostics/native';
export const API_ADAPTERS = `${API_PREFIX}/adapters`;
export const API_SCAN_START = `${API_PREFIX}/scan/start`;
export const API_SCAN_STOP = `${API_PREFIX}/scan/stop`;
export const API_DEVICES_PAIR = `${API_PREFIX}/devices/pair`;
export const API_SETTINGS = `${API_PREFIX}/settings`;
export const WS_PUBLIC_PATH = '/ws';
export const JSON_CONTENT_TYPE = 'application/json';
export const DEFAULT_PIN = '0000';
export const PAYLOAD_DETAIL_KEY = 'detail';

// --- Parameterised paths -------------------------------------------------
export const apiAdapterPower = (name: string) => `${API_ADAPTERS}/${name}/power`;
export const apiDevices = (audioOnly: boolean) => `${API_PREFIX}/devices?audio_only=${audioOnly}`;
export const apiDeviceConnect = (address: string) => `${API_PREFIX}/devices/${address}/connect`;
export const apiDeviceDisconnect = (address: string) => `${API_PREFIX}/devices/${address}/disconnect`;
export const apiDeviceVolume = (address: string) => `${API_PREFIX}/devices/${address}/volume`;
export const apiDevice = (address: string) => `${API_PREFIX}/devices/${address}`;
export const apiSpeakerSettings = (address: string) => `${API_SETTINGS}/speakers/${address}`;

// --- Transport status codes ---------------------------------------------
export const HTTP_UNAUTHORIZED = 401;
export const HTTP_NOT_FOUND = 404;
export const HTTP_SERVER_ERROR = 500;

// --- Live event vocabulary ----------------------------------------------
export const EVENT_DEVICE_DISCOVERED = 'device_discovered';
export const EVENT_DEVICE_UPDATED = 'device_updated';
export const EVENT_DEVICE_REMOVED = 'device_removed';
export const EVENT_ADAPTER_ADDED = 'adapter_added';
export const EVENT_ADAPTER_UPDATED = 'adapter_updated';
export const EVENT_SPEAKER_UPDATED = 'speaker_updated';
// Live playback state for one speaker. The bridge publishes this for every
// playback change, including ones made from Home Assistant, so the dashboard's
// volume slider follows the media_player entity instead of going stale.
export const EVENT_PLAYBACK_UPDATED = 'playback_updated';

// --- Reconnect / polling policy -----------------------------------------
export const WS_RECONNECT_BASE_DELAY_MS = 1000;
export const WS_RECONNECT_MAX_DELAY_MS = 10000;
export const WS_RECONNECT_MULTIPLIER = 1.5;
export const SCAN_POLL_INTERVAL_MS = 2000;

// --- Operator-facing messages -------------------------------------------
export const ERROR_MESSAGES = {
  nativeDiagnosticsUnavailable: 'Native diagnostics are unavailable',
  authenticationRequired: 'Authentication is required.',
  resourceNotFound: 'The requested Bluetooth resource was not found.',
  bridgeUnavailable: 'The bridge is temporarily unavailable.',
  requestFailed: 'The request could not be completed.',
  bluetoothStateUnavailable:
    'Bluetooth state is unavailable. Check the bridge connection and retry.',
} as const;
