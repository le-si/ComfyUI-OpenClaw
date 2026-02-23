import { test, expect } from '@playwright/test';
import { mockComfyUiCore, waitForOpenClawReady, clickTab } from '../utils/helpers.js';

test.describe('Settings Tab Stability', () => {
    test.beforeEach(async ({ page }) => {
        await mockComfyUiCore(page);

        // Mock Config GET & PUT
        await page.route('**/openclaw/config', async (route) => {
            if (route.request().method() === 'GET') {
                await route.fulfill({
                    status: 200,
                    contentType: 'application/json',
                    body: JSON.stringify({
                        ok: true,
                        config: {
                            provider: 'openai',
                            model: 'gpt-4o',
                            base_url: '',
                            timeout_sec: 120,
                            max_retries: 3
                        },
                        sources: { provider: 'default' },
                        providers: [
                            { id: 'openai', label: 'OpenAI' },
                            { id: 'anthropic', label: 'Anthropic' },
                            { id: 'custom', label: 'Custom' }
                        ],
                        apply: {}
                    }),
                });
            } else if (route.request().method() === 'PUT') {
                // Mock Config PUT (R53 feedback)
                await route.fulfill({
                    status: 200,
                    contentType: 'application/json',
                    body: JSON.stringify({
                        ok: true,
                        apply: {
                            applied_now: ['provider', 'model'],
                            restart_required: []
                        }
                    }),
                });
            }
        });

        // Mock Logs (Dependency)
        await page.route('**/openclaw/logs/tail*', async (route) => {
            await route.fulfill({ status: 200, body: JSON.stringify({ ok: true, content: [] }) });
        });
        // Mock Health (Dependency)
        // Note: harness mock for health is overridden by page.route if this line executes?
        // Actually, harness uses window.fetch. Mocking window.fetch happens in harness.
        // If we want to support config in health, we modified harness directly.
        // So this line is REDUNDANT or IGNORED for calls from UI?
        // But good to keep for any network fallbacks.
        await page.route('**/openclaw/health', async (route) => {
            await route.fulfill({ status: 200, body: JSON.stringify({ ok: true, config: { llm_key_configured: true }, pack: { version: 'test' } }) });
        });

        await page.goto('test-harness.html');
        await waitForOpenClawReady(page);
    });

    test('loads settings without flicker and populates fields', async ({ page }) => {
        await clickTab(page, 'Settings');

        // Check for specific fields to ensure render complete
        // We expect the provider select to be 'openai'
        const providerSelect = page.locator('select').first();
        // Wait for it to be visible to ensure "Loading..." is gone
        await expect(providerSelect).toBeVisible();
        await expect(providerSelect).toHaveValue('openai');

        // Model input should match
        // Note: The UI has a model select and input. The input is default visible.
        // Use first visible text input in settings tab logic (approximate but robust enough)
        const modelInput = page.locator('input[type="text"]').first();
        await expect(modelInput).toBeVisible();
        await expect(modelInput).toHaveValue('gpt-4o');

        // Ensure no 404 warning
        await expect(page.locator('text=Backend 404')).not.toBeVisible();
    });

    test('save triggers hot-reload feedback (R53)', async ({ page }) => {
        await clickTab(page, 'Settings');

        // Click Save (exact match to avoid "Save Key")
        const savePromise = page.waitForResponse(resp => resp.url().includes('/config') && resp.status() === 200);
        await page.getByRole('button', { name: 'Save', exact: true }).click();
        await savePromise;

        // Expect success message
        await expect(page.locator('.openclaw-status.ok')).toContainText('Saved!');
        await expect(page.locator('.openclaw-status.ok')).toContainText('Applied immediately');
    });
});
