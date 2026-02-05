
/**
 * Shared Utilities for Moltbot UI
 */

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
