import { useEffect, useState, useCallback, useRef } from 'react';
import { getWebSocketUrl, apiClient, AdapterInfo, DeviceInfo } from '../api/client';

export function useBluetoothEvents() {
  const [adapters, setAdapters] = useState<AdapterInfo[]>([]);
  const [devices, setDevices] = useState<DeviceInfo[]>([]);
  const [isScanning, setIsScanning] = useState(false);
  const [wsConnected, setWsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  const refreshData = useCallback(async () => {
    try {
      const [adapterList, deviceList] = await Promise.all([
        apiClient.getAdapters(),
        apiClient.getDevices(false),
      ]);
      setAdapters(adapterList);
      setDevices(deviceList);
      setIsScanning(adapterList.some((a) => a.discovering));
    } catch (e) {
      console.error('Failed to load Bluetooth state', e);
    }
  }, []);

  useEffect(() => {
    refreshData();

    // Setup WebSocket live stream
    const wsUrl = getWebSocketUrl();
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setWsConnected(true);
    };

    ws.onclose = () => {
      setWsConnected(false);
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

    return () => {
      ws.close();
    };
  }, [refreshData]);

  return {
    adapters,
    devices,
    isScanning,
    wsConnected,
    refreshData,
  };
}
