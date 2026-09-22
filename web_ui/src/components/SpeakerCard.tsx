import React, { useState } from 'react';
import { DeviceInfo, apiClient } from '../api/client';
import { Volume2, Bluetooth, Power, Trash2, Settings, AlertCircle } from 'lucide-react';

interface SpeakerCardProps {
  device: DeviceInfo;
  onSettingsClick: (device: DeviceInfo) => void;
  onRefresh: () => void;
}

export const SpeakerCard: React.FC<SpeakerCardProps> = ({ device, onSettingsClick, onRefresh }) => {
  const [loading, setLoading] = useState(false);
  const [volume, setVolume] = useState(70);
  const [showConfirmRemove, setShowConfirmRemove] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handleConnectToggle = async () => {
    setLoading(true);
    setErrorMsg(null);
    try {
      if (device.connected) {
        await apiClient.disconnectDevice(device.address);
      } else {
        await apiClient.connectDevice(device.address);
      }
      onRefresh();
    } catch (err: any) {
      setErrorMsg(err?.message || 'Connection toggle failed');
    } finally {
      setLoading(false);
    }
  };

  const handleRemove = async () => {
    setLoading(true);
    setErrorMsg(null);
    try {
      await apiClient.removeDevice(device.address);
      setShowConfirmRemove(false);
      onRefresh();
    } catch (err: any) {
      setErrorMsg(err?.message || 'Failed to remove speaker');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-2xl p-5 shadow-lg flex flex-col justify-between hover:border-slate-600 transition-all">
      <div>
        <div className="flex items-start justify-between">
          <div className="flex items-center space-x-3">
            <div className={`p-3 rounded-xl ${device.connected ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-700 text-slate-400'}`}>
              <Bluetooth className="w-6 h-6" />
            </div>
            <div>
              <h3 className="font-semibold text-lg text-slate-100">{device.alias || device.name || device.address}</h3>
              <p className="text-xs text-slate-400 flex items-center space-x-2">
                <span className="font-mono text-slate-300">{device.address}</span>
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

        {/* Device Type & Auto-Reconnect Tag */}
        <div className="mt-4 flex items-center space-x-2 text-xs">
          <span className="bg-slate-900/80 px-2.5 py-1 rounded-lg text-slate-300 border border-slate-700">
            {device.device_type}
          </span>
          {device.trusted && (
            <span className="bg-blue-950/80 px-2.5 py-1 rounded-lg text-blue-300 border border-blue-800">
              Auto-Reconnect
            </span>
          )}
        </div>

        {errorMsg && (
          <div className="mt-3 p-2 bg-rose-950/80 border border-rose-800 rounded-lg text-rose-300 text-xs flex items-center gap-1.5">
            <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />
            <span>{errorMsg}</span>
          </div>
        )}
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

      {/* Inline Confirmation for Remove */}
      {showConfirmRemove ? (
        <div className="mt-5 pt-3 border-t border-slate-700/80 flex items-center justify-between gap-2">
          <span className="text-xs text-rose-300 font-medium">Remove speaker?</span>
          <div className="flex items-center space-x-2">
            <button
              onClick={() => setShowConfirmRemove(false)}
              disabled={loading}
              className="px-3 py-1.5 text-xs text-slate-400 hover:text-white rounded-lg transition"
            >
              Cancel
            </button>
            <button
              onClick={handleRemove}
              disabled={loading}
              className="px-3 py-1.5 bg-rose-600 hover:bg-rose-500 text-white text-xs font-semibold rounded-lg shadow transition"
            >
              {loading ? 'Removing...' : 'Confirm Remove'}
            </button>
          </div>
        </div>
      ) : (
        /* Action Footer */
        <div className="mt-5 pt-3 border-t border-slate-700 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center space-x-2">
            {device.connected ? (
              <button
                onClick={handleConnectToggle}
                disabled={loading}
                className="px-3.5 py-2 text-xs font-semibold rounded-xl flex items-center space-x-1.5 bg-amber-500/10 border border-amber-500/30 text-amber-300 hover:bg-amber-500/20 transition-colors disabled:opacity-50"
              >
                <Power className="w-3.5 h-3.5" />
                <span>{loading ? 'Disconnecting...' : 'Disconnect'}</span>
              </button>
            ) : (
              <button
                onClick={handleConnectToggle}
                disabled={loading}
                className="px-3.5 py-2 text-xs font-semibold rounded-xl flex items-center space-x-1.5 bg-blue-600 hover:bg-blue-500 text-white transition-colors disabled:opacity-50 shadow-md shadow-blue-600/20"
              >
                <Power className="w-3.5 h-3.5" />
                <span>{loading ? 'Connecting...' : 'Connect'}</span>
              </button>
            )}

            <button
              onClick={() => setShowConfirmRemove(true)}
              className="px-3 py-2 text-xs font-medium rounded-xl flex items-center space-x-1.5 bg-rose-500/10 border border-rose-500/30 text-rose-300 hover:bg-rose-500/20 transition-colors"
              title="Remove speaker"
            >
              <Trash2 className="w-3.5 h-3.5 text-rose-400" />
              <span>Remove</span>
            </button>
          </div>

          <div className="flex items-center space-x-1 text-slate-400">
            <button
              onClick={() => onSettingsClick(device)}
              className="px-2.5 py-2 text-xs font-medium bg-slate-700/60 hover:bg-slate-700 hover:text-slate-200 border border-slate-600/50 rounded-xl transition-colors flex items-center space-x-1"
              title="Settings"
            >
              <Settings className="w-3.5 h-3.5" />
              <span>Settings</span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
};
