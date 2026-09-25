import React, { useState, useEffect, useRef } from 'react';
import { DeviceInfo, apiClient } from '../api/client';
import { X, RefreshCw, Bluetooth, Signal, Plus, Key, Search, Volume2, Radio, Trash2, Power } from 'lucide-react';

interface DiscoveryModalProps {
  isOpen: boolean;
  onClose: () => void;
  devices: DeviceInfo[];
  isScanning: boolean;
  onRefresh: () => void;
}

export const DiscoveryModal: React.FC<DiscoveryModalProps> = ({
  isOpen,
  onClose,
  devices,
  isScanning,
  onRefresh,
}) => {
  const [actionAddress, setActionAddress] = useState<string | null>(null);
  const [pinCode, setPinCode] = useState('0000');
  const [showPinInput, setShowPinInput] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [audioOnlyFilter, setAudioOnlyFilter] = useState(false);
  const [manualMac, setManualMac] = useState('');
  const [statusMessage, setStatusMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  // Auto-start discovery once per open so new devices stream in immediately
  // without requiring the user to press Start Scan first. Declared here with
  // the other hooks: hooks must never sit below the early `return null`.
  const autoStartRef = useRef(false);

  useEffect(() => {
    if (isOpen) {
      previousFocusRef.current = document.activeElement as HTMLElement;
      closeButtonRef.current?.focus();
    }
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) {
        onClose();
      }
      if (e.key === 'Tab' && isOpen) {
        const dialog = document.getElementById('discovery-dialog');
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

  // Auto-start discovery when the modal opens so new devices stream in
  // immediately without requiring the user to press Start Scan first.
  useEffect(() => {
    if (isOpen && !autoStartRef.current) {
      autoStartRef.current = true;
      if (!isScanning) {
        void apiClient.startScan().then(onRefresh).catch(() => {
          // Surface as a non-blocking status; the user can retry manually.
          setStatusMessage({ type: 'error', text: 'Could not start scanning. Try the Start Scan button.' });
        });
      }
    }
    if (!isOpen) {
      autoStartRef.current = false;
    }
  }, [isOpen, isScanning, onRefresh]);

  if (!isOpen) return null;

  const filteredDevices = devices
    .filter((d) => {
      if (audioOnlyFilter && !d.is_audio_sink) return false;
      if (!searchQuery.trim()) return true;

      const query = searchQuery.toLowerCase().trim();
      const macMatch = d.address.toLowerCase().includes(query);
      const nameMatch = d.name ? d.name.toLowerCase().includes(query) : false;
      const aliasMatch = d.alias ? d.alias.toLowerCase().includes(query) : false;

      return macMatch || nameMatch || aliasMatch;
    })
    // Strongest signal first: newly-found nearby devices surface at the top
    // while scanning; unknown RSSI sorts last. Newest discovery breaks ties.
    .sort((a, b) => {
      const rssiA = a.rssi ?? -999;
      const rssiB = b.rssi ?? -999;
      if (rssiA !== rssiB) return rssiB - rssiA;
      return (b.last_seen ?? 0) - (a.last_seen ?? 0);
    });

  const handlePair = async (address: string) => {
    if (!address.trim()) return;
    setActionAddress(address);
    setStatusMessage(null);
    try {
      await apiClient.pairDevice(address.trim(), pinCode);
      setStatusMessage({ type: 'success', text: `Connected to ${address}.` });
      onRefresh();
    } catch (err: any) {
      const msg = err?.message || String(err);
      setStatusMessage({
        type: 'error',
        text: /page timeout|br-connection-page-timeout|no route to host/i.test(msg)
          ? `Could not connect to ${address}. Ensure the speaker is in pairing mode (blinking LED) and try again.`
          : `Connection failed for ${address}: ${msg}`,
      });
    } finally {
      setActionAddress(null);
      setShowPinInput(false);
    }
  };

  const handleDisconnect = async (address: string) => {
    setActionAddress(address);
    setStatusMessage(null);
    try {
      await apiClient.disconnectDevice(address);
      setStatusMessage({ type: 'success', text: `Disconnected from ${address}.` });
      onRefresh();
    } catch (err: any) {
      setStatusMessage({ type: 'error', text: `Disconnect failed: ${err?.message || err}` });
    } finally {
      setActionAddress(null);
    }
  };

  const handleRemove = async (address: string) => {
    if (!address.trim()) return;
    setActionAddress(address);
    setStatusMessage(null);
    try {
      await apiClient.removeDevice(address.trim());
      setStatusMessage({ type: 'success', text: `Removed ${address}.` });
      onRefresh();
    } catch (err: any) {
      setStatusMessage({ type: 'error', text: `Remove failed: ${err?.message || err}` });
    } finally {
      setActionAddress(null);
    }
  };

  const handleToggleScan = async () => {
    if (isScanning) {
      await apiClient.stopScan();
    } else {
      await apiClient.startScan();
    }
    onRefresh();
  };

  return (
    <div
      className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4 animate-in fade-in duration-150"
      onClick={(e) => e.target === e.currentTarget && onClose()}
      role="dialog"
      aria-modal="true"
      aria-labelledby="discovery-dialog-title"
      id="discovery-dialog"
    >
      <div className="neu-surface rounded-xl w-full max-w-2xl overflow-hidden flex flex-col max-h-[85vh]">
        {/* Header */}
        <div className="p-4 flex items-center justify-between border-b neu-hairline">
          <div className="flex items-center space-x-3">
            <div className="p-2.5 bg-blue-600/20 text-blue-400 rounded-xl">
              <Bluetooth className="w-5 h-5" />
            </div>
            <div>
              <h2 id="discovery-dialog-title" className="text-lg font-bold text-white">Add Bluetooth Speaker</h2>
              <p className="text-xs neu-text-muted">Discover and connect nearby Bluetooth audio devices</p>
            </div>
          </div>
          <button
            ref={closeButtonRef}
            onClick={onClose}
              className="neu-button p-2 text-slate-300 hover:text-white rounded-lg"
            aria-label="Close dialog"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Status / Alert Banner */}
        {statusMessage && (
          <div
            className={`p-3.5 text-xs flex items-center justify-between border-b ${
              statusMessage.type === 'success'
                ? 'bg-emerald-950/80 border-emerald-800/80 text-emerald-300'
                : 'bg-rose-950/80 border-rose-800/80 text-rose-300'
            }`}
          >
            <span>{statusMessage.text}</span>
            <button
              onClick={() => setStatusMessage(null)}
              className="p-1 hover:opacity-75 text-slate-300"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        {/* Scan & Search Action Bar */}
        <div className="p-4 space-y-3 border-b neu-hairline">
          <div className="flex flex-wrap items-center justify-between gap-3">
            {/* Live Scan Indicator */}
            <div className="flex items-center space-x-2">
              <span className={`w-2.5 h-2.5 rounded-full ${isScanning ? 'bg-blue-400 animate-ping' : 'bg-slate-500'}`} />
              <span className="text-xs text-slate-300 font-medium">
                {isScanning ? 'Scanning for nearby devices...' : 'Scan idle'}
              </span>
            </div>

            <div className="flex items-center space-x-2">
              {showPinInput ? (
                <div className="neu-inset flex items-center space-x-1.5 rounded-lg px-2 py-1">
                  <Key className="w-3.5 h-3.5 text-slate-400" />
                  <input
                    type="text"
                    value={pinCode}
                    onChange={(e) => setPinCode(e.target.value)}
                    placeholder="PIN"
                    maxLength={6}
                    className="w-16 bg-transparent text-xs text-white focus:outline-none"
                  />
                </div>
              ) : (
                <button
                  onClick={() => setShowPinInput(true)}
                  className="neu-button px-3 py-1.5 text-xs text-slate-300 hover:text-white rounded-lg"
                >
                  Custom PIN
                </button>
              )}

              <button
                onClick={handleToggleScan}
                className={`neu-button px-4 py-1.5 text-xs font-semibold rounded-lg flex items-center space-x-2 ${
                  isScanning ? 'text-slate-200' : 'bg-blue-600 text-white hover:bg-blue-500'
                }`}
              >
                <RefreshCw className={`w-3.5 h-3.5 ${isScanning ? 'animate-spin' : ''}`} />
                <span>{isScanning ? 'Stop Scan' : 'Start Scan'}</span>
              </button>
            </div>
          </div>

          {/* Search Box & Filters */}
          <div className="flex flex-col sm:flex-row items-center gap-2 pt-1">
            <div className="relative flex-1 w-full">
              <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search by MAC address or name (e.g. EC:81 or Logitech)..."
                className="neu-control neu-placeholder w-full rounded-xl pl-9 pr-4 py-2 text-xs text-white focus:outline-none focus:ring-2 focus:ring-blue-400/70"
              />
              {searchQuery && (
                <button
                  onClick={() => setSearchQuery('')}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-white"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
            </div>

            <div className="flex items-center space-x-1 w-full sm:w-auto">
              <button
                onClick={() => setAudioOnlyFilter(false)}
                className={`flex-1 sm:flex-none px-3 py-2 text-xs rounded-xl flex items-center justify-center space-x-1.5 border transition ${
                  !audioOnlyFilter
                    ? 'neu-inset text-blue-300'
                    : 'neu-button text-slate-400 hover:text-slate-300'
                }`}
              >
                <Radio className="w-3.5 h-3.5" />
                <span>All Devices</span>
              </button>
              <button
                onClick={() => setAudioOnlyFilter(true)}
                className={`flex-1 sm:flex-none px-3 py-2 text-xs rounded-xl flex items-center justify-center space-x-1.5 border transition ${
                  audioOnlyFilter
                    ? 'neu-inset text-blue-300'
                    : 'neu-button text-slate-400 hover:text-slate-300'
                }`}
              >
                <Volume2 className="w-3.5 h-3.5" />
                <span>Speakers</span>
              </button>
            </div>
          </div>
        </div>

        {/* Device List */}
        <div className="p-4 overflow-y-auto space-y-2 flex-1">
          {filteredDevices.length === 0 ? (
            <div className="text-center py-10 neu-text-muted">
              <Bluetooth className="w-12 h-12 mx-auto neu-text-faint opacity-70 mb-3" />
              <p className="text-sm">
                {searchQuery
                  ? `No Bluetooth devices matching "${searchQuery}"`
                  : isScanning
                    ? 'Scanning — devices will appear here the moment they are found.'
                    : 'No Bluetooth devices detected yet.'}
              </p>
              <p className="text-xs neu-text-faint mt-1">
                Put your speaker in pairing mode or manage MAC addresses directly below.
              </p>
            </div>
          ) : (
            filteredDevices.map((dev) => {
              const hasName = Boolean(dev.name);
              const displayName = dev.name || dev.alias || 'Unknown Device';
              const isLoading = actionAddress === dev.address;

              return (
                <div
                  key={dev.address}
                  className="neu-inset rounded-xl p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 transition hover:border-slate-500/60"
                >
                  <div className="flex items-center space-x-3.5">
                    <div className={`p-2.5 rounded-lg ${dev.is_audio_sink ? 'bg-blue-600/20 text-blue-300' : 'neu-inset neu-text-muted'}`}>
                      {dev.is_audio_sink ? <Volume2 className="w-5 h-5" /> : <Bluetooth className="w-5 h-5" />}
                    </div>
                    <div>
                      <div className="flex items-center space-x-2">
                        <h4 className="font-semibold text-slate-100 text-sm">{displayName}</h4>
                        {hasName ? (
                          <span className="text-[10px] bg-emerald-900/40 text-emerald-400 border border-emerald-700/50 px-1.5 py-0.5 rounded">
                            Named
                          </span>
                        ) : (
                          <span className="text-[10px] neu-inset neu-text-faint px-1.5 py-0.5 rounded">
                            Unnamed Device
                          </span>
                        )}
                        {dev.is_audio_sink && (
                          <span className="text-[10px] bg-blue-900/40 text-blue-300 border border-blue-700/50 px-1.5 py-0.5 rounded">
                            Speaker
                          </span>
                        )}
                        {dev.connected && (
                          <span className="text-[10px] bg-emerald-950 text-emerald-300 border border-emerald-800 px-1.5 py-0.5 rounded">
                            Connected
                          </span>
                        )}
                      </div>
                      <p className="text-xs neu-text-muted font-mono flex items-center space-x-2 mt-0.5">
                        <span className="text-blue-300 font-semibold">{dev.address}</span>
                        <span>•</span>
                        <span>{dev.device_type || 'Bluetooth Device'}</span>
                        {dev.rssi !== null && dev.rssi !== undefined && (
                          <span className="flex items-center gap-1 text-slate-300 font-sans">
                            <Signal className="w-3 h-3 text-blue-400" /> {dev.rssi} dBm
                          </span>
                        )}
                      </p>
                    </div>
                  </div>

                  <div className="flex items-center space-x-2 self-end sm:self-auto">
                    {dev.connected ? (
                      <button
                        onClick={() => handleDisconnect(dev.address)}
                        disabled={isLoading}
                        aria-label={`Disconnect ${displayName}`}
                        className="neu-button px-3 py-1.5 text-amber-300 text-xs font-medium rounded-lg flex items-center space-x-1.5 disabled:opacity-50"
                      >
                        <Power className="w-3.5 h-3.5" />
                        <span>{isLoading ? '...' : 'Disconnect'}</span>
                      </button>
                    ) : (
                      <button
                        onClick={() => handlePair(dev.address)}
                        disabled={isLoading}
                        aria-label={`Connect to ${displayName}`}
                        className="neu-button px-3.5 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs font-medium rounded-lg flex items-center space-x-1.5 disabled:opacity-50"
                      >
                        <Plus className="w-3.5 h-3.5" />
                        <span>{isLoading ? 'Connecting...' : 'Connect'}</span>
                      </button>
                    )}

                    {(dev.paired || dev.connected) && (
                      <button
                        onClick={() => handleRemove(dev.address)}
                        disabled={isLoading}
                        aria-label={`Remove ${displayName}`}
                        className="neu-button px-2.5 py-1.5 text-rose-300 text-xs font-medium rounded-lg flex items-center space-x-1 disabled:opacity-50"
                        title="Remove device"
                      >
                        <Trash2 className="w-3.5 h-3.5 text-rose-400" />
                        <span>Remove</span>
                      </button>
                    )}
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Direct / Manual MAC Pair Footer */}
        <div className="p-4 border-t neu-hairline">
          <p className="text-xs neu-text-muted mb-2 font-medium">Connect by Bluetooth MAC Address:</p>
          <div className="flex flex-col sm:flex-row items-center gap-2">
            <input
              type="text"
              value={manualMac}
              onChange={(e) => setManualMac(e.target.value)}
              placeholder="e.g. EC:81:93:53:A9:16"
              className="neu-control neu-placeholder w-full sm:flex-1 rounded-xl px-3 py-2 text-xs font-mono text-white focus:outline-none focus:ring-2 focus:ring-blue-400/70"
            />
            <div className="flex items-center space-x-2 w-full sm:w-auto">
              <button
                onClick={() => handlePair(manualMac)}
                disabled={!manualMac.trim() || actionAddress === manualMac.trim()}
                className="neu-button flex-1 sm:flex-none px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-xs font-semibold rounded-xl flex items-center justify-center space-x-1.5"
              >
                <Plus className="w-3.5 h-3.5" />
                <span>{actionAddress === manualMac.trim() ? 'Connecting...' : 'Connect'}</span>
              </button>
              <button
                onClick={() => handleRemove(manualMac)}
                disabled={!manualMac.trim() || actionAddress === manualMac.trim()}
                className="neu-button flex-1 sm:flex-none px-4 py-2 disabled:opacity-50 text-rose-300 text-xs font-semibold rounded-xl flex items-center justify-center space-x-1.5"
                title="Remove MAC"
              >
                <Trash2 className="w-3.5 h-3.5 text-rose-400" />
                <span>Remove</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
