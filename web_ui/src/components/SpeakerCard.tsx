import React, { useEffect, useState } from 'react';
import { DeviceInfo, QueueCommand, apiClient } from '../api/client';
import { Volume2, Bluetooth, Power, Trash2, Settings, AlertCircle, SkipBack, SkipForward, Play, Pause, Square, Shuffle } from 'lucide-react';

interface SpeakerCardProps {
  device: DeviceInfo;
  onSettingsClick: (device: DeviceInfo) => void;
  onRefresh: () => void;
}

function formatTime(seconds: number | null | undefined): string {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return 'Time unavailable';
  const total = Math.floor(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

function queueStatusLabel(status?: string): string {
  if (status === 'playing') return 'Playing';
  if (status === 'paused') return 'Paused';
  if (status === 'failed') return 'Playback failed';
  if (status === 'completed') return 'Playlist complete';
  if (status === 'stopped') return 'Stopped';
  return 'Status unavailable';
}

export const SpeakerCard: React.FC<SpeakerCardProps> = ({ device, onSettingsClick, onRefresh }) => {
  const [loading, setLoading] = useState(false);
  const [volume, setVolume] = useState(70);
  const [volumeSaving, setVolumeSaving] = useState(false);
  const [showConfirmRemove, setShowConfirmRemove] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [queuePending, setQueuePending] = useState('');
  const [queueError, setQueueError] = useState<string | null>(null);

  // Seed the local volume from the level the bridge reports for the speaker.
  // The bridge reads the sink's real volume back from the audio server, so this
  // is where the speaker actually is - including a change made with the
  // speaker's own buttons. Until that first report there is no honest number to
  // show, so the slider stays disabled instead of claiming a level (it used to
  // fall back to 70%, which looked like the real volume and was not).
  const playbackVolume = device.playback?.volume;
  const reportedVolume = device.connected && typeof playbackVolume === 'number'
    ? Math.round(playbackVolume * 100)
    : null;
  const queueSupported = Boolean(device.playback && Object.prototype.hasOwnProperty.call(device.playback, 'queue'));
  const queue = device.playback?.queue ?? null;
  const hasCurrentItem = typeof queue?.index === 'number' && typeof queue.count === 'number'
    && queue.index >= 1 && queue.count >= queue.index;
  const nextAtEnd = hasCurrentItem && queue.repeat === 'off' && queue.index === queue.count;
  const playOperation = queue?.status !== 'failed' && device.playback?.state === 'playing' ? 'pause' : 'play';
  const playLabel = playOperation === 'pause' ? 'Pause' : 'Play';

  const sendQueueCommand = async (command: QueueCommand, action: string) => {
    if (!device.connected || !queueSupported || queuePending) return;
    setQueuePending(action);
    setQueueError(null);
    try {
      await apiClient.commandQueue(device.address, command);
    } catch {
      setQueueError('Could not confirm the queue command. Check the current playback state before trying again.');
    } finally {
      setQueuePending('');
      onRefresh();
    }
  };
  useEffect(() => {
    if (reportedVolume !== null) setVolume(reportedVolume);
  }, [reportedVolume]);

  const commitVolume = async (next: number) => {
    setVolumeSaving(true);
    setErrorMsg(null);
    try {
      await apiClient.setDeviceVolume(device.address, next);
      onRefresh();
    } catch (err: any) {
      setErrorMsg(err?.message || 'Failed to update volume');
      onRefresh();
    } finally {
      setVolumeSaving(false);
    }
  };

  const handleConnectToggle = async () => {
    setLoading(true);
    setErrorMsg(null);
    try {
      if (device.connected) {
        await apiClient.disconnectDevice(device.address);
      } else {
        await apiClient.connectDevice(device.address);
      }
      onRefresh();
    } catch (err: any) {
      setErrorMsg(err?.message || 'Connection toggle failed');
    } finally {
      setLoading(false);
    }
  };

  const handleRemove = async () => {
    setLoading(true);
    setErrorMsg(null);
    try {
      await apiClient.removeDevice(device.address);
      setShowConfirmRemove(false);
      onRefresh();
    } catch (err: any) {
      setErrorMsg(err?.message || 'Failed to remove speaker');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="neu-surface rounded-xl p-4 flex flex-col justify-between transition-all">
      <div>
        <div className="flex items-start justify-between">
          <div className="flex items-center space-x-3">
            <div className={`neu-inset p-3 rounded-xl ${device.connected ? 'text-emerald-400' : 'text-slate-400'}`}>
              <Bluetooth className="w-6 h-6" />
            </div>
            <div>
              <h3 className="font-semibold text-base text-slate-100">{device.alias || device.name || device.address}</h3>
              <p className="text-xs text-slate-400 flex items-center space-x-2">
                <span className="font-mono text-slate-300">{device.address}</span>
                <span>•</span>
                <span>{device.adapter_name}</span>
                {device.rssi && <span>• {device.rssi} dBm</span>}
              </p>
            </div>
          </div>
          <span
            className={`neu-inset px-2.5 py-1 text-xs font-medium rounded-full ${
              device.connected ? 'text-emerald-300' : device.detached ? 'text-amber-300' : 'text-slate-300'
            }`}
            title={device.detached ? 'BlueZ no longer reports this speaker; it is switched off or out of range' : undefined}
          >
            {device.connected ? 'Connected' : device.detached ? 'Offline' : 'Disconnected'}
          </span>
        </div>

        {/* Device Type & Auto-Reconnect Tag */}
        <div className="mt-4 flex items-center space-x-2 text-xs">
          <span className="neu-inset px-2.5 py-1 rounded-lg text-slate-300">
            {device.device_type}
          </span>
          {device.trusted && (
            <span className="bg-blue-950/80 px-2.5 py-1 rounded-lg text-blue-300 border border-blue-800">
              Auto-Reconnect
            </span>
          )}
        </div>

        {errorMsg && (
            <div className="neu-inset mt-3 p-2 rounded-lg text-rose-300 text-xs flex items-center gap-1.5">
            <AlertCircle className="w-3.5 h-3.5 shrink-0" />
            <span>{errorMsg}</span>
          </div>
        )}

        {queueSupported && (
          <section className="neu-inset mt-4 rounded-lg p-3" aria-label="Playback queue">
            <div className="flex flex-col gap-1" aria-live="polite" aria-atomic="true">
              {!device.connected && <p className="text-sm text-amber-300">Speaker disconnected. The queue was stopped.</p>}
              {device.connected && !queue && <p className="text-sm text-slate-300">No active queue</p>}
              {device.connected && queue && <>
                <p className="text-sm font-semibold text-slate-100">{queue.status === 'completed' ? 'Playlist complete' : queue.playlist || 'Playback queue'}</p>
                <p className="text-sm text-slate-200">{queue.title || 'Current item unavailable'}</p>
                {queue.artist && <p className="text-xs text-slate-400">{queue.artist}</p>}
                {hasCurrentItem && <p className="text-xs text-slate-300">Item {queue.index} of {queue.count}</p>}
                <p className="text-xs text-slate-300" aria-label="Playback timeline">
                  {formatTime(queue.position)} / {formatTime(queue.duration)}
                </p>
                <p className="text-xs text-slate-400">
                  {queueStatusLabel(queue.status)}{queue.shuffle ? ' · Shuffle on' : ''}{queue.repeat ? ` · Repeat ${queue.repeat}` : ''}
                </p>
                {queue.status === 'failed' && <p className="text-xs text-rose-300">Playback failed for “{queue.title || 'current item'}”. Nothing was skipped. Select Play to retry or Next to skip this item.</p>}
              </>}
            </div>
            {queueError && <p className="mt-2 text-xs text-rose-300" role="alert">{queueError}</p>}
            {queuePending && <p className="mt-2 text-xs text-amber-300" role="status" aria-live="polite" aria-atomic="true">Sending {queuePending}…</p>}
            {queue && <div className="mt-3 flex flex-wrap items-center gap-2">
              <button type="button" className="neu-button min-h-11 min-w-11 px-3 py-2" aria-label="Previous" title="Previous"
                disabled={!device.connected || !hasCurrentItem || Boolean(queuePending)}
                onClick={() => void sendQueueCommand({ queue_version: 1, operation: 'previous' }, 'Previous')}>
                <SkipBack className="h-4 w-4" aria-hidden="true" /><span>Previous</span>
              </button>
              <button type="button" className="neu-button min-h-11 min-w-11 px-3 py-2" aria-label={playLabel} title={playLabel}
                disabled={!device.connected || !hasCurrentItem || Boolean(queuePending)}
                onClick={() => void sendQueueCommand({ queue_version: 1, operation: playOperation }, playLabel)}>
                {playOperation === 'pause' ? <Pause className="h-4 w-4" aria-hidden="true" /> : <Play className="h-4 w-4" aria-hidden="true" />}
                <span>{playLabel}</span>
              </button>
              <button type="button" className="neu-button min-h-11 min-w-11 px-3 py-2" aria-label="Next" title="Next"
                disabled={!device.connected || !hasCurrentItem || Boolean(nextAtEnd) || Boolean(queuePending)}
                onClick={() => void sendQueueCommand({ queue_version: 1, operation: 'next' }, 'Next')}>
                <SkipForward className="h-4 w-4" aria-hidden="true" /><span>Next</span>
              </button>
              <button type="button" className="neu-button min-h-11 min-w-11 px-3 py-2 text-rose-300" aria-label="Stop" title="Stop"
                disabled={!device.connected || Boolean(queuePending)}
                onClick={() => void sendQueueCommand({ queue_version: 1, operation: 'stop' }, 'Stop')}>
                <Square className="h-4 w-4" aria-hidden="true" /><span>Stop</span>
              </button>
              <button type="button" className="neu-button min-h-11 px-3 py-2" aria-label="Shuffle" title="Shuffle"
                aria-pressed={Boolean(queue.shuffle)} disabled={!device.connected || Boolean(queuePending)}
                onClick={() => void sendQueueCommand({ queue_version: 1, operation: 'set_shuffle', shuffle: !queue.shuffle }, 'Shuffle')}>
                <Shuffle className="h-4 w-4" aria-hidden="true" /><span>Shuffle</span>
              </button>
              <label className="flex min-h-11 items-center gap-2 text-sm" htmlFor={`queue-repeat-${device.address}`}>Repeat
                <select id={`queue-repeat-${device.address}`} className="neu-inset min-h-11 px-2"
                  aria-label="Repeat" value={queue.repeat ?? 'off'} disabled={!device.connected || Boolean(queuePending)}
                  onChange={(event) => void sendQueueCommand({
                    queue_version: 1, operation: 'set_repeat', repeat: event.target.value as 'off' | 'one' | 'all',
                  }, 'Repeat')}>
                  <option value="off">Off</option><option value="one">One</option><option value="all">All</option>
                </select>
              </label>
            </div>}
          </section>
        )}
      </div>

      {/* Volume slider when connected */}
      {device.connected && (
        <div className="neu-inset mt-4 rounded-lg p-3">
          <div className="flex items-center justify-between text-xs text-slate-400 mb-2">
            <span className="flex items-center gap-1.5"><Volume2 className="w-4 h-4 text-slate-400" /> Volume</span>
            <span
              className="font-mono text-slate-200"
              title={reportedVolume === null ? 'Waiting for the bridge to read the speaker volume' : undefined}
            >
              {reportedVolume === null ? '—' : `${volume}%`}
            </span>
          </div>
          <input
            type="range"
            min="0"
            max="100"
            value={volume}
            aria-label="Speaker volume"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={reportedVolume ?? undefined}
            disabled={volumeSaving || reportedVolume === null}
            onChange={(e) => setVolume(Number(e.target.value))}
            onPointerUp={() => void commitVolume(volume)}
            onKeyUp={() => void commitVolume(volume)}
            className="neu-range w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer disabled:opacity-50"
          />
        </div>
      )}

      {/* Inline Confirmation for Remove */}
      {showConfirmRemove ? (
        <div className="neu-inset mt-4 rounded-lg p-2.5 flex items-center justify-between gap-2">
          <span className="text-xs text-rose-300 font-medium">Remove speaker?</span>
          <div className="flex items-center space-x-2">
            <button
              onClick={() => setShowConfirmRemove(false)}
              disabled={loading}
              className="neu-button px-3 py-1.5 text-xs text-slate-300 hover:text-white rounded-lg"
            >
              Cancel
            </button>
            <button
              onClick={handleRemove}
              disabled={loading}
              className="neu-button px-3 py-1.5 bg-rose-600 hover:bg-rose-500 text-white text-xs font-semibold rounded-lg"
            >
              {loading ? 'Removing...' : 'Confirm Remove'}
            </button>
          </div>
        </div>
      ) : (
        /* Action Footer */
        <div className="neu-inset mt-4 rounded-lg p-2.5 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center space-x-2">
            {device.connected ? (
              <button
                onClick={handleConnectToggle}
                disabled={loading}
                className="neu-button px-3.5 py-2 text-xs font-semibold rounded-xl flex items-center space-x-1.5 text-amber-300 disabled:opacity-50"
              >
                <Power className="w-3.5 h-3.5" />
                <span>{loading ? 'Disconnecting...' : 'Disconnect'}</span>
              </button>
            ) : (
              <button
                onClick={handleConnectToggle}
                disabled={loading}
                className="neu-button px-3.5 py-2 text-xs font-semibold rounded-xl flex items-center space-x-1.5 bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50"
              >
                <Power className="w-3.5 h-3.5" />
                <span>{loading ? 'Connecting...' : 'Connect'}</span>
              </button>
            )}

            <button
              onClick={() => setShowConfirmRemove(true)}
              className="neu-button px-3 py-2 text-xs font-medium rounded-xl flex items-center space-x-1.5 text-rose-300"
              title="Remove speaker"
            >
              <Trash2 className="w-3.5 h-3.5 text-rose-400" />
              <span>Remove</span>
            </button>
          </div>

          <div className="flex items-center space-x-1 text-slate-400">
            <button
              onClick={() => onSettingsClick(device)}
              className="neu-button px-2.5 py-2 text-xs font-medium text-slate-300 hover:text-slate-200 rounded-xl flex items-center space-x-1"
              title="Settings"
            >
              <Settings className="w-3.5 h-3.5" />
              <span>Settings</span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
};
