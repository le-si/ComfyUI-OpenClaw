import { app } from "../../scripts/app.js";

/**
 * F51: In-Canvas Context Toolbox
 * Adds quick actions to the node context menu.
 */
export function registerContextToolbox() {
    app.registerExtension({
        name: "OpenClaw.ContextToolbox",
        async setup() {
            // Wait for MoltbotActions to be available (defer slightly if needed, or import directly)
            // Since we import moltbotActions, it should be ready.
            const { moltbotActions } = await import("../openclaw_ui.js");

            const originalGetNodeMenuOptions = LGraphCanvas.prototype.getNodeMenuOptions;

            LGraphCanvas.prototype.getNodeMenuOptions = function (node) {
                const options = originalGetNodeMenuOptions.apply(this, arguments);
                if (!options) return options;

                // F51: Add OpenClaw Actions
                options.push(null); // Separator

                // 1. Inspect in Explorer
                options.push({
                    content: "\uD83D\uDD0D OpenClaw: Inspect",
                    callback: () => {
                        moltbotActions.openExplorer(node.type);
                    }
                });

                // 2. Doctor / Stats
                options.push({
                    content: "\uD83D\uDC89 OpenClaw: Doctor",
                    callback: () => {
                        moltbotActions.openDoctor();
                    }
                });

                // 3. Queue / Status
                options.push({
                    content: "\u23F3 OpenClaw: Queue Status",
                    callback: () => {
                        moltbotActions.openQueue("all");
                    }
                });

                // F50: OpenClaw Compare
                // Only show if node has inputs/widgets that can be compared
                if (node.widgets && node.widgets.length > 0) {
                    options.push({
                        content: "\u2696\uFE0F OpenClaw: Compare...",
                        callback: () => {
                            moltbotActions.openCompare(node);
                        }
                    });
                }

                // 4. Settings
                options.push({
                    content: "\u2699\uFE0F OpenClaw: Settings",
                    callback: () => {
                        moltbotActions.openSettings();
                    }
                });

                // 5. History (if applicable)
                // options.push({ ... });

                return options;
            };
        }
    });
}
