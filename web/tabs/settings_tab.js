/**
 * S26: Settings Tab (simplified, collapsible secrets)
 */
import { moltbotApi } from "../openclaw_api.js";
import { MoltbotSession } from "../openclaw_session.js";
import { getAdminErrorMessage } from "../admin_errors.js";

export const settingsTab = {
    id: "settings",
    title: "Settings",
    icon: "pi pi-cog",
    render: async (container) => {
        // IMPORTANT (UI layout): `.moltbot-content` has `overflow: hidden`.
        // This tab MUST render its own scroll container (`.moltbot-scroll-area`),
        // otherwise lower sections (e.g. UI Key Store) will be clipped with no way to scroll.
        container.innerHTML = `
            <div class="moltbot-panel">
                <div class="moltbot-scroll-area" id="openclaw-settings-scroll">
                    <div class="openclaw-loading-gate" style="padding:16px;text-align:center;opacity:0.6;">Initializing…</div>
                </div>
            </div>
        `;
        const scroll = container.querySelector("#openclaw-settings-scroll");

        // F39: Capability probe FIRST — gate optional features before any rendering.
        let capabilities = {};
        try {
            const capRes = await moltbotApi.getCapabilities();
            if (capRes.ok && capRes.data?.features) {
                capabilities = capRes.data.features;
            }
        } catch { /* non-fatal */ }

        const [healthRes, logRes, configRes] = await Promise.all([
            moltbotApi.getHealth(),
            moltbotApi.getLogs(50),
            moltbotApi.getConfig(),
        ]);

        // F39: Remove loading gate (readiness gating prevents first-render flicker)
        scroll.innerHTML = "";

        // If everything is 404, backend routes not registered

        // -- UI Boot Diagnostics / Backend Warning --
        const all404 = [healthRes, logRes, configRes].every(r => r && r.ok === false && r.status === 404);
        if (all404) {
            const warn = createSection("Backend Not Loaded");
            const hint = document.createElement("div");
            hint.className = "moltbot-note";
            hint.style.borderLeft = "4px solid #ff4444";
            hint.innerHTML = `
                OpenClaw UI loaded, but the server endpoints returned <code>HTTP 404</code>.
                This usually means ComfyUI did not load the Python part of this custom node pack.
                <br/><br/>
                Check ComfyUI startup logs for errors while importing <code>ComfyUI-OpenClaw</code>/<code>Comfyui-OpenClaw</code>,
                then restart ComfyUI.
                <br/><br/>
                Expected endpoints:
                <ul>
                  <li><code>/openclaw/health</code> (legacy: <code>/moltbot/health</code>)</li>
                  <li><code>/openclaw/config</code></li>
                  <li><code>/openclaw/logs/tail</code></li>
                </ul>
            `;
            warn.appendChild(hint);
            scroll.appendChild(warn);
        }

        // F39: Show degraded-state banner when capabilities are missing or partial
        if (!all404 && healthRes.ok && Object.keys(capabilities).length === 0) {
            const degradedWarn = createSection("Limited Mode");
            const degradedHint = document.createElement("div");
            degradedHint.className = "moltbot-note";
            degradedHint.style.borderLeft = "4px solid #ffaa00";
            degradedHint.innerHTML = `
                <b>⚠ Capabilities endpoint unavailable.</b> Some features may be hidden or behave differently.
                This can happen if the backend pack version is older than the frontend UI.
                <br/>Consider updating ComfyUI-OpenClaw to the latest version.
            `;
            degradedWarn.appendChild(degradedHint);
            scroll.appendChild(degradedWarn);
        }

        // -- System Health & Diagnostics --
        const healthSec = createSection("System Health");

        // F26: Diagnostics Block (Shim status + ComfyUI version)
        const diagDetails = document.createElement("details");
        diagDetails.style.marginBottom = "10px";
        diagDetails.style.padding = "8px";
        diagDetails.style.background = "var(--comfy-input-bg)";
        diagDetails.style.borderRadius = "4px";
        diagDetails.style.fontSize = "12px";
        diagDetails.style.color = "var(--input-text)";

        // Detect Shim
        const hasShim = typeof window.comfyAPI?.fetchApi === "function" || typeof window.fetchApi === "function" || !!healthRes.ok;
        // Note: fetchApi is imported in module scope, not global. If request worked, shim worked.
        // Actually best check is if healthRes.ok or we can inspect 'moltbotApi.prefix' implicitly.

        const packVer = (healthRes.ok && healthRes.data?.pack?.version) || "Unknown";
        const basePath = (healthRes.ok && healthRes.data?.pack?.base_path) || "/openclaw (inferred)";
        const comfyVersion = await detectComfyUiVersion(moltbotApi);

        // Collapsed by default; auto-expand if errors
        diagDetails.open = all404 || !hasShim;

        const summary = document.createElement("summary");
        summary.style.display = "flex";
        summary.style.justifyContent = "space-between";
        summary.style.alignItems = "center";
        summary.style.cursor = "pointer";
        summary.innerHTML = `
            <span><b>UI Boot Status</b></span>
            <span>${all404 ? "⚠️ Backend 404" : "✓ Connected"}</span>
        `;

        const body = document.createElement("div");
        body.innerHTML = `
            <div style="margin-top:4px; opacity:0.8;">
                ComfyUI: ${comfyVersion || "Unknown"} | Pack: ${packVer} | Prefix: ${basePath}
            </div>
            <div style="margin-top:4px; font-size:11px; color:${hasShim ? "var(--input-text)" : "#ff6666"}">
                Shim: ${hasShim ? "✓ Detected" : "⚠️ Missing (shim broken)"}
            </div>
        `;

        diagDetails.appendChild(summary);
        diagDetails.appendChild(body);
        healthSec.appendChild(diagDetails);

        if (healthRes.ok) {
            const { pack, config, uptime_sec } = healthRes.data;
            addRow(healthSec, "Uptime", `${Math.floor(uptime_sec)}s`);

            const keyStatus = config.llm_key_configured
                ? "Configured"
                : (config.llm_key_required ? "Missing" : "Not Req");
            const keyClass = (config.llm_key_configured || !config.llm_key_required) ? "ok" : "error";
            addRow(healthSec, "API Key", keyStatus, keyClass);
        } else {
            if (!all404) {
                addRow(healthSec, "Status", "Error", "error");
                const detail = [
                    healthRes.status ? `HTTP ${healthRes.status}` : null,
                    healthRes.error || "request_failed",
                ].filter(Boolean).join(" — ");
                addRow(healthSec, "Detail", detail);
            }
        }
        scroll.appendChild(healthSec);

        // -- LLM Settings Section --
        const llmSec = createSection("LLM Settings");
        if (configRes.ok) {
            // R54: Null-safe destructuring with defaults
            const data = configRes.data || {};
            const config = data.config || {};
            const sources = data.sources || {};
            const providers = data.providers || [];
            // R53: Apply feedback (optional, for debug/toast later)
            const applyInfo = data.apply || {};
            // R70: Settings schema (for frontend validation)
            const schema = data.schema || {};


            // Provider dropdown
            const providerRow = createFormRow("Provider", sources.provider === "env");
            const providerSelect = document.createElement("select");
            providerSelect.className = "moltbot-input";
            providerSelect.disabled = sources.provider === "env";
            providers.forEach(p => {
                const opt = document.createElement("option");
                opt.value = p.id;
                opt.textContent = p.label;
                if (p.id === config.provider) opt.selected = true;
                providerSelect.appendChild(opt);
            });
            providerRow.appendChild(providerSelect);
            llmSec.appendChild(providerRow);

            // R60: Reset model list when provider changes (avoids showing stale models from another provider).
            const resetModelList = () => {
                modelsLoaded = false;
                lastLoadedModels = [];
                modelSelect.innerHTML = "";
                modelDatalist.innerHTML = "";
                modelsStatus.textContent = "";
                modelsStatus.className = "moltbot-status";
                updateModelUiVisibility();
            };
            providerSelect.onchange = () => resetModelList();

            // Model input
            const modelRow = createFormRow("Model", sources.model === "env");
            const modelWrap = document.createElement("div");
            modelWrap.style.display = "flex";
            modelWrap.style.gap = "8px";
            modelWrap.style.alignItems = "center";

            // Model selection UX:
            // - Default: free-text input (works even if model listing isn't supported).
            // - After "Load Models": show a real <select> for discoverability + still allow "Custom…".
            const modelInput = document.createElement("input");
            modelInput.type = "text";
            modelInput.className = "moltbot-input";
            modelInput.value = config.model || "";
            modelInput.disabled = sources.model === "env";
            modelInput.style.flex = "1";

            const modelSelect = document.createElement("select");
            modelSelect.className = "moltbot-input";
            modelSelect.disabled = sources.model === "env";
            modelSelect.style.flex = "1";
            modelSelect.style.display = "none"; // shown after models load

            const MODEL_CUSTOM = "__custom__";

            // Datalist for remote suggestions (used in custom/free-text mode)
            const modelListId = "openclaw-model-list";
            modelInput.setAttribute("list", modelListId);
            const modelDatalist = document.createElement("datalist");
            modelDatalist.id = modelListId;

            let lastLoadedModels = [];
            let modelsLoaded = false;

            const updateModelUiVisibility = () => {
                // IMPORTANT (UX): Users expect an actual dropdown after "Load Models" even if the current
                // model is not in the returned list (e.g., switching provider but model still set to an
                // old value like "gpt-4o-mini"). Keep the <select> visible and use "Custom…" as a bridge.
                const showSelect = modelsLoaded;
                const showInput = !modelsLoaded || modelSelect.value === MODEL_CUSTOM;

                modelSelect.style.display = showSelect ? "" : "none";
                modelInput.style.display = showInput ? "" : "none";
            };

            const populateModelSelect = (models) => {
                modelSelect.innerHTML = "";

                const customOpt = document.createElement("option");
                customOpt.value = MODEL_CUSTOM;
                customOpt.textContent = "Custom…";
                modelSelect.appendChild(customOpt);

                models.slice(0, 5000).forEach((m) => {
                    const opt = document.createElement("option");
                    opt.value = m;
                    opt.textContent = m;
                    modelSelect.appendChild(opt);
                });

                modelsLoaded = true;
                const current = (modelInput.value || "").trim();
                if (current && models.includes(current)) {
                    modelSelect.value = current;
                } else {
                    modelSelect.value = MODEL_CUSTOM;
                }
                updateModelUiVisibility();
            };

            modelSelect.onchange = () => {
                const v = modelSelect.value;
                if (v === MODEL_CUSTOM) {
                    updateModelUiVisibility();
                    modelInput.focus();
                    return;
                }
                modelInput.value = v;
                updateModelUiVisibility();
            };

            const refreshModelsBtn = document.createElement("button");
            refreshModelsBtn.className = "moltbot-btn moltbot-btn-secondary";
            refreshModelsBtn.textContent = "Load Models";
            refreshModelsBtn.disabled = false;
            refreshModelsBtn.title = "Fetch remote model list (admin boundary).";

            const modelsStatus = document.createElement("div");
            modelsStatus.className = "moltbot-status";
            modelsStatus.style.minWidth = "120px";

            let tokenInput; // Will be set below

            refreshModelsBtn.onclick = async () => {
                const token = (tokenInput?.value || MoltbotSession.getAdminToken() || "").trim();
                refreshModelsBtn.disabled = true;
                modelsStatus.textContent = "Loading...";
                modelsStatus.className = "moltbot-status";

                const res = await moltbotApi.getModelList(providerSelect.value, token);
                if (res.ok) {
                    modelDatalist.innerHTML = "";
                    const models = Array.isArray(res.data?.models) ? res.data.models : [];
                    lastLoadedModels = models;
                    models.slice(0, 5000).forEach(m => {
                        const opt = document.createElement("option");
                        opt.value = m;
                        modelDatalist.appendChild(opt);
                    });
                    populateModelSelect(models);
                    modelsStatus.textContent = `✓ ${models.length} models`;
                    modelsStatus.className = "moltbot-status ok";
                } else {
                    const detail = [
                        res.status ? `HTTP ${res.status}` : null,
                        res.error || "Failed",
                    ].filter(Boolean).join(" — ");
                    modelsStatus.textContent = `✗ ${detail}`;
                    modelsStatus.className = "moltbot-status error";
                }
                refreshModelsBtn.disabled = false;
            };

            modelWrap.appendChild(modelSelect);
            modelWrap.appendChild(modelInput);
            modelWrap.appendChild(refreshModelsBtn);
            modelWrap.appendChild(modelsStatus);

            modelRow.appendChild(modelWrap);
            modelRow.appendChild(modelDatalist);
            llmSec.appendChild(modelRow);

            // Base URL input
            const baseUrlRow = createFormRow("Base URL", sources.base_url === "env");
            const baseUrlInput = document.createElement("input");
            baseUrlInput.type = "text";
            baseUrlInput.className = "moltbot-input";
            baseUrlInput.value = config.base_url || "";
            baseUrlInput.placeholder = "Leave empty for provider default";
            baseUrlInput.disabled = sources.base_url === "env";
            baseUrlRow.appendChild(baseUrlInput);
            llmSec.appendChild(baseUrlRow);

            // R60: Reset model list when base URL changes (cache key includes base_url).
            baseUrlInput.onchange = () => resetModelList();

            // Timeout
            const timeoutRow = createFormRow("Timeout (sec)", sources.timeout_sec === "env");
            const timeoutInput = document.createElement("input");
            timeoutInput.type = "number";
            timeoutInput.className = "moltbot-input moltbot-input-sm";
            timeoutInput.value = config.timeout_sec || 120;
            timeoutInput.min = 5;
            timeoutInput.max = 300;
            timeoutInput.disabled = sources.timeout_sec === "env";
            timeoutRow.appendChild(timeoutInput);
            llmSec.appendChild(timeoutRow);

            // Max Retries
            const retriesRow = createFormRow("Max Retries", sources.max_retries === "env");
            const retriesInput = document.createElement("input");
            retriesInput.type = "number";
            retriesInput.className = "moltbot-input moltbot-input-sm";
            retriesInput.value = config.max_retries || 3;
            retriesInput.min = 0;
            retriesInput.max = 10;
            retriesInput.disabled = sources.max_retries === "env";
            retriesRow.appendChild(retriesInput);
            llmSec.appendChild(retriesRow);

            // --- Admin Token Section ---
            const tokenRow = createFormRow(
                "Admin Token",
                false,
                createHelpButton(
                    "Admin Token",
                    `
                    <p>The Admin Token authorizes <b>write</b> actions (save config, test LLM, store keys).</p>
                    <ul>
                      <li>If <code>OPENCLAW_ADMIN_TOKEN</code> (or legacy <code>MOLTBOT_ADMIN_TOKEN</code>) is set on the server, you must enter the same token here.</li>
                      <li>If no server token is configured, admin actions are allowed on <b>localhost only</b> (convenience mode).</li>
                      <li>Never expose ComfyUI/OpenClaw to the public internet without proper access controls.</li>
                    </ul>
                    <p><b>PowerShell</b>: <code>$env:OPENCLAW_ADMIN_TOKEN="your-secret-token"</code></p>
                    <p><b>CMD</b>: <code>set OPENCLAW_ADMIN_TOKEN=your-secret-token</code></p>
                    `
                )
            );
            tokenInput = document.createElement("input");
            tokenInput.type = "password";
            tokenInput.className = "moltbot-input";
            tokenInput.placeholder = "Enter OPENCLAW_ADMIN_TOKEN if required (localhost-only if not configured)";
            tokenInput.value = "";
            tokenInput.autocomplete = "off";

            const tokenClearBtn = document.createElement("button");
            tokenClearBtn.className = "moltbot-btn moltbot-btn-secondary";
            tokenClearBtn.textContent = "Clear";
            tokenClearBtn.style.marginLeft = "4px";
            tokenClearBtn.onclick = () => {
                tokenInput.value = "";
                MoltbotSession.setAdminToken("");
            };

            tokenRow.appendChild(tokenInput);
            tokenRow.appendChild(tokenClearBtn);
            llmSec.appendChild(tokenRow);

            // Status message area
            const statusDiv = document.createElement("div");
            statusDiv.className = "moltbot-status";
            llmSec.appendChild(statusDiv);

            // Buttons row
            const btnRow = document.createElement("div");
            btnRow.className = "moltbot-btn-row";

            // Save button
            const saveBtn = document.createElement("button");
            saveBtn.className = "moltbot-btn";
            saveBtn.textContent = "Save";
            saveBtn.onclick = async () => {
                const token = (tokenInput.value || MoltbotSession.getAdminToken() || "").trim();
                if (token) MoltbotSession.setAdminToken(token);

                saveBtn.disabled = true;
                statusDiv.textContent = "Saving...";
                statusDiv.className = "moltbot-status";

                const updates = {
                    provider: providerSelect.value,
                    model: modelInput.value,
                    base_url: baseUrlInput.value,
                    timeout_sec: parseInt(timeoutInput.value) || 120,
                    max_retries: parseInt(retriesInput.value) || 3,
                };

                // R70: Client-side schema coercion (if schema available)
                if (schema && Object.keys(schema).length > 0) {
                    for (const [k, v] of Object.entries(updates)) {
                        const def = schema[k];
                        if (!def) continue;
                        if (def.type === "int" && typeof v !== "number") {
                            updates[k] = parseInt(v) || def.default;
                        }
                    }
                }

                const res = await moltbotApi.putConfig(updates, token);
                // R53: Hot-Reload Feedback
                if (res.ok) {
                    const apply = res.data?.apply || {};
                    let msg = "✓ Saved!";

                    if (apply.restart_required?.length > 0) {
                        msg += " Restart required for: " + apply.restart_required.join(", ");
                        statusDiv.className = "moltbot-status warning"; // Yellow/Orange
                    } else if (apply.applied_now?.length > 0) {
                        msg += " Applied immediately (Hot Reload).";
                        statusDiv.className = "moltbot-status ok";
                    } else {
                        // No changes or unknown
                        statusDiv.className = "moltbot-status ok";
                    }
                    statusDiv.textContent = msg;
                } else {
                    const errorMsg = getAdminErrorMessage(res.error, res.status);
                    statusDiv.textContent = `✗ ${res.errors?.join(", ") || errorMsg}`;
                    statusDiv.className = "moltbot-status error";
                }
                saveBtn.disabled = false;
            };
            btnRow.appendChild(saveBtn);

            // Test button
            const testBtn = document.createElement("button");
            testBtn.className = "moltbot-btn moltbot-btn-secondary";
            testBtn.textContent = "Test Connection";

            // R54: Debounced Test Action to prevent spam
            // We use a separate handler because we need to manage button state (disabled/enabled)
            // which debounce interferes with if not careful.
            // Better strategy: Disable button immediately on click, re-enable after completion.
            // Debounce is less critical here if we disable the button, but good for "auto-test on change" (future).
            // For now, implementing "Disable while testing" is the better guard than generic debounce for a button click.
            testBtn.onclick = async () => {
                if (testBtn.disabled) return;

                const token = (tokenInput.value || MoltbotSession.getAdminToken() || "").trim();
                if (token) MoltbotSession.setAdminToken(token);

                testBtn.disabled = true;
                statusDiv.textContent = "Testing...";
                statusDiv.className = "moltbot-status";

                // IMPORTANT (provider mismatch): "Test Connection" must test the provider/model currently
                // selected in the UI, even if the user hasn't clicked Save yet. Otherwise, the backend
                // falls back to the effective config (often "openai") and produces confusing errors like:
                // "API key not configured for provider 'openai'" while the UI is set to Gemini.
                try {
                    const res = await moltbotApi.testLLM(token, {
                        provider: providerSelect.value,
                        model: modelInput.value,
                        base_url: baseUrlInput.value,
                        timeout_sec: parseInt(timeoutInput.value) || 120,
                        max_retries: parseInt(retriesInput.value) || 3,
                    });
                    if (res.ok) {
                        statusDiv.textContent = "✓ Success! " + (res.response ? `"${res.response}"` : "");
                        statusDiv.className = "moltbot-status ok";
                    } else {
                        const errorMsg = getAdminErrorMessage(res.error, res.status);
                        statusDiv.textContent = `✗ ${errorMsg}`;
                        statusDiv.className = "moltbot-status error";
                    }
                } finally {
                    testBtn.disabled = false;
                }
            };
            btnRow.appendChild(testBtn);

            llmSec.appendChild(btnRow);

            // API Key instructions
            const keyNote = document.createElement("div");
            keyNote.className = "moltbot-note";
            keyNote.innerHTML = `<b>API Key</b>: Use <code>OPENCLAW_LLM_API_KEY</code> (or provider-specific keys) via environment variable (recommended), or enable the UI Key Store below (server-side storage; never stored in browser).`;
            llmSec.appendChild(keyNote);

        } else {
            const detail = [
                configRes.status ? `HTTP ${configRes.status}` : null,
                configRes.error || "Failed to load config",
            ].filter(Boolean).join(" — ");
            addRow(llmSec, "Error", detail);
        }
        scroll.appendChild(llmSec);

        // --- S26: Collapsible Secrets Section (always visible) ---
        if (configRes.ok) {
            const { config, sources, providers } = configRes.data;

            const secretsSec = createCollapsibleSection(
                "UI Key Store (Advanced)",
                `Server-side API key storage for portability. <b>Recommended:</b> Use ENV. <b>Acceptable:</b> Localhost-only single-user setups.`,
                false // Default collapsed
            );

            const secretsContent = secretsSec.content;

            const secretProviderRow = createFormRow("Store For");
            const secretProviderSelect = document.createElement("select");
            secretProviderSelect.className = "moltbot-input";
            // Build options from provider catalog + generic fallback
            const providerOptions = [];
            providers.forEach(p => providerOptions.push({ id: p.id, label: p.label, requires_key: p.requires_key }));
            providerOptions.push({ id: "generic", label: "Generic (fallback)", requires_key: true });
            providerOptions.forEach(p => {
                // Skip local providers (no key required) unless "generic"
                if (p.id !== "generic" && p.requires_key === false) return;
                const opt = document.createElement("option");
                opt.value = p.id;
                opt.textContent = p.label;
                secretProviderSelect.appendChild(opt);
            });
            secretProviderRow.appendChild(secretProviderSelect);
            secretsContent.appendChild(secretProviderRow);

            const secretKeyRow = createFormRow("API Key");
            const secretKeyWrap = document.createElement("div");
            secretKeyWrap.style.display = "flex";
            secretKeyWrap.style.gap = "8px";
            secretKeyWrap.style.alignItems = "center";

            const secretKeyInput = document.createElement("input");
            secretKeyInput.type = "password";
            secretKeyInput.className = "moltbot-input";
            secretKeyInput.placeholder = "Paste provider API key (not stored in browser)";
            secretKeyInput.value = "";
            secretKeyInput.autocomplete = "off";
            secretKeyInput.style.flex = "1";

            const secretKeyClearBtn = document.createElement("button");
            secretKeyClearBtn.className = "moltbot-btn moltbot-btn-secondary";
            secretKeyClearBtn.textContent = "Clear";
            secretKeyClearBtn.onclick = () => {
                secretKeyInput.value = "";
            };

            secretKeyWrap.appendChild(secretKeyInput);
            secretKeyWrap.appendChild(secretKeyClearBtn);
            secretKeyRow.appendChild(secretKeyWrap);
            secretsContent.appendChild(secretKeyRow);

            const secretsStatus = document.createElement("div");
            secretsStatus.className = "moltbot-status";
            secretsContent.appendChild(secretsStatus);

            const getAdminToken = () => {
                const tok = (container.querySelector('input[type="password"][placeholder*="OPENCLAW_ADMIN_TOKEN"]')?.value || MoltbotSession.getAdminToken() || "").trim();
                return tok;
            };

            const refreshSecretsStatus = async () => {
                const token = getAdminToken();

                secretsStatus.textContent = "Loading...";
                secretsStatus.className = "moltbot-status";
                const res = await moltbotApi.getSecretsStatus(token);
                if (res.ok) {
                    const secrets = res.data?.secrets || {};
                    const keys = Object.keys(secrets);
                    if (keys.length === 0) {
                        secretsStatus.textContent = "✓ No stored keys.";
                        secretsStatus.className = "moltbot-status ok";
                    } else {
                        secretsStatus.textContent = `✓ Stored keys: ${keys.join(", ")}`;
                        secretsStatus.className = "moltbot-status ok";
                    }
                } else {
                    const detail = [
                        res.status ? `HTTP ${res.status}` : null,
                        res.error || "Failed",
                    ].filter(Boolean).join(" — ");
                    secretsStatus.textContent = `✗ ${detail}`;
                    secretsStatus.className = "moltbot-status error";
                }
            };

            const secretsBtnRow = document.createElement("div");
            secretsBtnRow.className = "moltbot-btn-row";

            const secretsStatusBtn = document.createElement("button");
            secretsStatusBtn.className = "moltbot-btn moltbot-btn-secondary";
            secretsStatusBtn.textContent = "Check Status";
            secretsStatusBtn.onclick = async () => {
                secretsStatusBtn.disabled = true;
                await refreshSecretsStatus();
                secretsStatusBtn.disabled = false;
            };
            secretsBtnRow.appendChild(secretsStatusBtn);

            const secretsSaveBtn = document.createElement("button");
            secretsSaveBtn.className = "moltbot-btn";
            secretsSaveBtn.textContent = "Save Key";
            secretsSaveBtn.onclick = async () => {
                const token = getAdminToken();
                const apiKey = (secretKeyInput.value || "").trim();
                if (!apiKey) {
                    secretsStatus.textContent = "Please paste an API key first.";
                    secretsStatus.className = "moltbot-status error";
                    return;
                }
                if (token) MoltbotSession.setAdminToken(token);

                secretsSaveBtn.disabled = true;
                secretsStatus.textContent = "Saving...";
                secretsStatus.className = "moltbot-status";

                const res = await moltbotApi.saveSecret(secretProviderSelect.value, apiKey, token);
                if (res.ok) {
                    secretKeyInput.value = "";
                    secretsStatus.textContent = "✓ Saved to server store. Restart ComfyUI if needed.";
                    secretsStatus.className = "moltbot-status ok";
                    await refreshSecretsStatus();
                } else {
                    const detail = [
                        res.status ? `HTTP ${res.status}` : null,
                        res.error || "Failed",
                    ].filter(Boolean).join(" — ");
                    secretsStatus.textContent = `✗ ${detail}`;
                    secretsStatus.className = "moltbot-status error";
                }
                secretsSaveBtn.disabled = false;
            };
            secretsBtnRow.appendChild(secretsSaveBtn);

            const secretsClearBtn = document.createElement("button");
            secretsClearBtn.className = "moltbot-btn moltbot-btn-danger";
            secretsClearBtn.textContent = "Clear Stored Key";
            secretsClearBtn.onclick = async () => {
                const token = getAdminToken();
                if (token) MoltbotSession.setAdminToken(token);

                secretsClearBtn.disabled = true;
                secretsStatus.textContent = "Clearing...";
                secretsStatus.className = "moltbot-status";

                const res = await moltbotApi.clearSecret(secretProviderSelect.value, token);
                if (res.ok) {
                    secretsStatus.textContent = "✓ Cleared.";
                    secretsStatus.className = "moltbot-status ok";
                    await refreshSecretsStatus();
                } else {
                    const detail = [
                        res.status ? `HTTP ${res.status}` : null,
                        res.error || "Failed",
                    ].filter(Boolean).join(" — ");
                    secretsStatus.textContent = `✗ ${detail}`;
                    secretsStatus.className = "moltbot-status error";
                }
                secretsClearBtn.disabled = false;
            };
            secretsBtnRow.appendChild(secretsClearBtn);

            secretsContent.appendChild(secretsBtnRow);

            scroll.appendChild(secretsSec.container);
        }

        // -- Logs Section --
        const logsSec = createSection("Recent Logs");
        const logView = document.createElement("div");
        logView.className = "moltbot-log-viewer";

        if (logRes.ok) {
            const content = logRes.data?.content;
            logView.textContent = Array.isArray(content) ? content.join("\n") : String(content ?? "");
        } else {
            const detail = [
                logRes.status ? `HTTP ${logRes.status}` : null,
                logRes.error || "request_failed",
            ].filter(Boolean).join(" — ");
            logView.textContent = `Failed to load logs: ${detail}`;
        }

        logsSec.appendChild(logView);
        scroll.appendChild(logsSec);

        // F48: Deep Link Handling
        // Format: #settings/sectionId
        // We need to map known sections or just rely on text content matching if we didn't add IDs?
        // Let's rely on checking hash after render.
        setTimeout(() => {
            const hash = window.location.hash;
            if (hash && hash.startsWith("#settings/")) {
                const sectionKey = hash.split("/")[1];
                let target = null;

                // Simple mapping based on section titles we created
                // "LLM Settings" -> "llm"
                // "UI Key Store" -> "secrets"
                // "Recent Logs" -> "logs"
                // "System Health" -> "health"

                const sections = Array.from(scroll.querySelectorAll(".moltbot-section"));
                if (sectionKey === "llm") target = sections.find(s => s.textContent.includes("LLM Settings"));
                else if (sectionKey === "secrets") {
                    target = sections.find(s => s.textContent.includes("UI Key Store"));
                    // Auto-expand if targeted
                    if (target) {
                        const content = target.querySelector(".moltbot-collapsible-content");
                        const toggle = target.querySelector(".moltbot-collapsible-header span:last-child");
                        if (content) content.style.display = "block";
                        if (toggle) toggle.textContent = "▼";
                    }
                }
                else if (sectionKey === "logs") target = sections.find(s => s.textContent.includes("Recent Logs"));
                else if (sectionKey === "health") target = sections.find(s => s.textContent.includes("System Health"));

                if (target) {
                    target.scrollIntoView({ behavior: "smooth", block: "start" });
                    target.style.outline = "2px solid var(--primary-color, #2196F3)";
                    target.style.transition = "outline 1s";
                    setTimeout(() => target.style.outline = "none", 2000);
                }
            }
        }, 100);
    },
};

