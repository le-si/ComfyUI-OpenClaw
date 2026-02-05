/**
 * Adapter for ComfyUI's standard API shim.
 * Aligns custom fetch calls with ComfyUI's base path handling (e.g. /api/... or proxy prefixes)
 * Reference: R26/F24 Plan
 */

import { api } from "/scripts/api.js";

/**
 * Wrapper for api.fetchApi
 * @param {string} route - e.g. "/moltbot/health" -> becomes "/api/moltbot/health"
 * @param {object} options - fetch options
 */
export function fetchApi(route, options) {
    return api.fetchApi(route, options);
}

/**
 * Wrapper for api.apiURL
 * @param {string} route - e.g. "/view?filename=..." -> becomes "/api/view?..."
 */
export function apiURL(route) {
    return api.apiURL(route);
}

/**
 * Wrapper for api.fileURL (no /api prefix)
 * @param {string} route - e.g. "/moltbot/health" -> becomes "<basePath>/moltbot/health"
 */
export function fileURL(route) {
    return api.fileURL(route);
}
