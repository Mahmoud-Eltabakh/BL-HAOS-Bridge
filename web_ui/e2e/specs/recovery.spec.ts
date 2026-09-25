import { test, expect } from '@playwright/test';

test.describe('Compact dashboard status', () => {
  test('shows native status without operator-only panels', async ({ page }) => {
    await page.goto('/');

    await test.step('compact adapter and native status are visible', async () => {
      await expect(page.getByText('Adapters', { exact: true })).toBeVisible();
      await expect(page.getByRole('button', { name: /Home Assistant Native integration/i })).toBeVisible();
    });

    await test.step('removed operator panels are absent', async () => {
      await expect(page.getByText('Diagnostics', { exact: true })).toHaveCount(0);
      await expect(page.getByText('Guided recovery', { exact: true })).toHaveCount(0);
      await expect(page.getByRole('button', { name: 'Export support bundle' })).toHaveCount(0);
    });
  });
});
