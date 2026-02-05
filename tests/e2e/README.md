# ComfyUI-OpenClaw - E2E Tests

Playwright-based end-to-end tests for the OpenClaw sidebar UI.

## Setup

```bash
npm install
npx playwright install chromium
```

## Run

```bash
npm test

# optional
npm run test:ui
npm run test:headed
npm run test:debug
npm run test:report
```

## Structure

```
tests/e2e/
  specs/        # Playwright specs
  mocks/        # ComfyUI mocks
  utils/        # shared helpers
  test-harness.html
```
