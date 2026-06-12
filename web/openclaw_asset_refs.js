const PREVIEWABLE_MEDIA_TYPES = new Set(["images", "video", "audio", "3d", "text"]);
const THREE_D_EXTENSIONS = [".obj", ".fbx", ".gltf", ".glb", ".usdz"];
const TEXT_PREVIEW_MAX_LENGTH = 1024;

function pickAssetHash(imageRef = {}) {
    if (!imageRef || typeof imageRef !== "object") {
        return "";
    }
    const direct = typeof imageRef.asset_hash === "string"
        ? imageRef.asset_hash.trim()
        : (typeof imageRef.hash === "string" ? imageRef.hash.trim() : "");
    if (direct) {
        return direct;
    }
    const nested = imageRef.asset;
    if (nested && typeof nested === "object") {
        if (typeof nested.asset_hash === "string" && nested.asset_hash.trim()) {
            return nested.asset_hash.trim();
        }
        if (typeof nested.hash === "string" && nested.hash.trim()) {
            return nested.hash.trim();
        }
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

function has3dExtension(filename = "") {
    return THREE_D_EXTENSIONS.some((ext) => String(filename).toLowerCase().endsWith(ext));
}

function resolveMediaType(imageRef = {}, fallback = "images") {
    if (imageRef && typeof imageRef === "object") {
        const direct = typeof imageRef.media_type === "string"
            ? imageRef.media_type.trim()
            : (typeof imageRef.mediaType === "string" ? imageRef.mediaType.trim() : "");
        if (PREVIEWABLE_MEDIA_TYPES.has(direct)) {
            return direct;
        }
    }
    return PREVIEWABLE_MEDIA_TYPES.has(fallback) ? fallback : "images";
}

function normalizeTextOutputRef(value) {
    if (value == null) {
        return null;
    }
    let content = String(value);
    if (!content) {
        return null;
    }
    const textTruncated = content.length > TEXT_PREVIEW_MAX_LENGTH;
    if (textTruncated) {
        content = content.slice(0, TEXT_PREVIEW_MAX_LENGTH);
    }
    return {
        filename: "",
        subfolder: "",
        type: "output",
        media_type: "text",
        asset_hash: "",
        asset_api_id: "",
        asset_api_required: false,
        resolution: "inline_text",
        unsupported_reason: "",
        is_asset_backed: false,
        content,
        text_truncated: textTruncated,
        viewParams: null,
    };
}

export function normalizeComfyOutputRef(imageRef = {}, mediaType = "images") {
    let outputRef = imageRef;
    const resolvedMediaType = resolveMediaType(outputRef, mediaType);

    if (!outputRef || typeof outputRef !== "object") {
        if (resolvedMediaType === "text") {
            return normalizeTextOutputRef(outputRef);
        }
        if (resolvedMediaType === "3d" && typeof outputRef === "string" && has3dExtension(outputRef)) {
            outputRef = { filename: outputRef, type: "output", subfolder: "" };
        } else {
            return null;
        }
    }

    const finalMediaType = resolveMediaType(outputRef, resolvedMediaType);
    const textContent = typeof outputRef.content === "string" && outputRef.content
        ? outputRef.content
        : (typeof outputRef.text === "string" && outputRef.text ? outputRef.text : "");
    if (finalMediaType === "text" && textContent) {
        return normalizeTextOutputRef(textContent);
    }

    const assetHash = pickAssetHash(outputRef);
    const assetApiId = pickAssetApiId(outputRef);
    const namedFilename = pickFilename(outputRef);
    const filename = namedFilename || assetHash || assetApiId;
    const subfolder = typeof outputRef.subfolder === "string" ? outputRef.subfolder : "";
    const type = typeof outputRef.type === "string" && outputRef.type ? outputRef.type : "output";

    if (!filename) {
        return null;
    }

    const explicitAssetApiRequired = outputRef.asset_api_required === true;
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
        media_type: finalMediaType,
        asset_hash: assetHash || "",
        asset_api_id: assetApiId || "",
        asset_api_required: assetApiRequired,
        resolution: assetApiRequired ? "asset_api_required" : "view",
        unsupported_reason: assetApiRequired ? "asset_api_required" : "",
        is_asset_backed: Boolean(assetHash || assetApiId),
        content: "",
        text_truncated: false,
        viewParams,
    };
}

export function extractHistoryOutputRefs(historyItem = {}) {
    const results = [];
    const outputs = historyItem && typeof historyItem === "object" ? (historyItem.outputs || {}) : {};

    for (const nodeOutput of Object.values(outputs)) {
        if (!nodeOutput || typeof nodeOutput !== "object") {
            continue;
        }
        for (const [mediaType, refs] of Object.entries(nodeOutput)) {
            if (!PREVIEWABLE_MEDIA_TYPES.has(mediaType) || !Array.isArray(refs)) {
                continue;
            }
            for (const imageRef of refs) {
                const normalized = normalizeComfyOutputRef(imageRef, mediaType);
                if (normalized) {
                    results.push(normalized);
                }
            }
        }
    }

    return results;
}

export function extractHistoryImageRefs(historyItem = {}) {
    return extractHistoryOutputRefs(historyItem).filter((ref) => ref.media_type === "images");
}
