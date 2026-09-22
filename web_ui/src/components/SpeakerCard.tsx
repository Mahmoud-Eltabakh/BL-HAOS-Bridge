import React, { useState } from 'react';
import { DeviceInfo, apiClient } from '../api/client';
import { Volume2, Bluetooth, Power, Trash2, Settings } from 'lucide-react';

interface SpeakerCardProps {
  device: DeviceInfo;
  onSettingsClick: (device: DeviceInfo) => void;
  onRefresh: () => void;
}

export const SpeakerCard: React.FC<SpeakerCardProps> = ({ device, onSettingsClick, onRefresh }) => {
  const [loading, setLoading] = useState(false);
  const [volume, setVolume] = useState(70);

  const handleConnectToggle = async () => {
    setLoading(true);
    try {
      if (device.connected) {
        await apiClient.disconnectDevice(device.address);
      } else {
        await apiClient.connectDevice(device.address);
      }
      onRefresh();
    } catch (err) {
      alert(`Error toggling connection: ${err}`);
    } finally {
      setLoading(false);
    }
  };

  const handleRemove = async () => {
    if (confirm(`Remove and unpair "${device.alias || device.name}"?`)) {
      await apiClient.removeDevice(device.address);
      onRefresh();
    }
  };

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-xl p-5 shadow-lg flex flex-col justify-between hover:border-slate-600 transition-all">
      <div>
        <div className="flex items-start justify-between">
          <div className="flex items-center space-x-3">
            <div className={`p-3 rounded-lg ${device.connected ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-700 text-slate-400'}`}>
              <Bluetooth className="w-6 h-6" />
            </div>
            <div>
              <h3 className="font-semibold text-lg text-slate-100">{device.alias || device.name || device.address}</h3>
              <p className="text-xs text-slate-400 flex items-center space-x-2">
                <span>{device.address}</span>
                <span>•</span>
                <span>{device.adapter_name}</span>
                {device.rssi && <span>• {device.rssi} dBm</span>}
              </p>
            </div>
          </div>
          <span className={`px-2.5 py-1 text-xs font-medium rounded-full ${device.connected ? 'bg-emerald-950 text-emerald-300 border border-emerald-800' : 'bg-slate-700 text-slate-300'}`}>
            {device.connected ? 'Connected' : 'Disconnected'}
          </span>
        </div>

        {/* Device Type & Codec */}
        <div className="mt-4 flex items-center space-x-2 text-xs">
          <span className="bg-slate-900/80 px-2.5 py-1 rounded-md text-slate-300 border border-slate-700">
            {device.device_type}
          </span>
          {device.trusted && (
            <span className="bg-blue-950/80 px-2.5 py-1 rounded-md text-blue-300 border border-blue-800">
              Auto-Reconnect
            </span>
          )}
        </div>
      </div>

      {/* Volume slider when connected */}
      {device.connected && (
        <div className="mt-5 pt-4 border-t border-slate-700/60">
          <div className="flex items-center justify-between text-xs text-slate-400 mb-2">
            <span className="flex items-center gap-1.5"><Volume2 className="w-4 h-4 text-slate-400" /> Volume</span>
            <span className="font-mono text-slate-200">{volume}%</span>
          </div>
          <input
            type="range"
            min="0"
            max="100"
            value={volume}
            onChange={(e) => setVolume(Number(e.target.value))}
            className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
          />
        </div>
      )}

      {/* Action Footer */}
      <div className="mt-5 pt-3 border-t border-slate-700 flex items-center justify-between">
        <button
          onClick={handleConnectToggle}
          disabled={loading}
          className={`px-4 py-2 text-sm font-medium rounded-lg flex items-center space-x-2 transition-colors ${
            device.connected
              ? 'bg-slate-700 hover:bg-slate-600 text-rose-300'
              : 'bg-blue-600 hover:bg-blue-500 text-white'
          }`}
        >
          <Power className="w-4 h-4" />
          <span>{device.connected ? 'Disconnect' : 'Connect'}</span>
        </button>

        <div className="flex items-center space-x-1 text-slate-400">
          <button
            onClick={() => onSettingsClick(device)}
            className="p-2 hover:bg-slate-700 hover:text-slate-200 rounded-lg transition-colors"
            title="Speaker Settings"
          >
            <Settings className="w-4 h-4" />
          </button>
          <button
            onClick={handleRemove}
            className="p-2 hover:bg-rose-900/40 hover:text-rose-400 rounded-lg transition-colors"
            title="Remove Device"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
};
