import { defineConfig, devices } from '@playwright/test';

/**
 * BL-HAOS Ingress dashboard E2E configuration.
 *
 * Runs against the real FastAPI backend in deterministic demo mode:
 *   BLHAOS_DEMO_MODE=true BLHAOS_DEMO_SCENARIO=healthy uvicorn bl_haos.main:app
 * The webServer recipe below boots it automatically unless BLHAOS_E2E_URL
 * points at an already-running instance.
 */
export default defineConfig({
  testDir: './specs',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: process.env.BLHAOS_E2E_URL ?? 'http://127.0.0.1:8099',
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: process.env.BLHAOS_E2E_URL
    ? undefined
    : {
        command: 'python -m uvicorn backend.bl_haos.main:app --host 127.0.0.1 --port 8099',
        cwd: '../..',
        url: 'http://127.0.0.1:8099/api/health',
        reuseExistingServer: !process.env.CI,
        timeout: 60_000,
        env: {
          BLHAOS_DEMO_MODE: 'true',
          BLHAOS_DEMO_SCENARIO: 'healthy',
        },
      },
});
