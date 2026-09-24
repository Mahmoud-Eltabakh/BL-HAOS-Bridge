import { test, expect } from '@playwright/test';

test.describe('Dashboard startup', () => {
  test('loads, shows the demo adapter and demo speaker', async ({ page }) => {
    await test.step('open the ingress dashboard', async () => {
      await page.goto('/');
      await expect(page.getByRole('heading', { name: /BL-HAOS/ })).toBeVisible();
    });

    await test.step('demo speaker card renders', async () => {
      await expect(page.getByText('Demo Speaker')).toBeVisible({ timeout: 15_000 });
    });

    await test.step('live websocket badge connects', async () => {
      await expect(page.getByText('Live')).toBeVisible({ timeout: 15_000 });
    });
  });
});