function createSection(title) {
    const div = document.createElement("div");
    div.className = "moltbot-section";
    const h4 = document.createElement("h4");
    h4.textContent = title;
    div.appendChild(h4);
    return div;
}

function createCollapsibleSection(title, description, defaultExpanded = false) {
    const container = document.createElement("div");
    container.className = "moltbot-section moltbot-collapsible-section";

    const header = document.createElement("div");
    header.className = "moltbot-collapsible-header";
    header.style.cursor = "pointer";
    header.style.display = "flex";
    header.style.justifyContent = "space-between";
    header.style.alignItems = "center";
    header.style.userSelect = "none";

    const titleWrap = document.createElement("div");
    titleWrap.style.display = "flex";
    titleWrap.style.alignItems = "center";
    titleWrap.style.gap = "8px";

    const h4 = document.createElement("h4");
    h4.style.margin = "0";
    h4.innerHTML = title;
    titleWrap.appendChild(h4);

    // Add help button inline
    const helpBtn = createHelpButton(
        "UI Key Store (Security & Usage)",
        `
        <p>This feature lets you paste an LLM provider API key in the UI and save it to the <b>server-side</b> secret store (<code>{STATE_DIR}/secrets.json</code>).</p>
        <p><b>Important</b>:</p>
        <ul>
          <li>Recommended: use environment variables for API keys.</li>
          <li>Only use UI storage on a single-user, localhost-only setup.</li>
          <li>ENV keys always take priority over stored keys.</li>
          <li>Secrets are stored as plaintext JSON on disk (protected by OS permissions).</li>
          <li>Outbound LLM requests are protected by an SSRF policy. Built-in providers are allowlisted by default; custom Base URL hosts must be added via <code>OPENCLAW_LLM_ALLOWED_HOSTS</code> (or use <code>OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST=1</code> at your own risk).</li>
        </ul>
        <p><b>PowerShell</b>: <code>$env:OPENCLAW_LLM_API_KEY="&lt;YOUR_API_KEY&gt;"</code></p>
        <p><b>CMD</b>: <code>set OPENCLAW_LLM_API_KEY=&lt;YOUR_API_KEY&gt;</code></p>
        `
    );
    titleWrap.appendChild(helpBtn);



    const toggle = document.createElement("span");
    toggle.textContent = defaultExpanded ? "▼" : "►";
    toggle.style.fontSize = "12px";
    toggle.style.transition = "transform 0.2s";

    header.appendChild(titleWrap);
    header.appendChild(toggle);
    container.appendChild(header);

    const descDiv = document.createElement("div");
    descDiv.className = "moltbot-note";
    descDiv.style.margin = "8px 0";
    descDiv.innerHTML = description;
    container.appendChild(descDiv);

    const content = document.createElement("div");
    content.className = "moltbot-collapsible-content";
    content.style.display = defaultExpanded ? "block" : "none";
    content.style.marginTop = "8px";
    container.appendChild(content);

    header.onclick = () => {
        const isExpanded = content.style.display !== "none";
        content.style.display = isExpanded ? "none" : "block";
        toggle.textContent = isExpanded ? "►" : "▼";
    };

    return { container, content };
}

