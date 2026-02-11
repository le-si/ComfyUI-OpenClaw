"""
Settings Schema Registry (R70).
Defines strict schema for all OpenClaw UI-persisted settings keys.
Provides type coercion, validation, and unknown-key rejection.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("ComfyUI-OpenClaw.services.settings_schema")


class SettingType(Enum):
    """Supported setting value types."""

    STRING = "string"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    ENUM = "enum"
    LIST_STRING = "list_string"


@dataclass
class SettingDef:
    """Definition for a single registered setting key."""

    key: str
    type: SettingType
    default: Any
    description: str = ""
    min_val: Optional[Union[int, float]] = None
    max_val: Optional[Union[int, float]] = None
    enum_values: Optional[List[str]] = None
    apply_mode: str = "immediate"  # "immediate" | "restart"
    category: str = "llm"  # "llm" | "scheduler" | "ui"


# ──────────────────────────── Schema Registry ────────────────────────────

_REGISTRY: Dict[str, SettingDef] = {}


def register_setting(defn: SettingDef) -> None:
    """Register a setting definition. Overwrites silently if key exists."""
    _REGISTRY[defn.key] = defn


def get_setting_def(key: str) -> Optional[SettingDef]:
    """Return definition for a registered key, or None."""
    return _REGISTRY.get(key)


def is_registered(key: str) -> bool:
    """Check whether a key is in the registry."""
    return key in _REGISTRY


def list_registered_keys() -> List[str]:
    """Return sorted list of all registered setting keys."""
    return sorted(_REGISTRY.keys())


def get_schema_map() -> Dict[str, dict]:
    """Return JSON-serializable schema map for frontend consumption."""
    out = {}
    for k, d in _REGISTRY.items():
        entry: dict = {
            "type": d.type.value,
            "default": d.default,
            "apply_mode": d.apply_mode,
            "category": d.category,
        }
        if d.description:
            entry["description"] = d.description
        if d.min_val is not None:
            entry["min"] = d.min_val
        if d.max_val is not None:
            entry["max"] = d.max_val
        if d.enum_values:
            entry["enum_values"] = d.enum_values
        out[k] = entry
    return out


# ──────────────────────────── Coercion ────────────────────────────


def coerce_value(key: str, raw: Any) -> Tuple[Any, Optional[str]]:
    """
    Coerce *raw* to the registered type for *key*.

    Returns:
        (coerced_value, error_message_or_None)
    """
    defn = _REGISTRY.get(key)
    if defn is None:
        return raw, f"Unknown setting key: {key}"

    try:
        return _coerce(defn, raw), None
    except (ValueError, TypeError) as exc:
        return defn.default, f"Coercion error for '{key}': {exc}"


def _coerce(defn: SettingDef, raw: Any) -> Any:
    """Internal coercion dispatcher."""
    if raw is None:
        return defn.default

    if defn.type == SettingType.STRING:
        return str(raw)

    if defn.type == SettingType.INT:
        val = int(raw)
        if defn.min_val is not None:
            val = max(int(defn.min_val), val)
        if defn.max_val is not None:
            val = min(int(defn.max_val), val)
        return val

    if defn.type == SettingType.FLOAT:
        val = float(raw)
        if defn.min_val is not None:
            val = max(float(defn.min_val), val)
        if defn.max_val is not None:
            val = min(float(defn.max_val), val)
        return val

    if defn.type == SettingType.BOOL:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "on")
        return bool(raw)

    if defn.type == SettingType.ENUM:
        s = str(raw).strip()
        if defn.enum_values and s not in defn.enum_values:
            raise ValueError(f"'{s}' not in allowed values: {defn.enum_values}")
        return s

    if defn.type == SettingType.LIST_STRING:
        if isinstance(raw, list):
            return [str(x) for x in raw]
        if isinstance(raw, str):
            return [x.strip() for x in raw.split(",") if x.strip()]
        raise TypeError(f"Cannot coerce {type(raw).__name__} to list_string")

    return raw


def coerce_dict(updates: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """
    Coerce all values in *updates* through the schema registry.

    Returns:
        (coerced_dict, list_of_error_messages)
    """
    coerced: Dict[str, Any] = {}
    errors: List[str] = []

    for key, raw in updates.items():
        if not is_registered(key):
            errors.append(f"Unknown setting key: {key}")
            continue
        val, err = coerce_value(key, raw)
        if err:
            errors.append(err)
        else:
            coerced[key] = val

    return coerced, errors


# ──────────────────────────── Bootstrap defaults ────────────────────────────


def _register_defaults() -> None:
    """Register built-in OpenClaw setting definitions."""
    _defs = [
        SettingDef(
            key="provider",
            type=SettingType.STRING,
            default="openai",
            description="LLM provider ID",
            category="llm",
        ),
        SettingDef(
            key="model",
            type=SettingType.STRING,
            default="gpt-4o-mini",
            description="LLM model ID",
            category="llm",
        ),
        SettingDef(
            key="base_url",
            type=SettingType.STRING,
            default="",
            description="Custom base URL (empty = provider default)",
            category="llm",
        ),
        SettingDef(
            key="timeout_sec",
            type=SettingType.INT,
            default=120,
            min_val=5,
            max_val=300,
            description="LLM request timeout in seconds",
            category="llm",
        ),
        SettingDef(
            key="max_retries",
            type=SettingType.INT,
            default=3,
            min_val=0,
            max_val=10,
            description="Max LLM retry attempts",
            category="llm",
        ),
        SettingDef(
            key="fallback_models",
            type=SettingType.LIST_STRING,
            default=[],
            description="Failover model list (comma-separated)",
            category="llm",
        ),
        SettingDef(
            key="fallback_providers",
            type=SettingType.LIST_STRING,
            default=[],
            description="Failover provider list (comma-separated)",
            category="llm",
        ),
        SettingDef(
            key="max_failover_candidates",
            type=SettingType.INT,
            default=3,
            min_val=1,
            max_val=5,
            description="Max failover candidates",
            category="llm",
        ),
    ]
    for d in _defs:
        register_setting(d)


# Auto-register on import
_register_defaults()
