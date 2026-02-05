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

function ensurePythonShimIfNeeded(env) {
  // If `python` is missing but python3 exists, add a local shim folder.
  // CI images usually provide python; this helps WSL environments.
  const which = (cmd) => spawnSync('bash', ['-lc', `command -v ${cmd}`], { encoding: 'utf-8' });
  const py = which('python');
  if (py.status === 0) return env;

  const py3 = which('python3');
  if (py3.status !== 0) return env;

  const binDir = path.join(process.cwd(), '.tmp', 'bin');
  ensureDir(binDir);

  const shimPath = path.join(binDir, 'python');
  try {
    fs.symlinkSync(py3.stdout.trim(), shimPath);
  } catch {
    // ignore if already exists
  }

  env.PATH = `${binDir}:${env.PATH || ''}`;
  return env;
}

function runNpmExec(args, { label }) {
  if (process.env.npm_execpath) {
    const res = spawnSync(process.execPath, [process.env.npm_execpath, 'exec', '--', ...args], {
      stdio: 'inherit',
      env,
    });
    return res;
  }
  return spawnSync('npm', ['exec', '--', ...args], { stdio: 'inherit', env });
}

function runPlaywrightCommand(args, { label }) {
  if (process.platform === 'win32') {
    const res = runNpmExec(args, { label });
    if (res.error && res.error.code === 'ENOENT') {
      console.error(
        `[OpenClaw] Failed to run ${label}: npm not found in PATH (and npm_execpath not set)`,
      );
      process.exit(1);
    }
    return res;
  }

  const res = spawnSync(getNpxCommand(), args, { stdio: 'inherit', env });
  if (res.error && res.error.code === 'ENOENT') {
    const fallback = runNpmExec(args, { label });
    if (fallback.error) {
      console.error(`[OpenClaw] Failed to run ${label}:`, fallback.error);
      process.exit(1);
    }
    return fallback;
  }
  return res;
}

function ensurePlaywrightBrowsersIfNeeded() {
  // In CI, ensure Playwright browsers are installed; otherwise tests fail with exit code 1.
  if (!process.env.CI && process.env.OPENCLAW_PLAYWRIGHT_INSTALL !== '1') {
    return;
  }
  const args = ['playwright', 'install'];
  if (process.platform === 'linux') {
    args.push('--with-deps');
  }
  const res = runPlaywrightCommand(args, { label: 'Playwright install' });
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

function getNpxCommand() {
  return process.platform === 'win32' ? 'npx.cmd' : 'npx';
}

if (isWSL() && isDrvFsCwd()) {
  const tmpDir = path.join(process.cwd(), '.tmp', 'playwright');
  ensureDir(tmpDir);
  env.TMPDIR = tmpDir;
  env.TMP = tmpDir;
  env.TEMP = tmpDir;
}

ensurePythonShimIfNeeded(env);
ensurePlaywrightBrowsersIfNeeded();

let res = runPlaywrightCommand(['playwright', 'test'], { label: 'Playwright' });
if (res.error) {
  console.error('[OpenClaw] Failed to run Playwright:', res.error);
  process.exit(1);
}
process.exit((res.status === undefined || res.status === null) ? 1 : res.status);
