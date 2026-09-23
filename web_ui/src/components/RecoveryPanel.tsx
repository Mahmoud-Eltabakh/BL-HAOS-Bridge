import React, { useState } from 'react';
import { AlertCircle, CheckCircle2, RotateCw } from 'lucide-react';
import { RecoveryContract, RecoveryResult, apiClient } from '../api/client';

interface Props {
  contract: RecoveryContract | null;
  target: string | undefined;
  onComplete: (result: RecoveryResult) => void;
}

export const RecoveryPanel: React.FC<Props> = ({ contract, target, onComplete }) => {
  const [pending, setPending] = useState<string | null>(null);
  const [result, setResult] = useState<RecoveryResult | null>(null);
  const guidance = contract?.guidance;

  if (!guidance) return <p className="mt-3 text-xs text-slate-500">Recovery guidance is unavailable.</p>;

  const run = async (action: string) => {
    setPending(action);
    try {
      const next = await apiClient.executeRecovery(action, target);
      setResult(next);
      onComplete(next);
    } catch {
      setResult({ action_id: action, target: target ?? null, correlation_id: 'unavailable', result: 'failed', detail: 'Recovery unavailable', diagnostics: {} as RecoveryResult['diagnostics'] });
    } finally {
      setPending(null);
    }
  };

  return <section className="mt-4 border border-slate-800 bg-slate-800/30 rounded-xl p-4" aria-live="polite">
    <div className="flex items-center gap-2"><RotateCw className="w-4 h-4 text-cyan-400" /><h2 className="text-sm font-semibold text-slate-200">Guided recovery</h2></div>
    <p className="mt-3 text-sm text-slate-200">{guidance.diagnosis}</p>
    <p className="mt-1 text-xs text-slate-400">Expected: {guidance.expected_outcome}</p>
    <div className="mt-3 flex flex-wrap gap-2">
      {guidance.actions.map((action) => <button key={action} disabled={pending !== null} onClick={() => void run(action)} className="px-3 py-2 text-xs rounded-lg border border-cyan-800 text-cyan-200 hover:bg-cyan-950/50 disabled:opacity-50">{pending === action ? 'Working...' : action.replace(/_/g, ' ')}</button>)}
    </div>
    {result && <p className={`mt-3 flex items-center gap-2 text-xs ${result.result === 'succeeded' ? 'text-emerald-300' : 'text-rose-300'}`}>
      {result.result === 'succeeded' ? <CheckCircle2 className="w-3.5 h-3.5" /> : <AlertCircle className="w-3.5 h-3.5" />}
      {result.result === 'succeeded' ? 'Recovery completed.' : 'Recovery did not complete.'} Correlation: {result.correlation_id}
    </p>}
  </section>;
};