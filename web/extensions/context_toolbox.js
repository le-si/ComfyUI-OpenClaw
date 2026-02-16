import { app } from "../../scripts/app.js";
import { tabManager } from "../openclaw_tabs.js";
import { moltbotUI } from "../openclaw_ui.js";
import { moltbotApi } from "../openclaw_api.js";

/**
 * F51: In-Canvas Context Toolbox
 * Adds quick actions to the node context menu.
 */
export function registerContextToolbox() {
    app.registerExtension({
        name: "OpenClaw.ContextToolbox",
        async setup() {
            const originalGetNodeMenuOptions = LGraphCanvas.prototype.getNodeMenuOptions;

            LGraphCanvas.prototype.getNodeMenuOptions = function (node) {
                const options = originalGetNodeMenuOptions.apply(this, arguments);
                if (!options) return options;

                // F51: Add OpenClaw Actions
                options.push(null); // Separator

                // 1. Inspect in Explorer
                options.push({
                    content: "\uD83D\uDD0D OpenClaw: Inspect Node", // Magnifying glass
                    callback: () => {
                        // Switch to Explorer tab
                        const explorerTab = tabManager.tabs["explorer"];
                        if (explorerTab) {
                            tabManager.activateTab("explorer");
                            // If Explorer has a filter/search API, use it
                            if (explorerTab.instance && typeof explorerTab.instance.search === "function") {
                                explorerTab.instance.search(node.type);
                            } else {
                                // Fallback: try to set input value if exposed
                                const input = document.querySelector(".moltbot-explorer-search");
                                if (input) {
                                    input.value = node.type;
                                    input.dispatchEvent(new Event("input"));
                                }
                            }
                        } else {
                            moltbotUI.showBanner("warning", "Explorer tab not available.");
                        }
                    }
                });

                // 2. View Stats (Placeholder for now, maybe deep link to metrics)
                // options.push({ content: "OpenClaw: View Stats", ... });

                // 3. Jump to Settings (if node has settings, generic for now)
                options.push({
                    content: "\u2699\uFE0F OpenClaw: Settings", // Gear
                    callback: () => {
                        tabManager.activateTab("settings");
                    }
                });

                // 4. Missing Node Guidance (if node is red/missing)
                if (node.type === "undefined" || node.type === undefined || node.has_errors) {
                    options.push({
                        content: "\uD83E\uDE79 OpenClaw: Find Replacements", // Bandage
                        callback: async () => {
                            // Deep link to packs/manager or show replacements
                            moltbotUI.showBanner("info", "Searching for replacements... (Simulated)");
                        }
                    });
                }

                return options;
            };
        }
    });
}
