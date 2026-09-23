import { useEffect, useState, useCallback, useRef } from 'react';
import { getWebSocketUrl, apiClient, AdapterInfo, DeviceInfo } from '../api/client';

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
      setError('Bluetooth state is unavailable. Check the bridge connection and retry.');
    }
  }, []);

  useEffect(() => {
    let unmounted = false;
    let retryDelay = 1000;

    const connectWs = () => {
      if (unmounted) return;
      const wsUrl = getWebSocketUrl();
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        if (unmounted) return;
        setWsConnected(true);
        retryDelay = 1000;
        void refreshData();
      };

      ws.onclose = () => {
        if (unmounted) return;
        setWsConnected(false);
        // Exponential backoff reconnect up to 10s
        reconnectTimeoutRef.current = setTimeout(() => {
          retryDelay = Math.min(10000, retryDelay * 1.5);
          connectWs();
        }, retryDelay);
      };

      ws.onerror = () => {
        ws.close();
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.event === 'device_discovered' || msg.event === 'device_updated') {
            const dev = msg.data as DeviceInfo;
            setDevices((prev) => {
              const index = prev.findIndex((d) => d.address === dev.address);
              if (index >= 0) {
                const copy = [...prev];
                copy[index] = dev;
                return copy;
              }
              return [...prev, dev];
            });
          } else if (msg.event === 'device_removed') {
            const removedPath = msg.data as string;
            setDevices((prev) => prev.filter((d) => d.path !== removedPath));
          } else if (msg.event === 'adapter_updated' || msg.event === 'adapter_added') {
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

  return {
    adapters,
    devices,
    isScanning,
    wsConnected,
    error,
    refreshData,
  };
}
