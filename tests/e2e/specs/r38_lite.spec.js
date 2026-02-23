import { test, expect } from '@playwright/test';
import { mockComfyUiCore, waitForOpenClawReady, clickTab } from '../utils/helpers.js';

test.describe('R38 Lite UX lifecycle', () => {
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

        await page.goto('test-harness.html');
        await waitForOpenClawReady(page);
    });

    test('Planner shows staged loading + elapsed timer and then succeeds', async ({ page }) => {
        const pageErrors = [];
        page.on('pageerror', (e) => pageErrors.push(e.message));

        await page.route('**/openclaw/assist/planner', async (route) => {
            await new Promise((resolve) => setTimeout(resolve, 1700));
            try {
                await route.fulfill({
                    status: 200,
                    contentType: 'application/json',
                    body: JSON.stringify({
                        positive: 'A foggy mountain valley',
                        negative: 'lowres, blurry',
                        params: { width: 1024, height: 1024 },
                    }),
                });
            } catch {
                // Request may already be aborted by navigation/cancel in edge races.
            }
        });

        await clickTab(page, 'Planner');
        await page.locator('#planner-run-btn').click();

        await expect(page.locator('#planner-loading')).toBeVisible();
        await expect(page.locator('#planner-stage')).toContainText('Waiting for provider response...', { timeout: 2000 });
        await expect(page.locator('#planner-elapsed')).not.toHaveText('Elapsed: 0s', { timeout: 2500 });

        await expect(page.locator('#planner-out-pos')).toHaveValue('A foggy mountain valley');
        await expect(page.locator('#planner-out-neg')).toHaveValue('lowres, blurry');
        await expect(page.locator('#planner-loading')).toBeHidden();
        await expect(page.locator('#planner-run-btn')).toBeVisible();

        expect(pageErrors).toEqual([]);
    });

    test('Refiner cancel keeps UI stable and retry succeeds', async ({ page }) => {
        const pageErrors = [];
        page.on('pageerror', (e) => pageErrors.push(e.message));

        let callCount = 0;
        await page.route('**/openclaw/assist/refiner', async (route) => {
            callCount += 1;

            if (callCount === 1) {
                await new Promise((resolve) => setTimeout(resolve, 2500));
                try {
                    await route.fulfill({
                        status: 200,
                        contentType: 'application/json',
                        body: JSON.stringify({
                            refined_positive: 'stale response should be ignored',
                            refined_negative: 'stale',
                            rationale: 'stale',
                        }),
                    });
                } catch {
                    // Cancel path may abort before fulfill.
                }
                return;
            }

            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    refined_positive: 'clean cinematic portrait lighting',
                    refined_negative: 'overexposed, noisy',
                    rationale: 'Adjusted lighting and constrained noise artifacts.',
                }),
            });
        });

        await clickTab(page, 'Refiner');
        await page.locator('#refiner-orig-pos').fill('portrait, natural light');
        await page.locator('#refiner-issue').fill('too noisy and inconsistent lighting');

        await page.locator('#refiner-run-btn').click();
        await expect(page.locator('#refiner-loading')).toBeVisible();
        await expect(page.locator('#refiner-stage')).toContainText('Waiting for provider response...', { timeout: 2000 });

        await page.locator('#refiner-cancel-btn').click();
        await expect(page.locator('#refiner-loading')).toBeHidden();
        await expect(page.locator('#refiner-run-btn')).toBeVisible();
        await expect(page.locator('.openclaw-toast')).toContainText('Request cancelled by user');

        await page.locator('#refiner-run-btn').click();

        await expect(page.locator('#refiner-new-pos')).toHaveValue('clean cinematic portrait lighting');
        await expect(page.locator('#refiner-new-neg')).toHaveValue('overexposed, noisy');
        await expect(page.locator('#refiner-rationale')).toContainText('Adjusted lighting');

        expect(pageErrors).toEqual([]);
    });
});
