import { useEffect, useState, useCallback, useRef } from 'react';
import { getWebSocketUrl, apiClient, AdapterInfo, DeviceInfo, PlaybackInfo } from '../api/client';
import {
  ERROR_MESSAGES,
  EVENT_ADAPTER_ADDED,
  EVENT_ADAPTER_UPDATED,
  EVENT_DEVICE_DISCOVERED,
  EVENT_DEVICE_REMOVED,
  EVENT_DEVICE_UPDATED,
  EVENT_PLAYBACK_UPDATED,
  SCAN_POLL_INTERVAL_MS,
  WS_RECONNECT_BASE_DELAY_MS,
  WS_RECONNECT_MAX_DELAY_MS,
  WS_RECONNECT_MULTIPLIER,
} from '../constants';

export function useBluetoothEvents() {
  const [adapters, setAdapters] = useState<AdapterInfo[]>([]);
  const [devices, setDevices] = useState<DeviceInfo[]>([]);
  const [isScanning, setIsScanning] = useState(false);
  const [wsConnected, setWsConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refreshData = useCallback(async () => {
    try {
      const [adapterList, deviceList] = await Promise.all([
        apiClient.getAdapters(),
        apiClient.getDevices(false),
      ]);
      setAdapters(adapterList);
      setDevices(deviceList);
      setIsScanning(adapterList.some((a) => a.discovering));
      setError(null);
    } catch (e) {
      setError(ERROR_MESSAGES.bluetoothStateUnavailable);
    }
  }, []);

  useEffect(() => {
    let unmounted = false;
    let retryDelay = WS_RECONNECT_BASE_DELAY_MS;

    const connectWs = () => {
      if (unmounted) return;
      const wsUrl = getWebSocketUrl();
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        if (unmounted) return;
        setWsConnected(true);
        retryDelay = WS_RECONNECT_BASE_DELAY_MS;
        void refreshData();
      };

      ws.onclose = () => {
        if (unmounted) return;
        setWsConnected(false);
        // Exponential backoff reconnect up to the shared ceiling.
        reconnectTimeoutRef.current = setTimeout(() => {
          retryDelay = Math.min(WS_RECONNECT_MAX_DELAY_MS, retryDelay * WS_RECONNECT_MULTIPLIER);
          connectWs();
        }, retryDelay);
      };

      ws.onerror = () => {
        ws.close();
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.event === EVENT_DEVICE_DISCOVERED || msg.event === EVENT_DEVICE_UPDATED) {
            const dev = msg.data as DeviceInfo;
            setDevices((prev) => {
              const index = prev.findIndex((d) => d.address === dev.address);
              if (index >= 0) {
                const copy = [...prev];
                // BlueZ property events carry no playback block; keep the one the
                // bridge last published instead of clearing the card's volume.
                copy[index] = { ...dev, playback: dev.playback ?? prev[index].playback };
                return copy;
              }
              // New device found during a scan: append immediately so the UI
              // shows it in real time instead of waiting for a full refresh.
              return [...prev, dev];
            });
            // A discovered device usually means a scan is running; keep the
            // indicator honest even if the adapter event was missed.
            if (msg.event === EVENT_DEVICE_DISCOVERED) {
              setIsScanning(true);
            }
          } else if (msg.event === EVENT_DEVICE_REMOVED) {
            const removedPath = msg.data as string;
            setDevices((prev) => prev.filter((d) => d.path !== removedPath));
          } else if (msg.event === EVENT_PLAYBACK_UPDATED) {
            // Volume/state changed somewhere else - typically the Home Assistant
            // media_player entity. Patch the matching device so the speaker card's
            // slider follows it instead of showing the stale level.
            const update = msg.data as { address?: string; playback?: PlaybackInfo };
            if (!update?.address || !update.playback) return;
            const target = update.address.trim().toLowerCase();
            setDevices((prev) =>
              prev.map((d) =>
                d.address.trim().toLowerCase() === target ? { ...d, playback: update.playback } : d
              )
            );
          } else if (msg.event === EVENT_ADAPTER_UPDATED || msg.event === EVENT_ADAPTER_ADDED) {
            const adapter = msg.data as AdapterInfo;
            setAdapters((prev) => {
              const index = prev.findIndex((a) => a.interface === adapter.interface);
              if (index >= 0) {
                const copy = [...prev];
                copy[index] = adapter;
                return copy;
              }
              return [...prev, adapter];
            });
            setIsScanning(adapter.discovering);
          }
        } catch (err) {
          console.error('WebSocket payload parse error', err);
        }
      };
    };

    connectWs();

    return () => {
      unmounted = true;
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, [refreshData]);

  useEffect(() => {
    let unmounted = false;
    let pollInterval: ReturnType<typeof setInterval> | null = null;

    if (isScanning) {
      pollInterval = setInterval(async () => {
        try {
          const deviceList = await apiClient.getDevices(false);
          if (!unmounted) {
            // Merge rather than replace: WebSocket events may have delivered
            // fresher records between polls; the poll backfills anything the
            // WS missed (e.g. transient disconnects) without clobbering it.
            setDevices((prev) => {
              const byAddress = new Map(prev.map((d) => [d.address, d]));
              for (const dev of deviceList) {
                byAddress.set(dev.address, dev);
              }
              return [...byAddress.values()];
            });
          }
        } catch (err) {
          console.debug('Failed to poll devices during scan', err);
        }
      }, SCAN_POLL_INTERVAL_MS);
    }

    return () => {
      unmounted = true;
      if (pollInterval) clearInterval(pollInterval);
    };
  }, [isScanning]);

  return {
    adapters,
    devices,
    isScanning,
    wsConnected,
    error,
    refreshData,
  };
}
