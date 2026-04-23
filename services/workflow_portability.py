"""
Workflow portability helpers for OpenClaw-specific nodes.

The contract is intentionally advisory/metadata-only: it documents how OpenClaw
nodes degrade to standard ComfyUI fields without mutating graphs automatically.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Tuple

if __package__ and "." in __package__:
    from ..nodes.portability_contract import (
        PORTABILITY_CONTRACT_VERSION,
        get_node_portability_mappings,
    )
else:  # pragma: no cover (top-level test import mode)
    from nodes.portability_contract import (  # type: ignore
        PORTABILITY_CONTRACT_VERSION,
        get_node_portability_mappings,
    )


def get_workflow_portability_contract() -> Dict[str, Any]:
    return {
        "version": PORTABILITY_CONTRACT_VERSION,
        "export_mode": "advisory_metadata",
        "nodes": get_node_portability_mappings(),
    }


def get_missing_node_fallback(class_type: str) -> Dict[str, Any] | None:
    metadata = get_node_portability_mappings().get(class_type)
    if metadata is None:
        return None
    return {
        "display_name": metadata["display_name"],
        "portable_mode": metadata["portable_mode"],
        "fallback_kind": metadata["fallback_kind"],
        "portable_summary": metadata["portable_summary"],
        "standard_field_targets": list(metadata["standard_field_targets"]),
        "replacement_hints": list(metadata["replacement_hints"]),
    }


def analyze_workflow_portability(workflow: Dict[str, Any]) -> Dict[str, Any]:
    contract = get_workflow_portability_contract()
    entries = []
    detected_class_types = set()
    recommended_actions = []

    for node_id, node_data in _iter_sorted_workflow_nodes(workflow):
        class_type = node_data.get("class_type")
        if not isinstance(class_type, str):
            continue
        metadata = contract["nodes"].get(class_type)
        if metadata is None:
            continue
        detected_class_types.add(class_type)
        recommended_actions.extend(metadata["replacement_hints"])
        entries.append(
            {
                "node_id": str(node_id),
                "class_type": class_type,
                "display_name": metadata["display_name"],
                "portable_mode": metadata["portable_mode"],
                "fallback_kind": metadata["fallback_kind"],
                "portable_summary": metadata["portable_summary"],
                "standard_field_targets": list(metadata["standard_field_targets"]),
                "replacement_hints": list(metadata["replacement_hints"]),
            }
        )

    total_nodes = len(entries)
    portable_mode_required = total_nodes > 0
    portable_mode_supported = (
        portable_mode_required
        and all(entry["portable_mode"] != "unsupported" for entry in entries)
    )

    return {
        "contract_version": contract["version"],
        "export_mode": contract["export_mode"],
        "summary": {
            "openclaw_nodes": total_nodes,
            "portable_mode_required": portable_mode_required,
            "portable_mode_supported": portable_mode_supported,
            "requires_manual_rewire": portable_mode_required,
        },
        "detected_class_types": sorted(detected_class_types),
        "recommended_actions": _dedupe_preserve_order(recommended_actions),
        "openclaw_nodes": entries,
    }


def _iter_sorted_workflow_nodes(
    workflow: Dict[str, Any],
) -> Iterable[Tuple[str, Dict[str, Any]]]:
    def sort_key(item: Tuple[Any, Any]) -> Tuple[int, Any]:
        key = item[0]
        if isinstance(key, str) and key.isdigit():
            return (0, int(key))
        return (1, str(key))

    for node_id, node_data in sorted(workflow.items(), key=sort_key):
        if isinstance(node_data, dict):
            yield str(node_id), node_data


def _dedupe_preserve_order(items: Iterable[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result

