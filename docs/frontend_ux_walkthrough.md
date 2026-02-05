# Frontend UX Walkthrough (ComfyUI-OpenClaw)

This document summarizes the current OpenClaw sidebar UI structure and how to verify it after changes.

## UI Structure

- Entry: `web/openclaw.js` registers the extension and sidebar tab.
- Shell: `web/openclaw_ui.js` renders the header, tab bar, and tab panes.
- Tabs: `web/openclaw_tabs.js` manages tab registration, rendering, and remount safety.
- API: `web/openclaw_api.js` provides a normalized fetch wrapper and OpenClaw endpoints (legacy Moltbot endpoints still work).
- Styles: `web/openclaw.css` provides shared design tokens and component classes.
- Errors: `web/openclaw_utils.js` provides `showError()` / `clearError()` helpers.

## Feature Gating (Capabilities)

- Backend exposes `GET /openclaw/capabilities` (legacy `/moltbot/capabilities` still works).
- Frontend fetches capabilities during setup and conditionally registers tabs:
  - `assist_planner` → Planner
  - `assist_refiner` → Refiner
  - `scheduler` → Variants (current gating)
  - `presets` → Library
  - `approvals` → Approvals

If capabilities are unavailable, the full tab set is registered to surface actionable errors (instead of “missing tabs”).

## Quick Manual Checks

1. Open ComfyUI and confirm OpenClaw appears in the sidebar.
2. Switch between all visible tabs multiple times (and reopen the sidebar if possible) and ensure panes do not go blank.
3. Planner: click **Plan Generation** with minimal input and confirm either results render or a readable error appears.
4. Refiner: click **Refine Prompts** (with or without image) and confirm either results render or a readable error appears.
5. Library/Approvals: if backend endpoints are not enabled, confirm the UI shows a clear error state (no crashes).

## E2E (Playwright) Checks

- Run: `npm test`
- Tests live in: `tests/e2e/specs/`
- Harness: `tests/e2e/test-harness.html` (mocks ComfyUI core + basic OpenClaw API calls)
