
/**
 * Simple debounce utility (R54 UI Guard).
 * Prevents rapid-fire API calls (e.g. test connection spam).
 */
export function debounce(func, wait) {
    let timeout;
    return function (...args) {
        const context = this;
        clearTimeout(timeout);
        timeout = setTimeout(() => func.apply(context, args), wait);
    };
}
