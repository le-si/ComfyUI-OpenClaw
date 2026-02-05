
/**
 * Shared Utilities for Moltbot UI
 */

/**
 * Simple DOM factory helper.
 * @param {string} tag - HTML tag name
 * @param {string} className - Optional class name
 * @param {string} text - Optional text content
 */
export function makeEl(tag, className = "", text = "") {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined && text !== null && text !== "") {
        el.textContent = text;
    }
    return el;
}

/**
 * Lightweight toast helper for UI feedback.
 * @param {string} message
 * @param {"info"|"error"|"success"} variant
 */
export function showToast(message, variant = "info") {
    const toast = document.createElement("div");
    toast.className = `moltbot-toast moltbot-toast-${variant}`;
    toast.textContent = message;
    toast.style.position = "fixed";
    toast.style.right = "16px";
    toast.style.bottom = "16px";
    toast.style.padding = "8px 12px";
    toast.style.borderRadius = "6px";
    toast.style.background = variant === "error" ? "#5a1e1e" : (variant === "success" ? "#1e5a2b" : "#2d2d2d");
    toast.style.color = "#fff";
    toast.style.zIndex = "9999";
    toast.style.boxShadow = "0 4px 12px rgba(0,0,0,0.3)";
    document.body.appendChild(toast);

    setTimeout(() => {
        toast.remove();
    }, 2500);
}

/**
 * Display an error message within a container.
 * Looks for an existing .moltbot-error-box, or creates one at the top.
 */
export function showError(container, message) {
    let errorBox = container.querySelector(".moltbot-error-box");

    if (!errorBox) {
        // Try to find one by ID pattern if specific class missing? No, stick to class.
        // If not found, inject at top of panel
        const panel = container.querySelector(".moltbot-panel") || container;
        errorBox = document.createElement("div");
        errorBox.className = "moltbot-error-box";
        // Insert after panel header or at top
        const header = panel.querySelector(".moltbot-section-header");
        if (header && header.nextSibling) {
            panel.insertBefore(errorBox, header.nextSibling);
        } else {
            panel.prepend(errorBox);
        }
    }

    errorBox.textContent = message;
    errorBox.style.display = "block";

    // Auto-scroll to error
    errorBox.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/**
 * Clear error message in container.
 */
export function clearError(container) {
    const errorBox = container.querySelector(".moltbot-error-box");
    if (errorBox) {
        errorBox.style.display = "none";
        errorBox.textContent = "";
    }
}

/**
 * Copy text to clipboard and show ephemeral tooltip/toast
 */
export async function copyToClipboard(text, btnElement) {
    try {
        await navigator.clipboard.writeText(text);

        // Show feedback on button
        const origText = btnElement.textContent;
        btnElement.textContent = "Copied!";
        btnElement.classList.add("moltbot-btn-success");

        setTimeout(() => {
            btnElement.textContent = origText;
            btnElement.classList.remove("moltbot-btn-success");
        }, 1500);

    } catch (err) {
        console.error("Failed to copy:", err);
        alert("Failed to copy to clipboard");
    }
}
