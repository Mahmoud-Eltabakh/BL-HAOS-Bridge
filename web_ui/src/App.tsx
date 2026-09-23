import React, { useEffect, useState } from 'react';
import { useBluetoothEvents } from './hooks/useBluetoothEvents';
import { SpeakerCard } from './components/SpeakerCard';
import { DiscoveryModal } from './components/DiscoveryModal';
import { AdapterStatus } from './components/AdapterStatus';
import { SettingsModal } from './components/SettingsModal';
import { apiClient, DeviceInfo, NativeDiagnostics, OperatorDiagnostics, RecoveryContract } from './api/client';
import { DiagnosticsPanel } from './components/DiagnosticsPanel';
import { RecoveryPanel } from './components/RecoveryPanel';
import { AlertCircle, Bluetooth, Plus, Volume2, RefreshCw, ChevronDown, ChevronUp, ShieldCheck } from 'lucide-react';

export const App: React.FC = () => {
  const { adapters, devices, isScanning, wsConnected, error: bluetoothError, refreshData } = useBluetoothEvents();
  const [isDiscoveryOpen, setIsDiscoveryOpen] = useState(false);
  const [selectedDevice, setSelectedDevice] = useState<DeviceInfo | null>(null);
  const [diagnostics, setDiagnostics] = useState<NativeDiagnostics | null>(null);
  const [diagnosticsError, setDiagnosticsError] = useState<string | null>(null);
  const [showDiagnostics, setShowDiagnostics] = useState(false);
  const [operatorDiagnostics, setOperatorDiagnostics] = useState<OperatorDiagnostics | null>(null);
  const [recovery, setRecovery] = useState<RecoveryContract | null>(null);
  const [operatorLoading, setOperatorLoading] = useState(true);
  const [operatorError, setOperatorError] = useState<string | null>(null);

  const pairedSpeakers = devices.filter((d) => d.paired);

  const refreshDiagnostics = async () => {
    setOperatorLoading(true);
    try {
      setDiagnostics(await apiClient.getNativeDiagnostics());
      const nextDiagnostics = await apiClient.getDiagnostics();
      setOperatorDiagnostics(nextDiagnostics);
      setRecovery(await apiClient.getRecovery());
      setDiagnosticsError(null);
      setOperatorError(null);
    } catch (error) {
      setDiagnostics(null);
      setDiagnosticsError(error instanceof Error ? error.message : 'Native diagnostics are unavailable');
      setOperatorError('Diagnostics are unavailable');
    } finally {
      setOperatorLoading(false);
    }
  };

  useEffect(() => {
    void refreshDiagnostics();
  }, []);

  const refreshAll = () => {
    refreshData();
    void refreshDiagnostics();
  };

  return (
    <div className="flex-1 bg-slate-900 text-slate-100 p-6 md:p-10 max-w-7xl mx-auto w-full">
      {/* Top Navbar */}
      <header className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-8 border-b border-slate-800">
        <div className="flex items-center space-x-3.5">
          <div className="p-3 bg-blue-600 rounded-2xl shadow-lg shadow-blue-500/20 text-white">
            <Bluetooth className="w-7 h-7" />
          </div>
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2.5">
              BL-HAOS
              <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-blue-900/60 text-blue-300 border border-blue-700/60">
                Bluetooth Audio
              </span>
              <span
                className={`text-[10px] font-medium px-2 py-0.5 rounded-full border ${
                  wsConnected
                    ? 'bg-emerald-950/60 text-emerald-400 border-emerald-700/60'
                    : 'bg-rose-950/60 text-rose-400 border-rose-700/60'
                }`}
              >
                {wsConnected ? 'Live' : 'Offline'}
              </span>
            </h1>
            <p className="text-xs text-slate-400 mt-0.5">Home Assistant OS Bluetooth Audio Adapter & Multi-Room Sync</p>
          </div>
        </div>

        <div className="flex items-center space-x-3">
          <button
            onClick={refreshAll}
            className="p-2.5 text-slate-400 hover:text-white bg-slate-800 border border-slate-700 rounded-xl hover:bg-slate-700 transition"
            title="Refresh State"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
          <button
            onClick={() => setIsDiscoveryOpen(true)}
            className="px-5 py-2.5 bg-blue-600 hover:bg-blue-500 text-white font-medium text-sm rounded-xl flex items-center space-x-2 shadow-lg shadow-blue-600/20 transition"
          >
            <Plus className="w-4 h-4" />
            <span>Add Speaker</span>
          </button>
        </div>
      </header>

      {/* Adapter Status Bar */}
      <div className="mt-6">
        <AdapterStatus adapters={adapters} onRefresh={refreshAll} />
      </div>
      {bluetoothError && <div className="mt-4 rounded-xl border border-rose-800/80 bg-rose-950/50 px-4 py-3 text-sm text-rose-200" role="alert">{bluetoothError} <button className="ml-2 underline" onClick={refreshAll}>Retry</button></div>}

      <div data-demo-mode={operatorDiagnostics?.demo_mode ? 'true' : 'false'}>
        <DiagnosticsPanel diagnostics={operatorDiagnostics} loading={operatorLoading} error={operatorError} onExport={() => void apiClient.downloadSupportBundle()} />
      </div>
      <RecoveryPanel
        contract={recovery}
        target={operatorDiagnostics?.last_failure?.speaker}
        onComplete={(result) => { if (result.diagnostics) setOperatorDiagnostics(result.diagnostics); }}
      />

      {/* Integration Status Accordion */}
      <section className="mt-4 border border-slate-800 bg-slate-800/30 rounded-xl overflow-hidden transition-all" aria-live="polite">
        <button
          onClick={() => setShowDiagnostics(!showDiagnostics)}
          className="w-full px-4 py-2.5 flex items-center justify-between text-xs text-slate-400 hover:text-slate-200 transition"
        >
          <div className="flex items-center space-x-2">
            <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
            <span className="font-medium text-slate-300">Home Assistant Native integration</span>
            <span className="text-[10px] bg-slate-800 text-slate-400 border border-slate-700 px-1.5 py-0.2 rounded">
              {diagnostics?.native_transport_ready ? 'Connected' : 'Ready'}
            </span>
          </div>
          <div className="flex items-center space-x-1.5 text-slate-400">
            <span>Details</span>
            {showDiagnostics ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          </div>
        </button>

        {showDiagnostics && (
          <div className="px-4 pb-3.5 pt-1 border-t border-slate-800/80 bg-slate-900/40">
            {diagnosticsError ? (
              <p className="flex items-center gap-2 text-xs text-rose-300"><AlertCircle className="w-3.5 h-3.5" />{diagnosticsError}</p>
            ) : diagnostics ? (
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 text-xs text-slate-300">
                <span>Bridge Status: <strong className="text-emerald-400 font-medium">{diagnostics.native_transport_ready ? 'Ready' : 'Unavailable'}</strong></span>
                <span>Active Link: <strong className="text-slate-200 font-medium">{diagnostics.bridge_credential_present ? 'Configured' : 'Local Push'}</strong></span>
                <span>Speakers Synced: <strong className="text-slate-200 font-medium">{diagnostics.connected_trusted_speaker_count}/{diagnostics.trusted_speaker_count}</strong></span>
              </div>
            ) : (
              <p className="text-xs text-slate-400">Loading integration status...</p>
            )}
          </div>
        )}
      </section>

      {/* Main Speakers Grid */}
      <main className="mt-8">
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-lg font-bold text-slate-200 flex items-center gap-2">
            <Volume2 className="w-5 h-5 text-blue-400" />
            <span>Speakers</span>
            <span className="text-xs font-normal text-slate-400">({pairedSpeakers.length})</span>
          </h2>
        </div>

        {pairedSpeakers.length === 0 ? (
          <div className="bg-slate-800/40 border border-dashed border-slate-700 rounded-2xl p-12 text-center">
            <Volume2 className="w-12 h-12 mx-auto text-slate-600 mb-3" />
            <h3 className="text-base font-semibold text-slate-300">No speakers added yet</h3>
            <p className="text-xs text-slate-400 mt-1 max-w-sm mx-auto">
              Click Add Speaker to discover and connect your Bluetooth speaker.
            </p>
            <button
              onClick={() => setIsDiscoveryOpen(true)}
              className="mt-5 px-5 py-2 bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold rounded-lg inline-flex items-center space-x-1.5 transition"
            >
              <Plus className="w-4 h-4" />
              <span>Add Speaker</span>
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {pairedSpeakers.map((dev) => (
              <SpeakerCard
                key={dev.address}
                device={dev}
                onSettingsClick={(d) => setSelectedDevice(d)}
                onRefresh={refreshData}
              />
            ))}
          </div>
        )}
      </main>

      {/* Modals */}
      <DiscoveryModal
        isOpen={isDiscoveryOpen}
        onClose={() => setIsDiscoveryOpen(false)}
        devices={devices}
        isScanning={isScanning}
        onRefresh={refreshData}
      />

      <SettingsModal
        isOpen={!!selectedDevice}
        onClose={() => setSelectedDevice(null)}
        selectedDevice={selectedDevice}
        adapters={adapters}
        onSaved={refreshData}
      />
    </div>
  );
};
export default App;
