import { test, expect } from '@playwright/test';

test.describe('Recovery boundary and support export', () => {
  test('support bundle downloads without credentials', async ({ page }) => {
    await page.goto('/');

    await test.step('export support bundle', async () => {
      const downloadPromise = page.waitForEvent('download');
      await page.getByRole('button', { name: 'Export support bundle' }).click();
      const download = await downloadPromise;
      expect(download.suggestedFilename()).toContain('bl-haos-support-bundle');
    });
  });
});
