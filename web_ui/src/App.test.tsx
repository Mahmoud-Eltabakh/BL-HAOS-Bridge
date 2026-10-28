import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import App from './App';
import { apiClient } from './api/client';

vi.mock('./hooks/useBluetoothEvents', () => ({ useBluetoothEvents: () => ({
  adapters: [], devices: [], isScanning: false, wsConnected: true, error: null, refreshData: vi.fn(),
}) }));
vi.mock('./api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('./api/client')>();
  return { ...original, apiClient: Object.fromEntries(Object.keys(original.apiClient).map((key) => [key, vi.fn(async () => ({}))])) };
});

afterEach(() => { cleanup(); vi.clearAllMocks(); });

it('opens the public HA catalog at the top frame using a secure native anchor adjacent to integration status', async () => {
  render(<App />);
  await waitFor(() => expect(apiClient.getNativeDiagnostics).toHaveBeenCalledTimes(1));
  const link = screen.getByRole('link', { name: 'Open Media Catalog' });
  expect(link).toHaveAttribute('href', '/bl-haos-media');
  expect(link).toHaveAttribute('target', '_top');
  expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  expect(link).toHaveClass('min-h-11');
  expect(link.className).toContain('focus-visible:');
  expect(within(link.closest('section')!).getByText('Home Assistant Native integration')).toBeVisible();
  link.focus();
  expect(link).toHaveFocus();
});

it('adds no apiClient calls when activated, excluding normal App diagnostics polling', async () => {
  render(<App />);
  await waitFor(() => expect(apiClient.getNativeDiagnostics).toHaveBeenCalledTimes(1));
  const before = Object.values(apiClient).map((method) => vi.mocked(method).mock.calls.length);
  const link = screen.getByRole('link', { name: 'Open Media Catalog' });
  link.addEventListener('click', (event) => event.preventDefault());
  fireEvent.click(link);
  await Promise.resolve();
  expect(Object.values(apiClient).map((method) => vi.mocked(method).mock.calls.length)).toEqual(before);
  expect(document.querySelector('iframe')).toBeNull();
});