function createFormRow(label, locked = false, helpBtn = null) {
    const row = document.createElement("div");
    row.className = "moltbot-form-row";
    const header = document.createElement("div");
    header.style.display = "flex";
    header.style.alignItems = "center";
    header.style.justifyContent = "space-between";
    header.style.gap = "8px";

    const lbl = document.createElement("label");
    lbl.className = "moltbot-label";
    lbl.textContent = label + (locked ? " 🔒" : "");
    if (locked) lbl.title = "Locked (env override)";

    header.appendChild(lbl);
    if (helpBtn) header.appendChild(helpBtn);
    row.appendChild(header);
    return row;
}

function createHelpButton(title, html) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "moltbot-help-btn";
    btn.textContent = "?";
    btn.title = "Help";
    btn.onclick = (e) => {
        e.stopPropagation(); // Prevent collapsible toggle
        showHelpModal(title, html);
    };
    return btn;
}

function showHelpModal(title, html) {
    // Remove any existing modal overlay
    const existing = document.querySelector(".moltbot-modal-overlay");
    if (existing) existing.remove();

    const overlay = document.createElement("div");
    overlay.className = "moltbot-modal-overlay";
    overlay.addEventListener("click", (e) => {
        if (e.target === overlay) overlay.remove();
    });

    const modal = document.createElement("div");
    modal.className = "moltbot-modal";

    const header = document.createElement("div");
    header.className = "moltbot-modal-header";
    header.textContent = title;

    const closeBtn = document.createElement("button");
    closeBtn.className = "moltbot-btn moltbot-btn-secondary";
    closeBtn.textContent = "Close";
    closeBtn.onclick = () => overlay.remove();
    header.appendChild(closeBtn);

    const body = document.createElement("div");
    body.className = "moltbot-modal-body";
    body.innerHTML = html;

    modal.appendChild(header);
    modal.appendChild(body);
    overlay.appendChild(modal);
    document.body.appendChild(overlay);
}

