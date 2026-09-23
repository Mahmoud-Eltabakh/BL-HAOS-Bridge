import React, { useState, useEffect, useRef } from 'react';
import { DeviceInfo, AdapterInfo, apiClient } from '../api/client';
import { X, Sliders, Save, Volume2, AlertCircle } from 'lucide-react';

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
  const [saving, setSaving] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (isOpen) {
      previousFocusRef.current = document.activeElement as HTMLElement;
      closeButtonRef.current?.focus();
    }
    if (selectedDevice) {
      setAlias(selectedDevice.alias || selectedDevice.name || '');
      setAutoReconnect(selectedDevice.trusted);
      setPreferredAdapter(selectedDevice.adapter_name || 'hci0');
      setErrorMsg(null);
    }
  }, [selectedDevice]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) {
        onClose();
      }
      if (e.key === 'Tab' && isOpen) {
        const dialog = document.getElementById('settings-dialog');
        const focusable = dialog ? Array.from(dialog.querySelectorAll<HTMLElement>('button, input, select, [tabindex]:not([tabindex="-1"])')) : [];
        if (focusable.length && ((e.target === focusable[0] && e.shiftKey) || (e.target === focusable[focusable.length - 1] && !e.shiftKey))) {
          e.preventDefault();
          focusable[e.shiftKey ? focusable.length - 1 : 0].focus();
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
      if (isOpen) previousFocusRef.current?.focus();
    };
  }, [isOpen, onClose]);

  if (!isOpen || !selectedDevice) return null;

  const handleSave = async () => {
    setSaving(true);
    setErrorMsg(null);
    try {
      await apiClient.updateSpeaker(selectedDevice.address, {
        custom_alias: alias.trim() || undefined,
        auto_reconnect: autoReconnect,
        preferred_adapter: preferredAdapter,
        default_volume: defaultVolume,
      });
      onSaved();
      onClose();
    } catch (err: any) {
      setErrorMsg(err?.message || 'Failed to save settings');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4 animate-in fade-in duration-150"
      onClick={(e) => e.target === e.currentTarget && onClose()}
      role="dialog"
      aria-modal="true"
      aria-labelledby="settings-dialog-title"
      id="settings-dialog"
    >
      <div className="bg-slate-800 border border-slate-700 rounded-2xl w-full max-w-lg overflow-hidden shadow-2xl">
        <div className="p-5 border-b border-slate-700 flex items-center justify-between bg-slate-900/50">
          <div className="flex items-center space-x-3">
            <Sliders className="w-5 h-5 text-blue-400" />
            <h3 id="settings-dialog-title" className="font-bold text-white text-base">Speaker Settings</h3>
          </div>
          <button
            ref={closeButtonRef}
            onClick={onClose}
            className="p-2 text-slate-400 hover:text-white rounded-lg hover:bg-slate-700 transition"
            aria-label="Close settings"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {errorMsg && (
          <div className="p-3 bg-rose-950/80 border-b border-rose-800 text-rose-300 text-xs flex items-center gap-2">
            <AlertCircle className="w-4 h-4 flex-shrink-0" />
            <span>{errorMsg}</span>
          </div>
        )}

        <div className="p-6 space-y-4">
          <div>
            <label className="block text-xs font-semibold text-slate-300 mb-1.5">Custom Speaker Name</label>
            <input
              type="text"
              value={alias}
              onChange={(e) => setAlias(e.target.value)}
              placeholder="e.g. Living Room Speaker"
              className="w-full bg-slate-900 border border-slate-700 rounded-xl px-3.5 py-2 text-sm text-slate-100 focus:outline-none focus:border-blue-500"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-300 mb-1.5">Bluetooth Adapter</label>
            <select
              value={preferredAdapter}
              onChange={(e) => setPreferredAdapter(e.target.value)}
              className="w-full bg-slate-900 border border-slate-700 rounded-xl px-3.5 py-2 text-sm text-slate-100 focus:outline-none focus:border-blue-500"
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
              <p className="text-sm font-medium text-slate-200">Auto-Reconnect</p>
              <p className="text-xs text-slate-400">Automatically reconnect when speaker turns on or is in range</p>
            </div>
            <input
              type="checkbox"
              checked={autoReconnect}
              onChange={(e) => setAutoReconnect(e.target.checked)}
              className="w-5 h-5 rounded bg-slate-900 border-slate-700 text-blue-600 focus:ring-0 cursor-pointer"
            />
          </div>
        </div>

        <div className="p-4 bg-slate-900/50 border-t border-slate-700 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-4 py-2 text-xs font-medium text-slate-300 hover:text-white rounded-xl hover:bg-slate-700 transition"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-5 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-xs font-semibold rounded-xl flex items-center space-x-1.5 shadow-lg shadow-blue-600/20 transition"
          >
            <Save className="w-3.5 h-3.5" />
            <span>{saving ? 'Saving...' : 'Save Settings'}</span>
          </button>
        </div>
      </div>
    </div>
  );
};
