/**
 * R6: Global Error Handler
 * Installs window-level error handlers to catch unhandled exceptions if enabled.
 */

let installed = false;

export function installGlobalErrorHandlers() {
    if (installed) return;

    window.addEventListener("error", (event) => {
        // Filter for openclaw-related moltbot-related errors if possible, or just log generic
        // For MVP, we simply log to console with a specific prefix for easier debugging
        console.error("[OpenClaw Global Error]", event.error);
    });

    window.addEventListener("unhandledrejection", (event) => {
        console.error("[OpenClaw Unhandled Rejection]", event.reason);
    });

    installed = true;
    console.log("[OpenClaw] Global error handlers installed.");
}