function addRow(container, key, val, valClass = "") {
    const row = document.createElement("div");
    row.className = "moltbot-kv-row";

    const k = document.createElement("span");
    k.className = "moltbot-kv-key";
    k.textContent = key;

    const v = document.createElement("span");
    v.className = `moltbot-kv-val ${valClass}`;
    v.textContent = val;

    row.appendChild(k);
    row.appendChild(v);
    container.appendChild(row);
}

async function detectComfyUiVersion(api) {
    const candidates = [
        () => window?.COMFYUI_VERSION,
        () => window?.comfyui_version,
        () => window?.ComfyUI?.version,
        () => window?.app?.version,
        () => window?.app?.ui?.settings?.getSettingValue?.("ComfyUI.Version", null),
        () => window?.app?.ui?.settings?.getSettingValue?.("comfyui.version", null),
    ];

    for (const get of candidates) {
        try {
            const v = normalizeVersion(get?.());
            if (v) return v;
        } catch { }
    }

    const endpoints = ["/system_stats", "/system_info", "/version"];
    for (const path of endpoints) {
        try {
            const res = await api.fetch(path, { timeout: 1500 });
            if (!res.ok) continue;
            const v = extractComfyVersion(res.data);
            if (v) return v;
        } catch { }
    }

    return null;
}

function extractComfyVersion(data) {
    if (!data) return null;
    if (typeof data === "string") return normalizeVersion(data);
    if (typeof data !== "object") return null;

    const direct = normalizeVersion(data.comfyui_version || data.comfyuiVersion);
    if (direct) return direct;

    const nested = normalizeVersion(data.comfyui?.version || data.comfyui?.comfyui_version);
    if (nested) return nested;

    const system = normalizeVersion(data.system?.comfyui_version || data.system?.version);
    if (system) return system;

    const name = String(data.name || data.app || "").toLowerCase();
    const namedVersion = normalizeVersion(data.version);
    if (namedVersion && name.includes("comfy")) return namedVersion;

    return null;
}

function normalizeVersion(value) {
    if (value === null || value === undefined) return null;
    const str = String(value).trim();
    return str ? str : null;
}
