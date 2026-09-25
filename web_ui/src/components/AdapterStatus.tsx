import React, { useState } from 'react';
import { AdapterInfo, apiClient } from '../api/client';
import { Radio, Power } from 'lucide-react';

interface AdapterStatusProps {
  adapters: AdapterInfo[];
  onRefresh: () => void;
}

export const AdapterStatus: React.FC<AdapterStatusProps> = ({ adapters, onRefresh }) => {
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const togglePower = async (name: string, current: boolean) => {
    setPending(name);
    setError(null);
    try {
      await apiClient.setAdapterPower(name, !current);
      onRefresh();
    } catch {
      setError(`Could not change ${name} power state. Retry the action.`);
    } finally {
      setPending(null);
    }
  };

  return (
    <div className="neu-surface rounded-lg px-3 py-2.5 flex flex-wrap items-center gap-2.5" aria-live="polite">
      <div className="flex items-center space-x-2 text-slate-300 font-semibold text-xs mr-1">
        <Radio className="w-3.5 h-3.5 text-blue-400" />
        <span>Adapters</span>
      </div>

      {adapters.map((adapter) => (
        <div
          key={adapter.interface}
          className="neu-inset rounded-md px-2.5 py-1 flex items-center space-x-2.5 text-[11px]"
        >
          <span className={`w-2 h-2 rounded-full ${adapter.powered ? 'bg-emerald-400' : 'bg-rose-500'}`} />
          <span className="font-semibold text-slate-200">{adapter.interface}</span>
          <span className="neu-text-muted font-mono hidden sm:inline">({adapter.address})</span>
          <button
            onClick={() => void togglePower(adapter.interface, adapter.powered)}
            disabled={pending !== null}
            aria-label={`${adapter.powered ? 'Power off' : 'Power on'} ${adapter.interface}`}
            className={`neu-button p-1 rounded ${adapter.powered ? 'text-emerald-400' : 'text-slate-300'}`}
            title="Toggle Adapter Power"
          >
            <Power className="w-3.5 h-3.5" />
          </button>
        </div>
      ))}
      {error && <p className="basis-full text-xs text-rose-300">{error}</p>}
    </div>
  );
};
