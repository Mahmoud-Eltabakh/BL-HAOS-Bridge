import { test, expect } from '@playwright/test';

test.describe('Diagnostics and native status', () => {
  test('details panel reveals bridge readiness and speaker sync counts', async ({ page }) => {
    await page.goto('/');

    await test.step('open the details accordion', async () => {
      await page.getByRole('button', { name: /Details/i }).click();
    });

    await test.step('native bridge readiness is reported', async () => {
      await expect(page.getByText(/Bridge Status:/)).toBeVisible({ timeout: 15_000 });
      await expect(page.getByText(/Speakers Synced:/)).toBeVisible();
    });
  });
});
