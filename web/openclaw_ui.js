/**
 * F7: Moltbot UI Shell
 * Manages the main sidebar layout: Header, Tabs, Content.
 */
import { tabManager } from "./openclaw_tabs.js";
import { ErrorBoundary } from "./ErrorBoundary.js";
import { moltbotApi } from "./openclaw_api.js";

export class MoltbotUI {
    constructor() {
        this.container = null;
        this.boundary = new ErrorBoundary("MoltbotUI");
        this.floating = {
            panel: null,
            content: null,
        };
    }

    /**
     * Mount the UI into a provided container (sidebar render target).
     */
    mount(container) {
        this.container = container;
        this.boundary.run(container, () => this._render(container));
    }

    /**
     * Legacy fallback: toggle a floating panel (must not touch document.body directly).
     */
    toggleFloatingPanel() {
        if (!this.floating.panel) {
            const panel = document.createElement("div");
            panel.className = "moltbot-floating-panel";

            const close = document.createElement("button");
            close.className = "moltbot-floating-close";
            close.textContent = "×";
            close.title = "Close";
            close.addEventListener("click", () => {
                panel.classList.remove("visible");
            });

            const content = document.createElement("div");
            content.className = "moltbot-floating-content";

            panel.appendChild(close);
            panel.appendChild(content);
            document.body.appendChild(panel);

            this.floating.panel = panel;
            this.floating.content = content;
        }

        const panel = this.floating.panel;
        const content = this.floating.content;
        const isVisible = panel.classList.contains("visible");

        if (isVisible) {
            panel.classList.remove("visible");
            return;
        }

        panel.classList.add("visible");
        this.mount(content);
    }

    _render(container) {
        container.innerHTML = "";
        container.className = "moltbot-sidebar-container";

        // 1. Header
        const header = document.createElement("div");
        header.className = "moltbot-header";

        const statusDot = document.createElement("div");
        statusDot.className = "moltbot-status-dot ok";
        statusDot.title = "System Status";
        this.statusDot = statusDot;

        const title = document.createElement("div");
        title.className = "moltbot-title";
        title.textContent = "OpenClaw";

        // F9: About badges (version fetched from /openclaw/health; legacy /moltbot/health)
        const badges = document.createElement("div");
        badges.className = "moltbot-badges";
        const versionSpan = document.createElement("span");
        versionSpan.className = "moltbot-version";
        versionSpan.textContent = "v...";
        const repoLink = document.createElement("a");
        repoLink.href = "https://github.com/rookiestar28/ComfyUI-OpenClaw";
        repoLink.target = "_blank";
        repoLink.className = "moltbot-repo-link";
        repoLink.title = "View on GitHub";
        repoLink.textContent = "View on GitHub";
        badges.appendChild(versionSpan);
        badges.appendChild(repoLink);

        // Fetch version from health endpoint
        moltbotApi.getHealth().then(res => {
            if (res.ok && res.data) {
                const data = res.data;
                if (data.pack) {
                    versionSpan.textContent = `v${data.pack.version}`;
                }
                // S15: Check exposure
                this.checkExposure(data?.access_policy);

                // R87: Check Backpressure
                const obs = data.stats?.observability;
                if (obs && obs.total_dropped > 0) {
                    const dropCount = obs.total_dropped;
                    // Only warn if significant relative to capacity or distinct threshold
                    this.showBanner("warning", `\u26A0\uFE0F High load: ${dropCount} observability events dropped (Queue full). logs/traces might be incomplete.`);
                }
            } else {
                versionSpan.textContent = "v?.?.?";
            }
        }).catch(() => {
            versionSpan.textContent = "v?.?.?";
        });

        header.appendChild(statusDot);
        header.appendChild(title);
        header.appendChild(badges);
        container.appendChild(header);

        // 2. Tab Bar
        const tabBar = document.createElement("div");
        tabBar.className = "moltbot-tabs";
        this.tabBar = tabBar;
        container.appendChild(tabBar);

        // 3. Content Area
        const contentArea = document.createElement("div");
        contentArea.className = "moltbot-content";
        this.contentArea = contentArea;
        container.appendChild(contentArea);

        // Initialize Tabs
        tabManager.init(tabBar, contentArea);
    }

    /**
     * S15: Check if OpenClaw is exposed remotely and warn user.
     */
    checkExposure(policy) {
        if (!policy) return;

        const isLocal = ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);

