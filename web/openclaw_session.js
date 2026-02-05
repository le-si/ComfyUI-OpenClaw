/**
 * Moltbot Session Store (R12)
 * Manages transient session state like the Admin Token.
 *
 * Rules:
 * - Admin Token is stored in sessionStorage (cleared on tab close).
 * - NOT persisted to localStorage (security).
 */
export const MoltbotSession = {
    // Keys
    KEYS: {
        ADMIN_TOKEN: "openclaw_admin_token",
        LEGACY_ADMIN_TOKEN: "moltbot_admin_token",
    },

    /**
     * Set the admin token for this session.
     * @param {string} token
     */
    setAdminToken(token) {
        if (!token) {
            sessionStorage.removeItem(this.KEYS.ADMIN_TOKEN);
            sessionStorage.removeItem(this.KEYS.LEGACY_ADMIN_TOKEN);
        } else {
            sessionStorage.setItem(this.KEYS.ADMIN_TOKEN, token);
            // Keep legacy key in sync so downgrades / older builds still work.
            sessionStorage.setItem(this.KEYS.LEGACY_ADMIN_TOKEN, token);
        }
    },

    /**
     * Get the current admin token.
     * @returns {string|null}
     */
    getAdminToken() {
        return (
            sessionStorage.getItem(this.KEYS.ADMIN_TOKEN) ||
            sessionStorage.getItem(this.KEYS.LEGACY_ADMIN_TOKEN)
        );
    },

    /**
     * Check if admin token is present.
     * @returns {boolean}
     */
    hasAdminToken() {
        return !!this.getAdminToken();
    }
};
