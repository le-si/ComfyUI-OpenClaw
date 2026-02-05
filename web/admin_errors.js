/**
 * S26+: Admin Boundary Error Messaging
 * Maps backend error codes to user-friendly messages with actionable hints.
 */

/**
 * Get user-friendly error message for admin boundary failures
 * @param {string} error - Error code from backend
 * @param {number} status - HTTP status code
 * @returns {string} User-friendly error message
 */
export function getAdminErrorMessage(error, status) {
    // Convenience mode: localhost-only
    if (error === "csrf_protection") {
        return "Cross-origin request blocked (localhost convenience mode). " +
            "Access from the same browser tab where ComfyUI is running, or configure OPENCLAW_ADMIN_TOKEN for token-based auth.";
    }

    // Remote admin denied
    if (error === "remote_admin_denied" || error?.includes("Remote admin access denied")) {
        return "Remote admin access denied. " +
            "Set OPENCLAW_ALLOW_REMOTE_ADMIN=1 on the server to allow remote access, then provide a valid Admin Token.";
    }

    // Token configured but missing/invalid
    if (status === 403 && (error === "Unauthorized" || error === "unauthorized" || error === "admin_token_mismatch")) {
        return "Server requires Admin Token (configured). " +
            "Enter valid OPENCLAW_ADMIN_TOKEN above.";
    }

    // Generic auth failure
    if (status === 403 || status === 401) {
        return "Authorization required. Enter Admin Token if server has OPENCLAW_ADMIN_TOKEN configured.";
    }

    // Rate limit
    if (status === 429 || error === "rate_limit_exceeded") {
        return "Rate limit exceeded. Wait a moment and try again.";
    }

    // Default: pass through
    return error || "Request failed";
}
