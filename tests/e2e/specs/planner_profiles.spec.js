import { test, expect } from '@playwright/test';
import { mockComfyUiCore, waitForOpenClawReady, clickTab } from '../utils/helpers.js';

test.describe('Planner profile registry', () => {
    test.beforeEach(async ({ page }) => {
        await mockComfyUiCore(page);

        await page.route('**/openclaw/config', async (route) => {
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, config: {}, apply: {} }) });
        });
        await page.route('**/openclaw/logs/tail*', async (route) => {
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, content: [] }) });
        });
        await page.route('**/openclaw/health', async (route) => {
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, pack: { version: 'test' } }) });
        });
        await page.route('**/openclaw/assist/planner/profiles', async (route) => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    profiles: [
                        { id: 'Photo', label: 'Photo Real', description: 'photo', version: '1.0' },
                        { id: 'Sketch', label: 'Sketch Draft', description: 'sketch', version: '1.0' },
                    ],
                    default_profile: 'Sketch',
                }),
            });
        });

        await page.goto('test-harness.html');
        await waitForOpenClawReady(page);
    });

    test('Planner tab populates profile dropdown from backend registry', async ({ page }) => {
        await clickTab(page, 'Planner');

        const select = page.locator('#planner-profile');
        await expect(select).toHaveValue('Sketch');
        await expect(select.locator('option')).toHaveCount(2);
        await expect(select.locator('option').nth(0)).toHaveText('Photo Real');
        await expect(select.locator('option').nth(1)).toHaveText('Sketch Draft');
    });
});
