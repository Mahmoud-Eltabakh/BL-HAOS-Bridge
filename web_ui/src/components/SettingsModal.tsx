import React, { useState, useEffect } from 'react';
import { DeviceInfo, AdapterInfo, apiClient } from '../api/client';
import { X, Sliders, Save, Volume2 } from 'lucide-react';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  selectedDevice: DeviceInfo | null;
  adapters: AdapterInfo[];
  onSaved: () => void;
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  selectedDevice,
  adapters,
  onSaved,
}) => {
  const [alias, setAlias] = useState('');
  const [autoReconnect, setAutoReconnect] = useState(true);
  const [preferredAdapter, setPreferredAdapter] = useState('hci0');
  const [defaultVolume, setDefaultVolume] = useState(70);

  useEffect(() => {
    if (selectedDevice) {
      setAlias(selectedDevice.alias || selectedDevice.name || '');
      setAutoReconnect(selectedDevice.trusted);
      setPreferredAdapter(selectedDevice.adapter_name || 'hci0');
    }
  }, [selectedDevice]);

  if (!isOpen || !selectedDevice) return null;

  const handleSave = async () => {
    try {
      await apiClient.updateSpeaker(selectedDevice.address, {
        custom_alias: alias,
        auto_reconnect: autoReconnect,
        preferred_adapter: preferredAdapter,
        default_volume: defaultVolume,
      });
      alert('Speaker preferences saved!');
      onSaved();
      onClose();
    } catch (err) {
      alert(`Save failed: ${err}`);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4">
      <div className="bg-slate-800 border border-slate-700 rounded-2xl w-full max-w-lg overflow-hidden shadow-2xl">
        <div className="p-5 border-b border-slate-700 flex items-center justify-between bg-slate-900/50">
          <div className="flex items-center space-x-3">
            <Sliders className="w-5 h-5 text-blue-400" />
            <h3 className="font-bold text-white text-base">Speaker Settings</h3>
          </div>
          <button onClick={onClose} className="p-2 text-slate-400 hover:text-white rounded-lg">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-6 space-y-4">
          <div>
            <label className="block text-xs font-semibold text-slate-300 mb-1.5">Custom Speaker Name / Alias</label>
            <input
              type="text"
              value={alias}
              onChange={(e) => setAlias(e.target.value)}
              placeholder="e.g. Living Room Speaker"
              className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3.5 py-2 text-sm text-slate-100 focus:outline-none focus:border-blue-500"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-300 mb-1.5">Preferred Bluetooth Adapter</label>
            <select
              value={preferredAdapter}
              onChange={(e) => setPreferredAdapter(e.target.value)}
              className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3.5 py-2 text-sm text-slate-100 focus:outline-none focus:border-blue-500"
            >
              {adapters.map((ad) => (
                <option key={ad.interface} value={ad.interface}>
                  {ad.interface} ({ad.alias || ad.name})
                </option>
              ))}
            </select>
          </div>

          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="text-xs font-semibold text-slate-300 flex items-center gap-1.5">
                <Volume2 className="w-3.5 h-3.5 text-slate-400" /> Default Startup Volume
              </label>
              <span className="text-xs font-mono text-slate-200">{defaultVolume}%</span>
            </div>
            <input
              type="range"
              min="0"
              max="100"
              value={defaultVolume}
              onChange={(e) => setDefaultVolume(Number(e.target.value))}
              className="w-full h-2 bg-slate-900 rounded-lg appearance-none cursor-pointer accent-blue-500"
            />
          </div>

          <div className="flex items-center justify-between pt-2">
            <div>
              <p className="text-sm font-medium text-slate-200">Aggressive Auto-Reconnect</p>
              <p className="text-xs text-slate-400">Automatically restore connection when speaker wakes from standby</p>
            </div>
            <input
              type="checkbox"
              checked={autoReconnect}
              onChange={(e) => setAutoReconnect(e.target.checked)}
              className="w-5 h-5 rounded bg-slate-900 border-slate-700 text-blue-600 focus:ring-0 cursor-pointer"
            />
          </div>
        </div>

        <div className="p-4 border-t border-slate-700 bg-slate-900/50 flex justify-end space-x-3">
          <button onClick={onClose} className="px-4 py-2 text-xs font-medium text-slate-400 hover:text-white rounded-lg">
            Cancel
          </button>
          <button
            onClick={handleSave}
            className="px-5 py-2 bg-blue-600 hover:bg-blue-500 text-white text-xs font-medium rounded-lg flex items-center space-x-1.5"
          >
            <Save className="w-4 h-4" />
            <span>Save Changes</span>
          </button>
        </div>
      </div>
    </div>
  );
};
