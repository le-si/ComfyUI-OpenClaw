import { expect } from '@playwright/test';

export async function mockComfyUiCore(page) {
  // Mock ComfyUI core module import used by web/openclaw.js
  await page.route('**/scripts/app.js', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/javascript',
      body: 'export const app = window.app;',
    });
  });

  // Mock ComfyUI api module used by web/openclaw_comfy_api.js
  await page.route('**/scripts/api.js', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/javascript',
      body: `
        export const api = {
          fetchApi: async (route, options) => {
             // Prefix with /api if not already present (shim logic simulation)
             const url = "/api" + route;
             return fetch(url, options);
          },
          apiURL: (route) => "/api" + route,
          fileURL: (route) => route // Simplified for test
        };
      `,
    });
  });
}

export async function waitForMoltbotReady(page) {
  await page.waitForFunction(() => window.__moltbotTestReady === true, null, { timeout: 30_000 });

  // Basic sanity: header + tab bar exists
  await expect(page.locator('.moltbot-header')).toBeVisible();
  await expect(page.locator('.moltbot-tabs')).toBeVisible();
}

export async function clickTab(page, title) {
  const tab = page.locator('.moltbot-tab', { hasText: title });
  await tab.click();
}
