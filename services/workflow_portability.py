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

INACTIVE_LITEGRAPH_MODES = {2, 4}


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
    suppressed_entries = []
    detected_class_types = set()
    recommended_actions = []

    for node in iter_workflow_diagnostic_nodes(workflow):
        class_type = node.get("class_type")
        if not isinstance(class_type, str):
            continue
        metadata = contract["nodes"].get(class_type)
        if metadata is None:
            continue
        item = {
            "node_id": str(node["node_id"]),
            "class_type": class_type,
            "display_name": metadata["display_name"],
            "portable_mode": metadata["portable_mode"],
            "fallback_kind": metadata["fallback_kind"],
            "portable_summary": metadata["portable_summary"],
            "standard_field_targets": list(metadata["standard_field_targets"]),
            "replacement_hints": list(metadata["replacement_hints"]),
        }
        if not node.get("active", True):
            item["inactive_reason"] = node.get("inactive_reason") or "inactive"
            suppressed_entries.append(item)
            continue
        detected_class_types.add(class_type)
        recommended_actions.extend(metadata["replacement_hints"])
        entries.append(item)

    total_nodes = len(entries)
    portable_mode_required = total_nodes > 0
    portable_mode_supported = portable_mode_required and all(
        entry["portable_mode"] != "unsupported" for entry in entries
    )

    return {
        "contract_version": contract["version"],
        "export_mode": contract["export_mode"],
        "summary": {
            "openclaw_nodes": total_nodes,
            "suppressed_openclaw_nodes": len(suppressed_entries),
            "portable_mode_required": portable_mode_required,
            "portable_mode_supported": portable_mode_supported,
            "requires_manual_rewire": portable_mode_required,
        },
        "detected_class_types": sorted(detected_class_types),
        "recommended_actions": _dedupe_preserve_order(recommended_actions),
        "openclaw_nodes": entries,
        "suppressed_openclaw_nodes": suppressed_entries,
    }


def iter_workflow_diagnostic_nodes(
    workflow: Dict[str, Any]
) -> Iterable[Dict[str, Any]]:
    if not isinstance(workflow, dict):
        return []
    if isinstance(workflow.get("nodes"), list):
        return list(_iter_frontend_workflow_nodes(workflow))
    return list(_iter_api_workflow_nodes(workflow))


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


def _iter_api_workflow_nodes(workflow: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for node_id, node_data in _iter_sorted_workflow_nodes(workflow):
        inactive = _node_is_inactive(node_data)
        yield {
            "node_id": node_id,
            "node_data": node_data,
            "class_type": _node_class_type(node_data),
            "inputs": node_data.get("inputs") if isinstance(node_data, dict) else None,
            "active": not inactive,
            "inactive_reason": "self_inactive" if inactive else None,
            "is_subgraph_container": False,
            "source": "api_prompt",
        }


def _iter_frontend_workflow_nodes(workflow: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    root_nodes = (
        workflow.get("nodes") if isinstance(workflow.get("nodes"), list) else []
    )
    subgraph_defs = _collect_subgraph_defs(
        workflow.get("definitions", {}).get("subgraphs", [])
        if isinstance(workflow.get("definitions"), dict)
        else []
    )
    subgraph_def_map = {str(item["id"]): item for item in subgraph_defs}

    def walk(
        nodes: list[Any],
        *,
        parent_prefix: str = "",
        parent_active: bool = True,
        visiting: set[Tuple[str, str]] | None = None,
    ) -> Iterable[Dict[str, Any]]:
        visiting = visiting or set()
        for raw_node in nodes:
            if not isinstance(raw_node, dict):
                continue
            raw_id = raw_node.get("id")
            if raw_id is None:
                continue
            node_id = f"{parent_prefix}:{raw_id}" if parent_prefix else str(raw_id)
            class_type = _node_class_type(raw_node)
            self_inactive = _node_is_inactive(raw_node)
            active = parent_active and not self_inactive
            if active:
                inactive_reason = None
            elif not parent_active:
                inactive_reason = "ancestor_inactive"
            else:
                inactive_reason = "self_inactive"
            is_subgraph_container = (
                isinstance(class_type, str) and class_type in subgraph_def_map
            )
            yield {
                "node_id": node_id,
                "node_data": raw_node,
                "class_type": class_type,
                "inputs": raw_node.get("inputs"),
                "active": active,
                "inactive_reason": inactive_reason,
                "is_subgraph_container": is_subgraph_container,
                "source": "frontend_workflow",
            }
            if not is_subgraph_container:
                continue
            visit_key = (class_type, node_id)
            if visit_key in visiting:
                continue
            nested_def = subgraph_def_map.get(class_type)
            nested_nodes = nested_def.get("nodes") if nested_def else None
            if not isinstance(nested_nodes, list):
                continue
            next_visiting = set(visiting)
            next_visiting.add(visit_key)
            yield from walk(
                nested_nodes,
                parent_prefix=node_id,
                parent_active=active,
                visiting=next_visiting,
            )

    return list(walk(root_nodes))


def _collect_subgraph_defs(raw_defs: Any) -> list[Dict[str, Any]]:
    result: list[Dict[str, Any]] = []
    seen: set[str] = set()

    def collect(defs: Any) -> None:
        if not isinstance(defs, list):
            return
        for raw_def in defs:
            if not isinstance(raw_def, dict) or not isinstance(raw_def.get("id"), str):
                continue
            def_id = raw_def["id"]
            if def_id in seen:
                continue
            seen.add(def_id)
            result.append(raw_def)
            nested = raw_def.get("definitions")
            if isinstance(nested, dict):
                collect(nested.get("subgraphs"))

    collect(raw_defs)
    return result


def _node_class_type(node_data: Dict[str, Any]) -> str | None:
    class_type = node_data.get("class_type")
    if isinstance(class_type, str):
        return class_type
    node_type = node_data.get("type")
    if isinstance(node_type, str):
        return node_type
    return None


def _node_is_inactive(node_data: Dict[str, Any]) -> bool:
    mode = node_data.get("mode")
    try:
        return int(mode) in INACTIVE_LITEGRAPH_MODES
    except Exception:
        return False


def _dedupe_preserve_order(items: Iterable[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
