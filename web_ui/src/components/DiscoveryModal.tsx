import React, { useState } from 'react';
import { DeviceInfo, apiClient } from '../api/client';
import { X, RefreshCw, Bluetooth, Signal, Plus, Key } from 'lucide-react';

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

  if (!isOpen) return null;

  const unpariedDevices = devices.filter((d) => !d.paired);

  const handlePair = async (address: string) => {
    setPairingAddress(address);
    try {
      await apiClient.pairDevice(address, pinCode);
      alert('Successfully paired and trusted speaker!');
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
              <p className="text-xs text-slate-400">Discover and pair nearby Bluetooth audio devices</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 text-slate-400 hover:text-white rounded-lg hover:bg-slate-700 transition"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Scan Action Bar */}
        <div className="p-4 bg-slate-900/30 border-b border-slate-700/80 flex flex-wrap items-center justify-between gap-3">
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

        {/* Device List */}
        <div className="p-5 overflow-y-auto space-y-3 flex-1">
          {unpariedDevices.length === 0 ? (
            <div className="text-center py-12 text-slate-400">
              <Bluetooth className="w-12 h-12 mx-auto text-slate-600 mb-3" />
              <p className="text-sm">No new Bluetooth audio devices detected yet.</p>
              <p className="text-xs text-slate-500 mt-1">Put your speaker in pairing mode and click Start Scan.</p>
            </div>
          ) : (
            unpariedDevices.map((dev) => (
              <div
                key={dev.address}
                className="bg-slate-900/60 border border-slate-700/70 hover:border-slate-600 rounded-xl p-4 flex items-center justify-between transition"
              >
                <div className="flex items-center space-x-3.5">
                  <div className="p-2.5 bg-slate-800 text-slate-300 rounded-lg">
                    <Bluetooth className="w-5 h-5" />
                  </div>
                  <div>
                    <h4 className="font-semibold text-slate-100 text-sm">{dev.name || dev.alias || 'Unknown Device'}</h4>
                    <p className="text-xs text-slate-400 font-mono flex items-center space-x-2">
                      <span>{dev.address}</span>
                      <span>•</span>
                      <span>{dev.device_type}</span>
                      {dev.rssi && (
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
            ))
          )}
        </div>
      </div>
    </div>
  );
};
