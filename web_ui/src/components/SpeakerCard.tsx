import React, { useEffect, useState } from 'react';
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
  const [volumeSaving, setVolumeSaving] = useState(false);
  const [showConfirmRemove, setShowConfirmRemove] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Seed the local volume from the bridge-reported playback volume when the
  // device record changes, so the slider reflects reality after reconnects.
  const playbackVolume = (device as DeviceInfo & { playback?: { volume?: number | null } }).playback?.volume;
  const reportedVolume = device.connected
    ? Math.round((playbackVolume ?? 0.7) * 100)
    : null;
  useEffect(() => {
    if (reportedVolume !== null) setVolume(reportedVolume);
  }, [reportedVolume]);

  const commitVolume = async (next: number) => {
    setVolumeSaving(true);
    setErrorMsg(null);
    try {
      await apiClient.setDeviceVolume(device.address, next);
      onRefresh();
    } catch (err: any) {
      setErrorMsg(err?.message || 'Failed to update volume');
      onRefresh();
    } finally {
      setVolumeSaving(false);
    }
  };

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
    <div className="neu-surface rounded-xl p-4 flex flex-col justify-between transition-all">
      <div>
        <div className="flex items-start justify-between">
          <div className="flex items-center space-x-3">
            <div className={`neu-inset p-3 rounded-xl ${device.connected ? 'text-emerald-400' : 'text-slate-400'}`}>
              <Bluetooth className="w-6 h-6" />
            </div>
            <div>
              <h3 className="font-semibold text-base text-slate-100">{device.alias || device.name || device.address}</h3>
              <p className="text-xs text-slate-400 flex items-center space-x-2">
                <span className="font-mono text-slate-300">{device.address}</span>
                <span>•</span>
                <span>{device.adapter_name}</span>
                {device.rssi && <span>• {device.rssi} dBm</span>}
              </p>
            </div>
          </div>
          <span className={`neu-inset px-2.5 py-1 text-xs font-medium rounded-full ${device.connected ? 'text-emerald-300' : 'text-slate-300'}`}>
            {device.connected ? 'Connected' : 'Disconnected'}
          </span>
        </div>

        {/* Device Type & Auto-Reconnect Tag */}
        <div className="mt-4 flex items-center space-x-2 text-xs">
          <span className="neu-inset px-2.5 py-1 rounded-lg text-slate-300">
            {device.device_type}
          </span>
          {device.trusted && (
            <span className="bg-blue-950/80 px-2.5 py-1 rounded-lg text-blue-300 border border-blue-800">
              Auto-Reconnect
            </span>
          )}
        </div>

        {errorMsg && (
            <div className="neu-inset mt-3 p-2 rounded-lg text-rose-300 text-xs flex items-center gap-1.5">
            <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />
            <span>{errorMsg}</span>
          </div>
        )}
      </div>

      {/* Volume slider when connected */}
      {device.connected && (
        <div className="neu-inset mt-4 rounded-lg p-3">
          <div className="flex items-center justify-between text-xs text-slate-400 mb-2">
            <span className="flex items-center gap-1.5"><Volume2 className="w-4 h-4 text-slate-400" /> Volume</span>
            <span className="font-mono text-slate-200">{volume}%</span>
          </div>
          <input
            type="range"
            min="0"
            max="100"
            value={volume}
            aria-label="Speaker volume"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={volume}
            disabled={volumeSaving}
            onChange={(e) => setVolume(Number(e.target.value))}
            onPointerUp={() => void commitVolume(volume)}
            onKeyUp={() => void commitVolume(volume)}
            className="neu-range w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer"
          />
        </div>
      )}

      {/* Inline Confirmation for Remove */}
      {showConfirmRemove ? (
        <div className="neu-inset mt-4 rounded-lg p-2.5 flex items-center justify-between gap-2">
          <span className="text-xs text-rose-300 font-medium">Remove speaker?</span>
          <div className="flex items-center space-x-2">
            <button
              onClick={() => setShowConfirmRemove(false)}
              disabled={loading}
              className="neu-button px-3 py-1.5 text-xs text-slate-300 hover:text-white rounded-lg"
            >
              Cancel
            </button>
            <button
              onClick={handleRemove}
              disabled={loading}
              className="neu-button px-3 py-1.5 bg-rose-600 hover:bg-rose-500 text-white text-xs font-semibold rounded-lg"
            >
              {loading ? 'Removing...' : 'Confirm Remove'}
            </button>
          </div>
        </div>
      ) : (
        /* Action Footer */
        <div className="neu-inset mt-4 rounded-lg p-2.5 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center space-x-2">
            {device.connected ? (
              <button
                onClick={handleConnectToggle}
                disabled={loading}
                className="neu-button px-3.5 py-2 text-xs font-semibold rounded-xl flex items-center space-x-1.5 text-amber-300 disabled:opacity-50"
              >
                <Power className="w-3.5 h-3.5" />
                <span>{loading ? 'Disconnecting...' : 'Disconnect'}</span>
              </button>
            ) : (
              <button
                onClick={handleConnectToggle}
                disabled={loading}
                className="neu-button px-3.5 py-2 text-xs font-semibold rounded-xl flex items-center space-x-1.5 bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50"
              >
                <Power className="w-3.5 h-3.5" />
                <span>{loading ? 'Connecting...' : 'Connect'}</span>
              </button>
            )}

            <button
              onClick={() => setShowConfirmRemove(true)}
              className="neu-button px-3 py-2 text-xs font-medium rounded-xl flex items-center space-x-1.5 text-rose-300"
              title="Remove speaker"
            >
              <Trash2 className="w-3.5 h-3.5 text-rose-400" />
              <span>Remove</span>
            </button>
          </div>

          <div className="flex items-center space-x-1 text-slate-400">
            <button
              onClick={() => onSettingsClick(device)}
              className="neu-button px-2.5 py-2 text-xs font-medium text-slate-300 hover:text-slate-200 rounded-xl flex items-center space-x-1"
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
