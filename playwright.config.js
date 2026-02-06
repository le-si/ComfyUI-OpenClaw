// @ts-check
const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: 'tests/e2e/specs',
  timeout: 30_000,
  retries: 0,
  use: {
    baseURL: 'http://127.0.0.1:3000/tests/e2e/',
    headless: true,
  },
  webServer: {
    command: `${process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3')} -m http.server 3000`,
    url: 'http://127.0.0.1:3000/tests/e2e/test-harness.html',
    reuseExistingServer: true,
    timeout: 30_000,
  },
});
