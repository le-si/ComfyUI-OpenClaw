/**
 * OpenClaw API Wrapper (R7)
 * Provides consistent fetch usage, timeout handling, and type-safe response shapes.
 */
import { OpenClawSession } from "./openclaw_session.js";
import { fetchApi, apiURL, fileURL } from "./openclaw_comfy_api.js";

export class OpenClawAPI {
    constructor() {
        // baseUrl is handled by ComfyUI shim provided via fetchApi
        this.prefix = "/openclaw";
        this.legacyPrefix = "/moltbot";
    }

    /**
     * Gets the admin token from session storage (if available).
     */
    _getAdminToken() {
        return OpenClawSession.getAdminToken() || "";
    }

    _path(suffix) {
        return `${this.prefix}${suffix}`;
    }

    _adminTokenHeaders(token) {
        const t = token || this._getAdminToken();
        return {
            "X-OpenClaw-Admin-Token": t,
            "X-Moltbot-Admin-Token": t, // legacy
        };
    }

    /**
     * Generic fetch wrapper with timeout and error normalization.
     * @param {string} url - The URL to fetch
     * @param {object} options - Fetch options
     * @param {number} options.timeout - Timeout in ms (default: 10000)
     * @param {AbortSignal} options.signal - Optional AbortSignal from caller (R38-Lite)
     */
    async fetch(url, options = {}) {
        const { timeout = 10000, signal: externalSignal, ...fetchOptions } = options;

        // R38-Lite: Support both internal timeout and external abort signal
        const controller = new AbortController();
        let timedOut = false;
        let cancelledByCaller = false;
        const timeoutId = setTimeout(() => {
            timedOut = true;
            controller.abort();
        }, timeout);

        // If caller provides signal, listen for abort
        if (externalSignal) {
            if (externalSignal.aborted) {
                cancelledByCaller = true;
                controller.abort();
            } else {
                externalSignal.addEventListener(
                    "abort",
                    () => {
                        cancelledByCaller = true;
                        controller.abort();
                    },
                    { once: true }
                );
            }
        }

        try {
            // R26: Use ComfyUI shim (fetchApi) which handles base path automatically
            let response = null;

            const candidates = [];
            if (typeof url === "string") {
                candidates.push(url);
                if (url.startsWith(this.prefix + "/")) {
                    candidates.push(url.replace(this.prefix, this.legacyPrefix));
                } else if (url.startsWith(this.legacyPrefix + "/")) {
                    candidates.push(url.replace(this.legacyPrefix, this.prefix));
                }
            } else {
                candidates.push(url);
            }

            // 1) Try fetchApi (preferred)
            for (const candidate of candidates) {
                response = await fetchApi(candidate, {
                    ...fetchOptions,
                    signal: controller.signal,
                });
                if (response.status !== 404) break;
            }

            // 2) Try direct non-/api route as a hardened fallback (legacy loader / routing order issues)
            if (response && response.status === 404 && typeof url === "string") {
                for (const candidate of candidates) {
                    try {
                        response = await fetch(fileURL(candidate), {
                            ...fetchOptions,
                            signal: controller.signal,
                        });
                        if (response.status !== 404) break;
                    } catch {
                        // ignore and keep trying
                    }
                }
            }

            clearTimeout(timeoutId);

            // Best-effort body parsing
            let data = null;
            const contentType = response?.headers?.get("content-type");
            if (contentType && contentType.includes("application/json")) {
                try {
                    data = await response.json();
                } catch (e) {
                    // Ignore JSON parse error, data remains null
                }
            } else {
                try {
                    data = await response.text();
                } catch (e) { }
            }

            if (!response || !response.ok) {
                // Return normalized error shape
                return {
                    ok: false,
                    status: response ? response.status : 0,
                    error: (data && data.error) || (response ? response.statusText : "request_failed") || "request_failed",
                    data,
                };
            }

            return {
                ok: true,
                status: response.status,
                data,
            };

        } catch (err) {
            clearTimeout(timeoutId);
            // Network or Timeout/Abort errors
            const isAbort = err?.name === "AbortError";
            const abortKind = cancelledByCaller ? "cancelled" : (timedOut ? "timeout" : "cancelled");
            return {
                ok: false,
                status: 0,
                error: isAbort ? abortKind : "network_error",
                detail: err?.message,
            };
        }
    }

    // --- Endpoints ---

