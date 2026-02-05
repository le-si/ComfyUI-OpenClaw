/**
 * R6: ErrorBoundary
 * Wraps UI components to catch render errors and display a fallback UI.
 * modeled after ComfyUI-Doctor.
 */

export class ErrorBoundary {
    constructor(componentName = "Unknown Component") {
        this.componentName = componentName;
    }

    /**
     * Wrap a render function with error handling.
     * @param {HTMLElement} container - The container to render into.
     * @param {Function} renderFn - The logic to execute.
     */
    run(container, renderFn) {
        try {
            renderFn();
        } catch (error) {
            console.error(`[OpenClaw] Error rendering ${this.componentName}:`, error);
            this.showFallback(container, error);
        }
    }

    showFallback(container, error) {
        // Safe clear
        while (container.firstChild) {
            container.removeChild(container.firstChild);
        }

        const box = document.createElement("div");
        box.className = "moltbot-error-boundary";

        const h3 = document.createElement("h3");
        h3.textContent = `Error in ${this.componentName}`;

        const msg = document.createElement("div");
        msg.textContent = "Something went wrong. Please check the console.";

        const code = document.createElement("code");
        code.textContent = error.toString();

        box.appendChild(h3);
        box.appendChild(msg);
        box.appendChild(code);

        container.appendChild(box);
    }
}
