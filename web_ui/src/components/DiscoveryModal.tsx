import React, { useState } from 'react';
import { DeviceInfo, apiClient } from '../api/client';
import { X, RefreshCw, Bluetooth, Signal, Plus, Key, Search, Volume2, Radio } from 'lucide-react';

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
  const [pairingAddress, setPairingAddress] = useState<string | null>(null);
  const [pinCode, setPinCode] = useState('0000');
  const [showPinInput, setShowPinInput] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [audioOnlyFilter, setAudioOnlyFilter] = useState(false);
  const [manualMac, setManualMac] = useState('');

  if (!isOpen) return null;

  const filteredDevices = devices.filter((d) => {
    if (d.paired) return false;
    if (audioOnlyFilter && !d.is_audio_sink) return false;
    if (!searchQuery.trim()) return true;

    const query = searchQuery.toLowerCase().trim();
    const macMatch = d.address.toLowerCase().includes(query);
    const nameMatch = d.name ? d.name.toLowerCase().includes(query) : false;
    const aliasMatch = d.alias ? d.alias.toLowerCase().includes(query) : false;

    return macMatch || nameMatch || aliasMatch;
  });

  const handlePair = async (address: string) => {
    if (!address.trim()) return;
    setPairingAddress(address);
    try {
      await apiClient.pairDevice(address.trim(), pinCode);
      alert(`Successfully paired and trusted ${address}!`);
      onRefresh();
    } catch (err) {
      alert(`Pairing failed: ${err}`);
    } finally {
      setPairingAddress(null);
      setShowPinInput(false);
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
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4">
      <div className="bg-slate-800 border border-slate-700 rounded-2xl w-full max-w-2xl overflow-hidden shadow-2xl flex flex-col max-h-[85vh]">
        {/* Header */}
        <div className="p-5 border-b border-slate-700 flex items-center justify-between bg-slate-900/50">
          <div className="flex items-center space-x-3">
            <div className="p-2.5 bg-blue-600/20 text-blue-400 rounded-xl">
              <Bluetooth className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-white">Scan for Bluetooth Speakers</h2>
              <p className="text-xs text-slate-400">Discover, search, and pair nearby Bluetooth devices</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 text-slate-400 hover:text-white rounded-lg hover:bg-slate-700 transition"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Scan & Search Action Bar */}
        <div className="p-4 bg-slate-900/30 border-b border-slate-700/80 space-y-3">
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
                <div className="flex items-center space-x-1.5 bg-slate-900 border border-slate-700 rounded-lg px-2 py-1">
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
                  className="px-3 py-1.5 text-xs text-slate-400 hover:text-slate-200 bg-slate-800 border border-slate-700 rounded-lg transition"
                >
                  Custom PIN
                </button>
              )}

              <button
                onClick={handleToggleScan}
                className={`px-4 py-1.5 text-xs font-semibold rounded-lg flex items-center space-x-2 transition ${
                  isScanning ? 'bg-slate-700 text-slate-200 hover:bg-slate-600' : 'bg-blue-600 text-white hover:bg-blue-500'
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
                className="w-full bg-slate-900/80 border border-slate-700 rounded-xl pl-9 pr-4 py-2 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
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
                    ? 'bg-blue-600/30 text-blue-300 border-blue-500/50'
                    : 'bg-slate-800 text-slate-400 border-slate-700 hover:text-slate-300'
                }`}
              >
                <Radio className="w-3.5 h-3.5" />
                <span>All MACs</span>
              </button>
              <button
                onClick={() => setAudioOnlyFilter(true)}
                className={`flex-1 sm:flex-none px-3 py-2 text-xs rounded-xl flex items-center justify-center space-x-1.5 border transition ${
                  audioOnlyFilter
                    ? 'bg-blue-600/30 text-blue-300 border-blue-500/50'
                    : 'bg-slate-800 text-slate-400 border-slate-700 hover:text-slate-300'
                }`}
              >
                <Volume2 className="w-3.5 h-3.5" />
                <span>Audio Sinks</span>
              </button>
            </div>
          </div>
        </div>

        {/* Device List */}
        <div className="p-5 overflow-y-auto space-y-3 flex-1">
          {filteredDevices.length === 0 ? (
            <div className="text-center py-10 text-slate-400">
              <Bluetooth className="w-12 h-12 mx-auto text-slate-600 mb-3" />
              <p className="text-sm">
                {searchQuery
                  ? `No Bluetooth devices matching "${searchQuery}"`
                  : 'No unpaired Bluetooth devices detected yet.'}
              </p>
              <p className="text-xs text-slate-500 mt-1">
                Put your speaker in pairing mode or pair directly using its MAC address below.
              </p>
            </div>
          ) : (
            filteredDevices.map((dev) => {
              const hasName = Boolean(dev.name);
              const displayName = dev.name || dev.alias || 'Unknown Device';

              return (
                <div
                  key={dev.address}
                  className="bg-slate-900/60 border border-slate-700/70 hover:border-slate-600 rounded-xl p-4 flex items-center justify-between transition"
                >
                  <div className="flex items-center space-x-3.5">
                    <div className={`p-2.5 rounded-lg ${dev.is_audio_sink ? 'bg-blue-600/20 text-blue-400' : 'bg-slate-800 text-slate-400'}`}>
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
                          <span className="text-[10px] bg-slate-800 text-slate-400 border border-slate-700 px-1.5 py-0.5 rounded">
                            MAC Beacon
                          </span>
                        )}
                        {dev.is_audio_sink && (
                          <span className="text-[10px] bg-blue-900/40 text-blue-300 border border-blue-700/50 px-1.5 py-0.5 rounded">
                            Speaker
                          </span>
                        )}
                      </div>
                      <p className="text-xs text-slate-400 font-mono flex items-center space-x-2 mt-0.5">
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

                  <div className="flex items-center space-x-2">
                    <button
                      onClick={() => handlePair(dev.address)}
                      disabled={pairingAddress === dev.address}
                      className="px-3.5 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs font-medium rounded-lg flex items-center space-x-1.5 transition disabled:opacity-50"
                    >
                      <Plus className="w-3.5 h-3.5" />
                      <span>{pairingAddress === dev.address ? 'Pairing...' : 'Pair & Trust'}</span>
                    </button>
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Direct / Manual MAC Pair Footer */}
        <div className="p-4 bg-slate-900/70 border-t border-slate-700/80">
          <p className="text-xs text-slate-400 mb-2 font-medium">Direct Pair by Bluetooth MAC Address:</p>
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={manualMac}
              onChange={(e) => setManualMac(e.target.value)}
              placeholder="e.g. EC:81:93:53:A9:16"
              className="flex-1 bg-slate-950 border border-slate-700 rounded-xl px-3 py-2 text-xs font-mono text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
            />
            <button
              onClick={() => handlePair(manualMac)}
              disabled={!manualMac.trim() || pairingAddress === manualMac.trim()}
              className="px-4 py-2 bg-slate-700 hover:bg-slate-600 disabled:opacity-50 text-white text-xs font-semibold rounded-xl flex items-center space-x-1.5 transition"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>{pairingAddress === manualMac.trim() ? 'Pairing...' : 'Pair MAC'}</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