    async getHealth() {
        return this.fetch(this._path("/health"));
    }

    async getLogs(lines = 200) {
        return this.fetch(`${this._path("/logs/tail")}?lines=${lines}`);
    }

    async validateWebhook(payload) {
        return this.fetch(this._path("/webhook"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
    }

    async submitWebhook(payload) {
        return this.fetch(this._path("/webhook/submit"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
    }

    // R19: Capabilities
    async getCapabilities() {
        return this.fetch(this._path("/capabilities"));
    }

    // F17: ComfyUI History
    async getHistory(promptId) {
        // /history is a ComfyUI native endpoint.
        // ComfyUI's shim handles it if we pass "/history/..."?
        // Wait, ComfyUI endpoints are usually /history.
        // fetchApi('/history/...') maps to /api/history/...
        // ComfyUI backend registers /history?
        // Checking ComfyUI source: yes, app.routes.get("/history"...)
        // But usually under /api ?
        // Actually ComfyUI 'fetchApi' prefixes with '/api'.
        // Does 'history' live under '/api/history'? Yes.
        const res = await this.fetch(`/history/${promptId}`);
        if (!res.ok) return res;

        // ComfyUI returns: { "<prompt_id>": { ...historyItem... } }
        const data = res.data;
        const historyItem = (data && typeof data === "object") ? data[promptId] : null;
        return { ...res, data: historyItem };
    }

    // R25: Trace timeline (optional)
    async getTrace(promptId) {
        return this.fetch(`${this._path("/trace")}/${encodeURIComponent(promptId)}`);
    }

    // Helper: Build ComfyUI /view URL
    buildViewUrl(filename, subfolder = "", type = "output") {
        const params = new URLSearchParams({ filename, type });
        if (subfolder) params.set("subfolder", subfolder);
        // apiURL returns the full path including standard base
        return apiURL(`/view?${params.toString()}`);
    }

    // R21/F20: Get config
    async getConfig() {
        return this.fetch(this._path("/config"));
    }

    // R21/S13/F20: Update config (requires admin token)
    async putConfig(config, adminToken) {
        return this.fetch(this._path("/config"), {
            method: "PUT",
            headers: {
                "Content-Type": "application/json",
                ...this._adminTokenHeaders(adminToken),
            },
            body: JSON.stringify(config),
        });
    }

    // F20: Test LLM connection (uses effective config, no api_key in frontend)
    async runLLMTest() {
        return this.fetch(this._path("/llm/test"), {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...this._adminTokenHeaders(),
            },
            body: JSON.stringify({}), // Empty body = use effective config
            timeout: 30000,
        });
    }

    // Backwards compatibility alias for settings_tab.js
    async testLLM(adminToken, overrides = null) {
        return this.fetch(this._path("/llm/test"), {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...this._adminTokenHeaders(adminToken),
            },
            // IMPORTANT: Settings UI uses this to test the currently selected provider/model
            // without requiring a config "Save" first. Backend accepts an empty body too.
            body: JSON.stringify(overrides || {}),
            timeout: 30000,
        });
    }

    // F20+: Fetch remote model list (best-effort; admin boundary)
    async getModelList(providerId, adminToken) {
        const q = providerId ? `?provider=${encodeURIComponent(providerId)}` : "";
        return this.fetch(`${this._path("/llm/models")}${q}`, {
            method: "GET",
            headers: {
                ...this._adminTokenHeaders(adminToken),
            },
            timeout: 30000,
        });
    }

    // --- S25: Secrets Management (Admin-gated) ---

    /**
     * Get secrets status (NO VALUES).
     * Admin boundary (token if configured; otherwise loopback-only).
     */
    async getSecretsStatus(adminToken) {
        return this.fetch(this._path("/secrets/status"), {
            method: "GET",
            headers: {
                ...this._adminTokenHeaders(adminToken),
            },
        });
    }

    /**
     * Save API key to server store.
     * Admin boundary (token if configured; otherwise loopback-only).
     *
     * @param {string} provider - Provider ID ("openai", "anthropic", "generic")
     * @param {string} apiKey - API key value (NEVER logged)
     * @param {string} adminToken - Admin token
     */
    async saveSecret(provider, apiKey, adminToken) {
        return this.fetch(this._path("/secrets"), {
            method: "PUT",
            headers: {
                "Content-Type": "application/json",
                ...this._adminTokenHeaders(adminToken),
            },
            body: JSON.stringify({
                provider: provider,
                api_key: apiKey,
            }),
        });
    }

    /**
     * Clear provider secret.
     * Admin boundary (token if configured; otherwise loopback-only).
     */
    async clearSecret(provider, adminToken) {
        return this.fetch(this._path(`/secrets/${encodeURIComponent(provider)}`), {
            method: "DELETE",
            headers: {
                ...this._adminTokenHeaders(adminToken),
            },
        });
    }

    // --- Assist Endpoints (F8/F21) ---

    /**
     * Run Prompt Planner.
     * @param {object} params - { profile, requirements, style_directives, seed }
     * @param {AbortSignal} signal - Optional AbortSignal for cancellation (R38-Lite)
     */
    async runPlanner(params, signal = null) {
        return this.fetch(this._path("/assist/planner"), {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...this._adminTokenHeaders(),
            },
            body: JSON.stringify(params),
            timeout: 60000, // LLM calls may be slow
            signal, // R38-Lite: Pass signal
        });
    }

    /**
     * Run Prompt Refiner.
     * @param {object} params - { image_b64, orig_positive, orig_negative, issue, params_json, goal }
     * @param {AbortSignal} signal - Optional AbortSignal for cancellation (R38-Lite)
     */
    async runRefiner(params, signal = null) {
        return this.fetch(this._path("/assist/refiner"), {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...this._adminTokenHeaders(),
            },
            body: JSON.stringify(params),
            timeout: 60000,
            signal, // R38-Lite: Pass signal
        });
    }

