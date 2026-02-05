"""
Preflight Diagnostics Service (R42).

Provides logic to validate a workflow against the local ComfyUI environment,
checking for missing node classes and models.
"""

import logging
import time
from typing import Any, Dict, List, Set, Tuple

_CACHE = {}
_CACHE_TTL = 60  # seconds

# Heuristic mapping: input_key -> folder_paths type
_INPUT_KEY_MAP = {
    "ckpt_name": "checkpoints",
    "checkpoint": "checkpoints",
    "lora_name": "loras",
    "vae_name": "vae",
    "control_net_name": "controlnet",
    "upscale_model_name": "upscale_models",
    "style_model_name": "style_models",
    "clip_name": "clip",
    "unet_name": "unet",
    # Add more as discovered
}


def _get_node_class_mappings() -> Dict[str, Any]:
    """Safely retrieve the global NODE_CLASS_MAPPINGS."""
    if nodes and hasattr(nodes, "NODE_CLASS_MAPPINGS"):
        return nodes.NODE_CLASS_MAPPINGS
    return {}


def _get_model_inventory() -> Dict[str, List[str]]:
    """
    Retrieve snapshot of available models using folder_paths.
    Returns a dict mapping folder name (e.g., 'checkpoints') to list of filenames.
    Cached for 60s to prevent IO spam.
    """
    global _CACHE
    now = time.time()

    cached = _CACHE.get("inventory")
    if cached:
        timestamp, data = cached
        if now - timestamp < _CACHE_TTL:
            return data

    inventory = {}
    if not folder_paths:
        return inventory

    # Common model types to check
    # We use the keys from folder_paths.folder_names_and_paths if available,
    # or a hardcoded list of common ones.
    model_types = [
        "checkpoints",
        "loras",
        "vae",
        "embeddings",
        "controlnet",
        "upscale_models",
        "clip",
        "unet",
        "clip_vision",
        "style_models",
        "diffusers",
        "vae_approx",
        "photomaker",
    ]

    # Add any dynamic ones
    if hasattr(folder_paths, "folder_names_and_paths"):
        for k in folder_paths.folder_names_and_paths.keys():
            if k not in model_types:
                model_types.append(k)

    for mtype in model_types:
        try:
            files = folder_paths.get_filename_list(mtype)
            if files:
                inventory[mtype] = list(files)
        except Exception:
            # Some folders might not exist or raise error
            continue

    _CACHE["inventory"] = (now, inventory)
    return inventory


def run_preflight_check(workflow: Dict[str, Any]) -> Dict[str, Any]:
    """
    Analyze a workflow (API format) and return a diagnostic report.

    Args:
        workflow: The ComfyUI workflow JSON (node ID -> node data).

    Returns:
        Dict containing validation results (missing_nodes, missing_models, etc.)
    """
    report = {
        "ok": True,
        "summary": {"missing_nodes": 0, "missing_models": 0, "invalid_inputs": 0},
        "missing_nodes": [],
        "missing_models": [],
        "invalid_inputs": [],
        "notes": [],
    }

    if not isinstance(workflow, dict):
        report["ok"] = False
        report["notes"].append("Workflow must be a JSON object (API format).")
        return report

    # 1. Check Nodes
    available_nodes = _get_node_class_mappings()
    missing_node_counts: Dict[str, int] = {}

    # 2. Check Models (Heuristic)
    inventory = _get_model_inventory()
    missing_models_counts: Dict[str, Dict[str, Any]] = {}

    for node_id, node_data in workflow.items():
        if not isinstance(node_data, dict):
            continue

        # Check Node Class
        class_type = node_data.get("class_type")
        if not class_type:
            continue

        if available_nodes and class_type not in available_nodes:
            missing_node_counts[class_type] = missing_node_counts.get(class_type, 0) + 1

        # Check Inputs for Models
        inputs = node_data.get("inputs")
        if isinstance(inputs, dict):
            _check_inputs_for_models(inputs, inventory, missing_models_counts)

    # Format Results
    for cls, count in missing_node_counts.items():
        report["missing_nodes"].append({"class_type": cls, "count": count})

    for key, info in missing_models_counts.items():
        report["missing_models"].append(
            {"type": info["type"], "name": info["name"], "count": info["count"]}
        )

    # Summarize
    report["summary"]["missing_nodes"] = len(report["missing_nodes"])
    report["summary"]["missing_models"] = len(report["missing_models"])

    if (
        report["summary"]["missing_nodes"] > 0
        or report["summary"]["missing_models"] > 0
    ):
        report["ok"] = False

    if not nodes:
        report["notes"].append("Node inventory unavailable (backend import failed).")
    if not folder_paths:
        report["notes"].append("Model inventory unavailable (backend import failed).")

    return report


def _check_inputs_for_models(
    inputs: Dict[str, Any],
    inventory: Dict[str, List[str]],
    missing_counts: Dict[str, Dict[str, Any]],
):
    """
    Heuristic to detect missing models in node inputs.
    We look for keys that hint at model types (e.g. 'ckpt_name', 'lora_name').
    """
    # Mapping heuristic: input_key -> folder_paths type
    key_map = _INPUT_KEY_MAP

    for key, value in inputs.items():
        if not isinstance(value, str):
            continue

        target_type = key_map.get(key)
        if target_type:
            # Check if exists
            available = inventory.get(target_type, [])
            if value not in available:
                # Also try normalizing separators just in case (e.g. windows vs linux paths)
                # But typically ComfyUI expects exact match or relative match.
                # Use simple exact match for now.

                unique_key = f"{target_type}:{value}"
                if unique_key not in missing_counts:
                    missing_counts[unique_key] = {
                        "type": target_type,
                        "name": value,
                        "count": 0,
                    }
                missing_counts[unique_key]["count"] += 1
