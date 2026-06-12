"""
ComfyUI History Service (F17).
Parses ComfyUI /history/{prompt_id} responses and extracts image output metadata.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

try:
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen
except ImportError:
    urlopen = None  # type: ignore

logger = logging.getLogger("ComfyUI-OpenClaw.services.comfyui_history")

COMFYUI_URL = (
    os.environ.get("OPENCLAW_COMFYUI_URL")
    or os.environ.get("MOLTBOT_COMFYUI_URL")
    or "http://127.0.0.1:8188"
)
HISTORY_TIMEOUT = 5
PREVIEWABLE_MEDIA_TYPES = ("images", "video", "audio", "3d", "text")
THREE_D_EXTENSIONS = (".obj", ".fbx", ".gltf", ".glb", ".usdz")
TEXT_PREVIEW_MAX_LENGTH = 1024


def _pick_string(payload: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _pick_asset_hash(image_ref: Dict[str, Any]) -> str:
    asset_hash = _pick_string(image_ref, "asset_hash", "hash")
    if asset_hash:
        return asset_hash

    nested = image_ref.get("asset")
    if isinstance(nested, dict):
        return _pick_string(nested, "asset_hash", "hash")
    return ""


def _pick_asset_api_id(image_ref: Dict[str, Any]) -> str:
    asset_api_id = _pick_string(image_ref, "asset_id")
    if asset_api_id:
        return asset_api_id

    nested = image_ref.get("asset")
    if isinstance(nested, dict):
        return _pick_string(nested, "asset_id", "id")
    return ""


def _has_3d_extension(filename: str) -> bool:
    lower = filename.lower()
    return any(lower.endswith(ext) for ext in THREE_D_EXTENSIONS)


def _normalize_text_content(value: Any) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    text = str(value)
    if text == "":
        return None
    truncated = len(text) > TEXT_PREVIEW_MAX_LENGTH
    if truncated:
        text = text[:TEXT_PREVIEW_MAX_LENGTH]
    return {
        "filename": "",
        "subfolder": "",
        "type": "output",
        "media_type": "text",
        "asset_hash": "",
        "asset_api_id": "",
        "asset_api_required": False,
        "resolution": "inline_text",
        "view_url": "",
        "content": text,
        "text_truncated": truncated,
    }


def normalize_history_output_ref(
    output_ref: Any, media_type: str = "images"
) -> Optional[Dict[str, Any]]:
    resolved_media_type = (
        media_type if media_type in PREVIEWABLE_MEDIA_TYPES else "images"
    )

    if not isinstance(output_ref, dict):
        if resolved_media_type == "text":
            return _normalize_text_content(output_ref)
        if (
            resolved_media_type == "3d"
            and isinstance(output_ref, str)
            and _has_3d_extension(output_ref)
        ):
            output_ref = {"filename": output_ref, "type": "output", "subfolder": ""}
        else:
            return None

    declared_media_type = _pick_string(output_ref, "media_type", "mediaType")
    if declared_media_type in PREVIEWABLE_MEDIA_TYPES:
        resolved_media_type = declared_media_type

    text_content = _pick_string(output_ref, "content", "text")
    if resolved_media_type == "text" and text_content:
        text_ref = _normalize_text_content(text_content)
        if text_ref:
            return text_ref

    asset_hash = _pick_asset_hash(output_ref)
    asset_api_id = _pick_asset_api_id(output_ref)
    named_filename = _pick_string(output_ref, "filename", "name")
    filename = named_filename or asset_hash or asset_api_id
    subfolder = _pick_string(output_ref, "subfolder")
    img_type = _pick_string(output_ref, "type") or "output"

    if not filename:
        return None

    asset_api_required = bool(asset_api_id and not asset_hash and not named_filename)
    view_url = ""
    resolution = "asset_api_required" if asset_api_required else "view"

    if not asset_api_required:
        # IMPORTANT: keep OpenClaw on the bounded /view contract. Asset-hash refs
        # are accepted because they still resolve through /view; do not escalate
        # asset-api-only identifiers into implicit /api/assets runtime fetches.
        if asset_hash:
            params = {"filename": asset_hash}
        else:
            params = {"filename": filename, "type": img_type}
            if subfolder:
                params["subfolder"] = subfolder
        view_url = f"{COMFYUI_URL}/view?{urlencode(params)}"

    return {
        "filename": filename,
        "subfolder": subfolder,
        "type": img_type,
        "media_type": resolved_media_type,
        "asset_hash": asset_hash,
        "asset_api_id": asset_api_id,
        "asset_api_required": asset_api_required,
        "resolution": resolution,
        "view_url": view_url,
        "content": "",
        "text_truncated": False,
    }


def normalize_history_image_ref(image_ref: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return normalize_history_output_ref(image_ref, "images")


def fetch_history(prompt_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetch history for a given prompt_id from ComfyUI.
    Returns the history item dict if found, else None.
    """
    url = f"{COMFYUI_URL}/history/{prompt_id}"
    try:
        req = Request(url, method="GET")
        with urlopen(req, timeout=HISTORY_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get(prompt_id)
    except (URLError, HTTPError, json.JSONDecodeError, TimeoutError) as e:
        logger.warning(f"Failed to fetch history for {prompt_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error fetching history: {e}")
        return None


def extract_output_refs(history_item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract previewable media outputs from a history item."""
    results = []
    outputs = history_item.get("outputs", {})

    for node_output in outputs.values():
        if not isinstance(node_output, dict):
            continue
        for media_type in PREVIEWABLE_MEDIA_TYPES:
            refs = node_output.get(media_type, [])
            if not isinstance(refs, list):
                continue
            for ref in refs:
                normalized = normalize_history_output_ref(ref, media_type)
                if normalized:
                    results.append(normalized)

    return results


def extract_images(history_item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract image outputs from a history item.
    Returns list of normalized image refs.
    """
    return [
        ref
        for ref in extract_output_refs(history_item)
        if ref.get("media_type") == "images"
    ]


def get_job_status(history_item: Optional[Dict[str, Any]]) -> str:
    """
    Determine job status from history item.
    Returns: 'pending', 'running', 'completed', 'error', 'unknown'.
    """
    if history_item is None:
        return "pending"  # Not yet in history

    status = history_item.get("status", {})
    status_str = status.get("status_str", "")

    if status_str == "success":
        return "completed"
    elif status_str == "error":
        return "error"
    elif history_item.get("outputs"):
        return "completed"

    return "unknown"