    // --- F22: Presets ---

    async listPresets(category) {
        const query = category ? `?category=${encodeURIComponent(category)}` : "";
        return this.fetch(`${this._path("/presets")}${query}`, {
            headers: {
                ...this._adminTokenHeaders(),
            },
        });
    }

    async getPreset(id) {
        return this.fetch(`${this._path("/presets")}/${encodeURIComponent(id)}`, {
            headers: {
                ...this._adminTokenHeaders(),
            },
        });
    }

    async createPreset(data) {
        return this.fetch(this._path("/presets"), {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...this._adminTokenHeaders(),
            },
            body: JSON.stringify(data),
        });
    }

    async updatePreset(id, data) {
        return this.fetch(`${this._path("/presets")}/${encodeURIComponent(id)}`, {
            method: "PUT",
            headers: {
                "Content-Type": "application/json",
                ...this._adminTokenHeaders(),
            },
            body: JSON.stringify(data),
        });
    }

    async deletePreset(id) {
        return this.fetch(`${this._path("/presets")}/${encodeURIComponent(id)}`, {
            method: "DELETE",
            headers: {
                ...this._adminTokenHeaders(),
            },
        });
    }
    // --- S7: Approval Gates ---

    async getApprovals({ status, limit = 100, offset = 0 } = {}) {
        const params = new URLSearchParams({ limit, offset });
        if (status) params.set("status", status);

        return this.fetch(`${this._path("/approvals")}?${params.toString()}`, {
            headers: {
                ...this._adminTokenHeaders(),
            },
        });
    }

    async getApproval(id) {
        return this.fetch(`${this._path("/approvals")}/${encodeURIComponent(id)}`, {
            headers: {
                ...this._adminTokenHeaders(),
            },
        });
    }

    async approveRequest(id, { actor = "web_user", autoExecute = true } = {}) {
        return this.fetch(`${this._path("/approvals")}/${encodeURIComponent(id)}/approve`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...this._adminTokenHeaders(),
            },
            body: JSON.stringify({ actor, auto_execute: autoExecute }),
        });
    }

    async rejectRequest(id, { actor = "web_user" } = {}) {
        return this.fetch(`${this._path("/approvals")}/${encodeURIComponent(id)}/reject`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...this._adminTokenHeaders(),
            },
            body: JSON.stringify({ actor }),
        });
    }

    // --- S8/F11: Asset Packs ---

    async getPacks() {
        return this.fetch(this._path("/packs"), {
            headers: {
                ...this._adminTokenHeaders(),
            },
        });
    }

    async importPack(file, overwrite = false) {
        const formData = new FormData();
        formData.append("file", file);

        const query = overwrite ? "?overwrite=true" : "";

        return this.fetch(`${this._path("/packs/import")}${query}`, {
            method: "POST",
            headers: {
                ...this._adminTokenHeaders(),
                // Let browser set Content-Type for FormData
            },
            body: formData,
        });
    }

    async exportPack(name, version) {
        // Return URL for download (or blob fetch if needed)
        // Since it requires a token, we might need to fetch blob
        // But for simplicity, we can use a token parameter if supported, or fetch blob and create object URL.

        // Fetch as blob
        // R26: Use fetchApi to ensure base path
        const primaryPath = `${this._path("/packs/export")}/${encodeURIComponent(name)}/${encodeURIComponent(version)}`;
        const legacyPath = primaryPath.replace(this.prefix, this.legacyPrefix);

        const headers = this._adminTokenHeaders();

        let res = await fetchApi(primaryPath, { headers });
        if (res.status === 404) res = await fetchApi(legacyPath, { headers });

        if (res.status === 404) {
            try {
                res = await fetch(fileURL(primaryPath), { headers });
            } catch { }
        }
        if (res.status === 404) {
            try {
                res = await fetch(fileURL(legacyPath), { headers });
            } catch { }
        }

        if (res.ok) {
            const blob = await res.blob();
            return { ok: true, data: blob };
        }

        // If error, try to parse json error
        let error = "Download failed";
        try {
            const json = await res.json();
            error = json.error || error;
        } catch (e) { }

        return { ok: false, error };
    }

    async deletePack(name, version) {
        return this.fetch(`${this._path("/packs")}/${encodeURIComponent(name)}/${encodeURIComponent(version)}`, {
            method: "DELETE",
            headers: {
                ...this._adminTokenHeaders(),
            },
        });
    }

    // --- R42/F28: Preflight & Explorer ---

    async runPreflight(workflow) {
        return this.fetch(this._path("/preflight"), {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...this._adminTokenHeaders(),
            },
            body: JSON.stringify(workflow),
        });
    }

    async getInventory() {
        return this.fetch(this._path("/preflight/inventory"), {
            method: "GET",
            headers: {
                ...this._adminTokenHeaders(),
            },
        });
    }

    // --- R47: Checkpoints ---

    async listCheckpoints() {
        return this.fetch(this._path("/checkpoints"), {
            headers: { ...this._adminTokenHeaders() }
        });
    }

    async createCheckpoint(name, workflow, description = "") {
        return this.fetch(this._path("/checkpoints"), {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...this._adminTokenHeaders()
            },
            body: JSON.stringify({ name, workflow, description })
        });
    }

    async getCheckpoint(id) {
        return this.fetch(`${this._path("/checkpoints")}/${encodeURIComponent(id)}`, {
            headers: { ...this._adminTokenHeaders() }
        });
    }

    async deleteCheckpoint(id) {
        return this.fetch(`${this._path("/checkpoints")}/${encodeURIComponent(id)}`, {
            method: "DELETE",
            headers: { ...this._adminTokenHeaders() }
        });
    }

    // --- R71: Job Events ---

    /**
     * Poll for recent events (fallback).
     * @param {number} lastSeq - Sequence ID to start from
     */
    async getEvents(lastSeq = 0) {
        return this.fetch(`${this._path("/events")}?since=${lastSeq}`);
    }

    /**
     * Subscribe to SSE event stream.
     * @param {function} onEvent - Callback for events (eventData) => void
     * @param {function} onError - Callback for errors (error) => void
     * @returns {EventSource} The event source instance (caller must .close() it)
     */
    subscribeEvents(onEvent, onError) {
        // Use apiURL from shim to get full path
        const url = apiURL(this._path("/events/stream"));
        const es = new EventSource(url);

        const handle = (e) => {
            if (!e.data) return;
            try {
                const data = JSON.parse(e.data);
                // Unified event type injection if missing
                if (!data.event_type && e.type !== "message") {
                    data.event_type = e.type;
                }
                onEvent(data);
            } catch (err) {
                console.warn("[OpenClaw] Failed to parse SSE event:", err);
            }
        };

        es.onmessage = handle;
        es.addEventListener("queued", handle);
        es.addEventListener("running", handle);
        es.addEventListener("completed", handle);
        es.addEventListener("failed", handle);

        es.onerror = (err) => {
            if (onError) onError(err);
        };

        return es;
    }
}

export const openclawApi = new OpenClawAPI();

// Legacy compatibility aliases (Moltbot -> OpenClaw)
export const MoltbotAPI = OpenClawAPI;
export const moltbotApi = openclawApi;
