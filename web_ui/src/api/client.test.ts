import { describe, it, expect, vi } from 'vitest';
import { apiClient, getApiUrl, getBasePath, getWebSocketUrl, type QueueCommand } from './client';

describe('getBasePath', () => {
  it('strips trailing slashes from the ingress path', () => {
    expect(getBasePath()).toBe('');
  });
});

describe('getApiUrl', () => {
  it('prefixes endpoint with base path', () => {
    expect(getApiUrl('/api/health')).toBe('/api/health');
  });

  it('adds a leading slash when missing', () => {
    expect(getApiUrl('api/health')).toBe('/api/health');
  });
});

describe('getWebSocketUrl', () => {
  it('uses wss on https pages', () => {
    Object.defineProperty(window, 'location', {
      writable: true,
      value: { protocol: 'https:', host: 'ha.local', pathname: '/ingress/bl_haos/' },
    });
    expect(getWebSocketUrl()).toBe('wss://ha.local/ingress/bl_haos/ws');
  });

  it('uses ws on http pages', () => {
    Object.defineProperty(window, 'location', {
      writable: true,
      value: { protocol: 'http:', host: 'localhost:8099', pathname: '/' },
    });
    expect(getWebSocketUrl()).toBe('ws://localhost:8099/ws');
  });
});

describe('queue commands', () => {
  it.each<QueueCommand>([
    { queue_version: 1, operation: 'play' },
    { queue_version: 1, operation: 'pause' },
    { queue_version: 1, operation: 'stop' },
    { queue_version: 1, operation: 'next' },
    { queue_version: 1, operation: 'previous' },
    { queue_version: 1, operation: 'set_shuffle', shuffle: true },
    { queue_version: 1, operation: 'set_repeat', repeat: 'all' },
  ])('uses the protected ingress command path for %s', async (command) => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: 'ok' }), { status: 200 }));
    vi.stubGlobal('fetch', fetch);

    await apiClient.commandQueue('aa:bb:cc:dd:ee:01', command);

    expect(fetch).toHaveBeenCalledExactlyOnceWith(
      '/api/devices/aa:bb:cc:dd:ee:01/queue/command',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(command),
      },
    );
    expect(JSON.stringify(fetch.mock.calls[0])).not.toContain('Authorization');
  });
});
