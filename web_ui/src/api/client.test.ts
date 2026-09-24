import { describe, it, expect } from 'vitest';
import { getApiUrl, getBasePath, getWebSocketUrl } from './client';

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
