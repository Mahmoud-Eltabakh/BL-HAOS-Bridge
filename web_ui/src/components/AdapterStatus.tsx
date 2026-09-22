import React from 'react';
import { AdapterInfo, apiClient } from '../api/client';
import { Radio, Power } from 'lucide-react';

interface AdapterStatusProps {
  adapters: AdapterInfo[];
  onRefresh: () => void;
}

export const AdapterStatus: React.FC<AdapterStatusProps> = ({ adapters, onRefresh }) => {
  const togglePower = async (name: string, current: boolean) => {
    await apiClient.setAdapterPower(name, !current);
    onRefresh();
  };

  return (
    <div className="bg-slate-800/80 border border-slate-700/80 rounded-xl p-4 flex flex-wrap items-center gap-4">
      <div className="flex items-center space-x-2 text-slate-300 font-semibold text-sm mr-2">
        <Radio className="w-4 h-4 text-blue-400" />
        <span>Bluetooth Adapters:</span>
      </div>

      {adapters.map((adapter) => (
        <div
          key={adapter.interface}
          className="bg-slate-900/90 border border-slate-700 rounded-lg px-3 py-1.5 flex items-center space-x-3 text-xs"
        >
          <span className={`w-2 h-2 rounded-full ${adapter.powered ? 'bg-emerald-400' : 'bg-rose-500'}`} />
          <span className="font-semibold text-slate-200">{adapter.interface}</span>
          <span className="text-slate-400 font-mono hidden sm:inline">({adapter.address})</span>
          <button
            onClick={() => togglePower(adapter.interface, adapter.powered)}
            className={`p-1 rounded hover:bg-slate-800 transition ${adapter.powered ? 'text-emerald-400' : 'text-slate-500'}`}
            title="Toggle Adapter Power"
          >
            <Power className="w-3.5 h-3.5" />
          </button>
        </div>
      ))}
    </div>
  );
};
