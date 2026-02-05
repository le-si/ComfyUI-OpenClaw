/**
 * ComfyUI-OpenClaw Entry Point
 * Registers the extension and mounts the UI.
 */
import { app } from "../../../scripts/app.js";
import { moltbotUI } from "./openclaw_ui.js";
import { installGlobalErrorHandlers } from "./global_error_handler.js";
import { moltbotApi } from "./openclaw_api.js";

// Tabs
import { tabManager } from "./openclaw_tabs.js";
import { settingsTab } from "./tabs/settings_tab.js";
import { jobMonitorTab } from "./tabs/job_monitor_tab.js";
import { PlannerTab } from "./tabs/planner_tab.js";
import { VariantsTab } from "./tabs/variants_tab.js";
import { RefinerTab } from "./tabs/refiner_tab.js";
import { LibraryTab } from "./tabs/library_tab.js";
import { ApprovalsTab } from "./tabs/approvals_tab.js";
import { ExplorerTab } from "./tabs/explorer_tab.js";

function isExtensionEnabled() {
    try {
        return app?.ui?.settings?.getSettingValue?.("Moltbot.General.Enable", true) !== false;
    } catch (e) {
        return true;
    }
}

function isErrorBoundariesEnabled() {
    try {
        return app?.ui?.settings?.getSettingValue?.("Moltbot.General.ErrorBoundaries", true) !== false;
    } catch (e) {
        return true;
    }
}

function ensureCssInjected(id, href) {
    if (document.getElementById(id)) return;
    const link = document.createElement("link");
    link.id = id;
    link.rel = "stylesheet";
    link.type = "text/css";
    link.href = href;
    document.head.appendChild(link);
}

function ensureOpenClawCssInjected() {
    ensureCssInjected(
        "openclaw-styles",
        new URL("./openclaw.css", import.meta.url).href
    );
}

function ensureErrorBoundaryCssInjected() {
    ensureCssInjected(
        "openclaw-error-boundary-styles",
        new URL("./error_boundary.css", import.meta.url).href
    );
}

function installLegacyMenuButton() {
    const menuStrip = document.querySelector(".comfy-menu");

    const btn = document.createElement("button");
    btn.textContent = "🤖 OpenClaw";
    btn.style.cssText = `
        background: transparent;
        color: var(--fg-color, #ccc);
        border: none;
        cursor: pointer;
        font-size: 14px;
        padding: 5px 10px;
        margin-top: 10px;
        border-top: 1px solid var(--border-color, #444);
        width: 100%;
        text-align: left;
    `;

    btn.addEventListener("click", () => moltbotUI.toggleFloatingPanel());

    if (menuStrip) {
        menuStrip.appendChild(btn);
    } else {
        document.body.appendChild(btn);
    }
}

async function registerSupportedTabs() {
    // 1. Always register Settings & Job Monitor (Core)
    tabManager.registerTab(settingsTab);
    tabManager.registerTab(jobMonitorTab);

    // 2. Fetch Capabilities
    let features = {};
    let capabilitiesKnown = false;
    try {
        const res = await moltbotApi.getCapabilities();
        if (res.ok && res.data && res.data.features) {
            features = res.data.features;
            capabilitiesKnown = true;
        } else {
            console.warn("[OpenClaw] Failed to fetch capabilities, using defaults.");
        }
    } catch (e) {
        console.error("[OpenClaw] Error fetching capabilities:", e);
    }

    // 3. Conditionally Register (with a safe fallback)
    //
    // If capabilities are unavailable (404/pack mismatch/route registration failure),
    // we intentionally show the full tab set so users can see actionable errors
    // instead of “missing tabs”.
    const fallbackShowAll = !capabilitiesKnown;

    if (fallbackShowAll || features.assist_planner) tabManager.registerTab(PlannerTab);
    if (fallbackShowAll || features.assist_refiner) tabManager.registerTab(RefinerTab);
    if (fallbackShowAll || features.scheduler) tabManager.registerTab(VariantsTab);
    if (fallbackShowAll || features.presets) tabManager.registerTab(LibraryTab);
    if (fallbackShowAll || features.approvals) tabManager.registerTab(ApprovalsTab);
    if (fallbackShowAll || features.explorer || features.preflight || features.checkpoints) {
        tabManager.registerTab(ExplorerTab); // Explorer: inventory + preflight + snapshots
    }

    console.log("[OpenClaw] Tabs registered based on capabilities:", Object.keys(tabManager.tabs).length);
}

app.registerExtension({
    name: "ComfyUI-OpenClaw",

    async setup() {
        console.log("[OpenClaw] Extension loading...");

        // Register Settings (F7)
        if (app?.ui?.settings?.addSetting) {
            app.ui.settings.addSetting({
                id: "Moltbot.General.Enable",
                name: "Enable OpenClaw (requires restart)",
                type: "boolean",
                defaultValue: true,
            });
            app.ui.settings.addSetting({
                id: "Moltbot.General.ErrorBoundaries",
                name: "Enable Error Boundaries (requires restart)",
                type: "boolean",
                defaultValue: true,
            });
            app.ui.settings.addSetting({
                id: "Moltbot.Info",
                name: "ℹ️ Configure OpenClaw in the sidebar (left panel)",
                type: "text",
                defaultValue: "",
                attrs: { readonly: true, disabled: true },
            });
        }

        if (!isExtensionEnabled()) {
            console.log("[OpenClaw] Extension disabled via settings");
            return;
        }

        // Always inject base UI styles
        ensureOpenClawCssInjected();

        // Optional hardening (R6)
        if (isErrorBoundariesEnabled()) {
            ensureErrorBoundaryCssInjected();
            installGlobalErrorHandlers();
        }

        // Register Tabs Dynamics
        await registerSupportedTabs();

        // Preferred: modern sidebar API
        if (app?.extensionManager?.registerSidebarTab) {
            app.extensionManager.registerSidebarTab({
                id: "comfyui-openclaw",
                icon: "pi pi-bolt",
                title: "OpenClaw",
                tooltip: "OpenClaw: AI assistant for ComfyUI",
                type: "custom",
                render: (container) => {
                    moltbotUI.mount(container);
                },
            });
        } else {
            // Legacy fallback: left menu button + floating panel
            console.log("[OpenClaw] Sidebar API not found, using legacy menu button");
            installLegacyMenuButton();
        }

        console.log("[OpenClaw] Extension loaded.");
    },
});
