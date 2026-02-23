// @ts-check
const { defineConfig } = require('@playwright/test');

const e2ePort = process.env.OPENCLAW_E2E_PORT || '3000';
const e2eBase = `http://127.0.0.1:${e2ePort}`;

module.exports = defineConfig({
  testDir: 'tests/e2e/specs',
  timeout: 30_000,
  retries: 0,
  use: {
    baseURL: `${e2eBase}/tests/e2e/`,
    headless: true,
  },
  webServer: {
    // IMPORTANT: allow overriding port for environments where 3000 is blocked/reserved.
    command: `${process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3')} -m http.server ${e2ePort}`,
    url: `${e2eBase}/tests/e2e/test-harness.html`,
    reuseExistingServer: true,
    timeout: 30_000,
  },
});
