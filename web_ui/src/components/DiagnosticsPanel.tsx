import React from 'react';
import { Download, AlertCircle, Activity } from 'lucide-react';
import { OperatorDiagnostics } from '../api/client';

interface Props {
  diagnostics: OperatorDiagnostics | null;
  loading: boolean;
  error: string | null;
  onExport: () => void;
}

export const DiagnosticsPanel: React.FC<Props> = ({ diagnostics, loading, error, onExport }) => (
  <section className="mt-4 border border-slate-800 bg-slate-800/30 rounded-xl p-4" aria-live="polite">
    <div className="flex items-center justify-between gap-3">
      <div className="flex items-center gap-2">
        <Activity className="w-4 h-4 text-cyan-400" />
        <h2 className="text-sm font-semibold text-slate-200">Diagnostics</h2>
        {diagnostics?.demo_mode && <span className="text-[10px] uppercase tracking-wide text-amber-300 border border-amber-700/60 rounded px-1.5 py-0.5">Demo: {diagnostics.demo_scenario}</span>}
      </div>
      <button onClick={onExport} className="p-2 text-slate-400 hover:text-white" title="Export support bundle" aria-label="Export support bundle">
        <Download className="w-4 h-4" />
      </button>
    </div>
    {loading && <p className="mt-3 text-xs text-slate-400">Loading diagnostics...</p>}
    {error && <p className="mt-3 flex items-center gap-2 text-xs text-rose-300"><AlertCircle className="w-3.5 h-3.5" />Diagnostics are unavailable.</p>}
    {!loading && !error && diagnostics && (
      <div className="mt-3 grid grid-cols-2 md:grid-cols-4 gap-3 text-xs text-slate-300">
        <span>Status <strong className="block text-slate-100">{diagnostics.status}</strong></span>
        <span>Lifecycle <strong className="block text-slate-100">{diagnostics.lifecycle}</strong></span>
        <span>Failure <strong className="block text-amber-300">{diagnostics.last_failure?.classification ?? 'none'}</strong></span>
        <span>Events <strong className="block text-slate-100">{diagnostics.event_count}</strong></span>
      </div>
    )}
  </section>
);