export function normalizeLibraryCategory(category) {
    if (!category || category === "all") return null;
    return category;
}

export function filterLibraryItems(items = [], term = "") {
    const normalizedTerm = String(term || "").trim().toLowerCase();
    if (!normalizedTerm) {
        return Array.isArray(items) ? [...items] : [];
    }
    return (Array.isArray(items) ? items : []).filter((item) =>
        String(item?.name || "").toLowerCase().includes(normalizedTerm)
    );
}

export function getLibraryApplyTarget(preset = {}, explicitTarget = null) {
    if (explicitTarget) return explicitTarget;
    if (preset?.category === "prompt") return "planner";
    if (preset?.category === "params") return "variants";
    return null;
}
