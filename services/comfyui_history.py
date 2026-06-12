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


def normalize_history_image_ref(image_ref: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(image_ref, dict):
        return None

    asset_hash = _pick_asset_hash(image_ref)
    asset_api_id = _pick_asset_api_id(image_ref)
    named_filename = _pick_string(image_ref, "filename", "name")
    filename = named_filename or asset_hash or asset_api_id
    subfolder = _pick_string(image_ref, "subfolder")
    img_type = _pick_string(image_ref, "type") or "output"

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
        "asset_hash": asset_hash,
        "asset_api_id": asset_api_id,
        "asset_api_required": asset_api_required,
        "resolution": resolution,
        "view_url": view_url,
    }


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


def extract_images(history_item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract image outputs from a history item.
    Returns list of normalized image refs.
    """
    results = []
    outputs = history_item.get("outputs", {})

    for node_id, node_output in outputs.items():
        images = node_output.get("images", [])
        for img in images:
            normalized = normalize_history_image_ref(img)
            if normalized:
                results.append(normalized)

    return results


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
