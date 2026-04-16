function pickAssetHash(imageRef = {}) {
    if (!imageRef || typeof imageRef !== "object") {
        return "";
    }
    const direct = typeof imageRef.asset_hash === "string" ? imageRef.asset_hash.trim() : "";
    if (direct) {
        return direct;
    }
    const nested = imageRef.asset;
    if (nested && typeof nested === "object" && typeof nested.asset_hash === "string") {
        return nested.asset_hash.trim();
    }
    return "";
}

function pickAssetApiId(imageRef = {}) {
    if (!imageRef || typeof imageRef !== "object") {
        return "";
    }
    const direct = typeof imageRef.asset_api_id === "string"
        ? imageRef.asset_api_id.trim()
        : (typeof imageRef.asset_id === "string" ? imageRef.asset_id.trim() : "");
    if (direct) {
        return direct;
    }
    const nested = imageRef.asset;
    if (!nested || typeof nested !== "object") {
        return "";
    }
    if (typeof nested.asset_id === "string" && nested.asset_id.trim()) {
        return nested.asset_id.trim();
    }
    if (typeof nested.id === "string" && nested.id.trim()) {
        return nested.id.trim();
    }
    return "";
}

function pickFilename(imageRef = {}) {
    if (!imageRef || typeof imageRef !== "object") {
        return "";
    }
    if (typeof imageRef.filename === "string" && imageRef.filename.trim()) {
        return imageRef.filename.trim();
    }
    if (typeof imageRef.name === "string" && imageRef.name.trim()) {
        return imageRef.name.trim();
    }
    return "";
}

export function normalizeComfyOutputRef(imageRef = {}) {
    const assetHash = pickAssetHash(imageRef);
    const assetApiId = pickAssetApiId(imageRef);
    const namedFilename = pickFilename(imageRef);
    const filename = namedFilename || assetHash || assetApiId;
    const subfolder = typeof imageRef.subfolder === "string" ? imageRef.subfolder : "";
    const type = typeof imageRef.type === "string" && imageRef.type ? imageRef.type : "output";

    if (!filename) {
        return null;
    }

    const explicitAssetApiRequired = imageRef.asset_api_required === true;
    const assetApiRequired = Boolean(explicitAssetApiRequired || (assetApiId && !assetHash && !namedFilename));

    // IMPORTANT: asset-backed refs still resolve through /view when possible; do
    // not promote asset-api-only identifiers into implicit /api/assets fetches.
    const viewParams = assetApiRequired
        ? null
        : (
            assetHash
                ? { filename: assetHash }
                : {
                    filename,
                    type,
                    ...(subfolder ? { subfolder } : {}),
                }
        );

    return {
        filename,
        subfolder,
        type,
        asset_hash: assetHash || "",
        asset_api_id: assetApiId || "",
        asset_api_required: assetApiRequired,
        resolution: assetApiRequired ? "asset_api_required" : "view",
        unsupported_reason: assetApiRequired ? "asset_api_required" : "",
        is_asset_backed: Boolean(assetHash || assetApiId),
        viewParams,
    };
}

export function extractHistoryImageRefs(historyItem = {}) {
    const results = [];
    const outputs = historyItem && typeof historyItem === "object" ? (historyItem.outputs || {}) : {};

    for (const nodeOutput of Object.values(outputs)) {
        const images = Array.isArray(nodeOutput?.images) ? nodeOutput.images : [];
        for (const imageRef of images) {
            const normalized = normalizeComfyOutputRef(imageRef);
            if (normalized) {
                results.push(normalized);
            }
        }
    }

    return results;
}
