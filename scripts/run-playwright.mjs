import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

function isWSL() {
  return process.platform === 'linux' && !!process.env.WSL_DISTRO_NAME;
}

function isDrvFsCwd() {
  const cwd = process.cwd();
  return cwd.startsWith('/mnt/');
}

function ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
}

function resolvePlaywrightCli() {
  const candidates = [
    path.join(process.cwd(), 'node_modules', 'playwright', 'cli.js'),
    path.join(process.cwd(), 'node_modules', '@playwright', 'test', 'cli.js'),
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return null;
}

function runPlaywright(args, { label }) {
  const cli = resolvePlaywrightCli();
  if (!cli) {
    console.error(
      `[OpenClaw] Failed to run ${label}: Playwright CLI not found. Did you run 'npm install'?`,
    );
    process.exit(1);
  }
  return spawnSync(process.execPath, [cli, ...args], { stdio: 'inherit', env });
}

function ensurePlaywrightBrowsersIfNeeded() {
  // In CI, ensure Playwright browsers are installed; otherwise tests fail with exit code 1.
  if (!process.env.CI && process.env.OPENCLAW_PLAYWRIGHT_INSTALL !== '1') {
    return;
  }
  // Default to Chromium only (fast + matches CI workflow); allow override.
  const browsers = (process.env.OPENCLAW_PLAYWRIGHT_BROWSERS || 'chromium')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);

  const args = ['install', ...browsers];
  if (process.platform === 'linux' && process.env.OPENCLAW_PLAYWRIGHT_WITH_DEPS === '1') {
    args.push('--with-deps');
  }
  const res = runPlaywright(args, { label: 'Playwright install' });
  if (res.error) {
    console.error('[OpenClaw] Failed to run Playwright install:', res.error);
    process.exit(1);
  }
  if (res.status !== 0) {
    console.error(`[OpenClaw] Playwright install failed with exit code ${res.status}`);
    process.exit(res.status ?? 1);
  }
}

const env = { ...process.env };

if (isWSL() && isDrvFsCwd()) {
  const tmpDir = path.join(process.cwd(), '.tmp', 'playwright');
  ensureDir(tmpDir);
  env.TMPDIR = tmpDir;
  env.TMP = tmpDir;
  env.TEMP = tmpDir;
}

ensurePlaywrightBrowsersIfNeeded();

let res = runPlaywright(['test'], { label: 'Playwright' });
if (res.error) {
  console.error('[OpenClaw] Failed to run Playwright:', res.error);
  process.exit(1);
}
process.exit((res.status === undefined || res.status === null) ? 1 : res.status);
