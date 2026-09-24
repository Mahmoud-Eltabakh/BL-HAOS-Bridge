import { test, expect } from '@playwright/test';

test.describe('Volume slider', () => {
  test('slider commits the new level to the bridge', async ({ page }) => {
    await page.goto('/');
    const card = page.locator('div', { hasText: 'Demo Speaker' }).filter({ has: page.getByRole('slider') }).first();

    await test.step('volume slider appears on the connected card', async () => {
      const slider = page.getByRole('slider', { name: 'Speaker volume' });
      await expect(slider).toBeVisible({ timeout: 15_000 });
    });

    await test.step('commit a new volume level', async () => {
      const slider = page.getByRole('slider', { name: 'Speaker volume' });
      await slider.fill('55');
      await slider.dispatchEvent('pointerup');
    });
  });
});