        // Warn if not local
        if (!isLocal) {
            const isProtected = policy.observability === "token" && policy.token_configured;

            if (!isProtected) {
                // High risk: Remote + No Token
                this.showBanner("warning", "\u26A0\uFE0F Remote access detected; logs/config are protected unless you explicitly enable token-based access.");
            } else {
                // Medium risk: Remote + Token (Just info)
                // Optionally show nothing, or a small "Remote Access Secured" badge
                // console.log("OpenClaw remote access secured by token.");
            }
        }
    }

    showBanner(type, message) {
        // Create banner if not exists, or replace content
        let banner = this.container.querySelector(".moltbot-banner");
        if (!banner) {
            banner = document.createElement("div");
            banner.className = `moltbot-banner moltbot-banner-${type}`;
            // Insert after header
            const header = this.container.querySelector(".moltbot-header");
            header.after(banner);
        }
        banner.textContent = message;
    }
}

/**
 * F48/F49: Queue Lifecycle Monitor.
 * Consumes R71 events (SSE) with polling fallback to show deduplicated status banners.
 * Handles disconnected state and recovery based on B-Strict/B-Loose contracts.
 */
class QueueMonitor {
    constructor(ui) {
        this.ui = ui;
        this.lastBannerTime = 0;
        this.lastStatusId = null;
        this.bannerTTL = 5000; // 5s debounce for transient statuses
        this.es = null;
        this.isConnected = true; // Assume connected initially
    }

    start() {
        // 1. Start SSE subscription
        this.connectSSE();

        // 2. Poll health periodically (backup & backpressure check)
        setInterval(() => this.checkHealth(), 10000);
    }

    connectSSE() {
        if (this.es) {
            this.es.close();
        }

        this.es = moltbotApi.subscribeEvents(
            (data) => this.handleEvent(data),
            (err) => this.handleConnectionError(err)
        );
    }

    handleEvent(data) {
        // Recovered connection if we get an event
        if (!this.isConnected) {
            this.isConnected = true;
            this.showBanner("success", "\u2705 OpenClaw Backend Connected", "connection_restored", 3000);
        }

        const type = data.event_type;
        const pid = data.prompt_id ? data.prompt_id.slice(0, 8) : "???";

        switch (type) {
            case "queued":
                this.showBanner("info", `\u23F3 Job ${pid} queued`, `job_${type}`, 2000);
                break;
            case "running":
                this.showBanner("info", `\u25B6 Job ${pid} running...`, `job_${type}`, 5000);
                break;
            case "failed":
                this.showBanner("error", `\u274C Job ${pid} failed`, `job_${type}`, 10000);
                break;
            case "completed":
                // Optional: distinct success banner or silent
                // this.showBanner("success", `\u2705 Job ${pid} completed`, `job_${type}`, 3000);
                break;
        }
    }

    handleConnectionError(err) {
        // EventSource will retry automatically, but we flag UI state
        // Only show disconnected if it persists (debounce?)
        // For now, strict feedback:
        if (this.isConnected) {
            this.isConnected = false;
            this.showBanner("error", "\u26A0\uFE0F Backend Disconnected. Retrying...", "connection_lost");
        }
    }

    async checkHealth() {
        try {
            const res = await moltbotApi.getHealth();
            if (res.ok && res.data) {
                if (!this.isConnected) {
                    this.isConnected = true;
                    this.showBanner("success", "\u2705 Connection Restored", "connection_restored", 3000);
                    // Reconnect SSE if it was closed or dead
                    if (!this.es || this.es.readyState === 2) {
                        this.connectSSE();
                    }
                }

                const stats = res.data.stats || {};
                const obs = stats.observability || {};

                // R87: Backpressure
                if (obs.total_dropped > 0) {
                    this.showBanner(
                        "warning",
                        `\u26A0\uFE0F High load: ${obs.total_dropped} events dropped.`,
                        "backpressure"
                    );
                }
            } else {
                if (this.isConnected) {
                    this.isConnected = false;
                    this.showBanner("error", "\u26A0\uFE0F Backend Unreachable", "health_check_failed");
                }
            }
        } catch (e) {
            if (this.isConnected) {
                this.isConnected = false;
                this.showBanner("error", "\u26A0\uFE0F Connection Error", "health_check_exception");
            }
        }
    }

    showBanner(type, message, statusId, ttl = this.bannerTTL) {
        const now = Date.now();
        // Dedupe: Don't show same status banner if recently shown and within TTL
        // Exception: Errors usually override Info
        if (this.lastStatusId === statusId && (now - this.lastBannerTime < ttl)) {
            return;
        }

        // Priority Check (Simple): Don't overwrite Error with Info
        // (A real priority queue would be better, but this is F48 baseline)
        const currentIsError = this.lastStatusId && (this.lastStatusId.includes("error") || this.lastStatusId.includes("failed") || this.lastStatusId.includes("lost"));
        if (currentIsError && type === "info" && (now - this.lastBannerTime < 3000)) {
            return; // Hold error for at least 3s before info overwrites
        }

        this.ui.showBanner(type, message);
        this.lastStatusId = statusId;
        this.lastBannerTime = now;
    }
}

export const moltbotUI = new MoltbotUI();
const monitor = new QueueMonitor(moltbotUI);
monitor.start();
