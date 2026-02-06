"""
Template Service (R8/F5).
Loads manifest and renders templates for execution.

- Templates are runnable by ID
- Uses safe_io to load files
- Renders workflow JSON with safe inputs
"""

import copy
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .safe_io import resolve_under_root, safe_read_json

logger = logging.getLogger("ComfyUI-OpenClaw.services.templates")

# Root directory for templates
# In production code, this should be absolute path to ComfyUI/custom_nodes/ComfyUI-OpenClaw/data/templates
# We'll determine it dynamically relative to this file
MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
PACK_ROOT = os.path.dirname(MODULE_DIR)
TEMPLATES_ROOT = os.path.join(PACK_ROOT, "data", "templates")
MANIFEST_PATH = "manifest.json"


@dataclass
class TemplateConfig:
    path: str
    allowed_inputs: List[str]
    defaults: Dict[str, Any]


class TemplateService:
    _instance = None

    def __init__(self, templates_root: str = TEMPLATES_ROOT):
        self.templates_root = templates_root
        self.manifest: Dict[str, TemplateConfig] = {}
        self._manifest_abspath = os.path.join(self.templates_root, MANIFEST_PATH)
        self._manifest_mtime: Optional[float] = None
        self._last_load_error: Optional[str] = None
        self._load_manifest()

    def _maybe_reload_manifest(self) -> None:
        """
        Lightweight hot-reload:
        if manifest.json changes on disk, reload it.

        This prevents a common support pitfall where users edit `manifest.json`
        but forget to restart ComfyUI (or restart the UI but not the backend).
        """
        try:
            mtime = os.path.getmtime(self._manifest_abspath)
        except Exception:
            return

        if self._manifest_mtime is None or mtime > self._manifest_mtime:
            self._load_manifest()

    def _discover_template_ids(self) -> List[str]:
        """
        Discover runnable template IDs from disk.

        Policy:
        - Any `data/templates/<template_id>.json` present on disk is considered runnable.
        - `manifest.json` is optional and only used for per-template metadata (defaults, etc).

        Safety boundary is enforced elsewhere:
        - path traversal protection (`safe_io.resolve_under_root`)
        - strict placeholder substitution (no partial replacements)
        - request size limits and execution budgets
        """
        try:
            entries = os.listdir(self.templates_root)
        except Exception:
            entries = []

        ids: set[str] = set()
        for name in entries:
            if not name.endswith(".json"):
                continue
            if name == MANIFEST_PATH:
                continue
            if name.startswith("."):
                continue
            ids.add(os.path.splitext(name)[0])

        ids.update(self.manifest.keys())
        return sorted(ids)

    def _load_manifest(self):
        """Load and validate the template manifest."""
        try:
            self._last_load_error = None
            data = safe_read_json(self.templates_root, MANIFEST_PATH)
            if data.get("version") != 1:
                logger.error(f"Unsupported manifest version: {data.get('version')}")
                self._last_load_error = (
                    f"unsupported_manifest_version:{data.get('version')}"
                )
                return

            self.manifest.clear()
            for t_id, t_cfg in data.get("templates", {}).items():
                self.manifest[t_id] = TemplateConfig(
                    path=t_cfg["path"],
                    allowed_inputs=t_cfg.get("allowed_inputs", []),
                    defaults=t_cfg.get("defaults", {}),
                )
            try:
                self._manifest_mtime = os.path.getmtime(self._manifest_abspath)
            except Exception:
                self._manifest_mtime = None
            logger.info(
                f"Loaded {len(self.manifest)} templates from manifest: {self._manifest_abspath}"
            )
        except FileNotFoundError:
            logger.warning("Manifest not found, no templates available")
            self._last_load_error = "manifest_not_found"
        except Exception as e:
            # IMPORTANT (recurring support issue):
            # When users report "template_id missing" even after editing manifest.json,
            # the FIRST thing to verify is which manifest path was loaded by the running pack.
            # Keep `_manifest_abspath` + `_last_load_error` so `/openclaw/templates?debug=1`
            # can prove what file was actually read in the current runtime.
            self._last_load_error = f"manifest_load_failed:{type(e).__name__}:{e}"
            logger.error(f"Failed to load manifest ({self._manifest_abspath}): {e}")

    def get_debug_info(self) -> Dict[str, Any]:
        """
        Diagnostics-only metadata for support/debug tooling.
        Do not expose this to untrusted callers without an access boundary.
        """
        return {
            "templates_root": self.templates_root,
            "manifest_abspath": self._manifest_abspath,
            "manifest_mtime": self._manifest_mtime,
            "template_ids": sorted(list(self.manifest.keys())),
            "template_count": len(self.manifest),
            "discovered_template_ids": self._discover_template_ids(),
            "last_load_error": self._last_load_error,
        }

    def get_template_config(self, template_id: str) -> Optional[TemplateConfig]:
        self._maybe_reload_manifest()
        cfg = self.manifest.get(template_id)
        if cfg is not None:
            return cfg

        # No manifest entry: treat `<template_id>.json` as runnable if present.
        rel_path = f"{template_id}.json"
        try:
            abs_path = resolve_under_root(self.templates_root, rel_path)
        except Exception:
            return None

        if not os.path.isfile(abs_path):
            return None

        return TemplateConfig(path=rel_path, allowed_inputs=[], defaults={})

    def render_template(
        self, template_id: str, inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Render a template into a ComfyUI prompt workflow.

        Args:
            template_id: template ID
            inputs: input values to inject

        Returns:
            Rendered ComfyUI workflow JSON (dict)

        Raises:
            ValueError: if template not found or inputs invalid
        """
        config = self.get_template_config(template_id)
        if not config:
            raise ValueError(f"Unknown template: {template_id}")

        # NOTE:
        # We intentionally do NOT enforce a per-template input allowlist.
        # Unused keys have no effect because substitutions only happen on exact placeholders.

        # Load template workflow
        # Config path is relative to templates_root (or absolute if inside root? assume relative to manifest)
        # We need to handle the path carefully. The manifest says "data/templates/..." but safe_read expects relative to root.
        # Let's assume manifest paths are relative to PACK_ROOT or TEMPLATES_ROOT.
        # Plan says: "path": "data/templates/portrait_v1.json".
        # If TEMPLATES_ROOT is "data/templates", we need to adjust.
        # Let's simplify: allow manifest to specific filenames relative to TEMPLATES_ROOT.

        rel_path = config.path
        # If config.path starts with "data/templates/", strip it if we are using that as root
        if rel_path.startswith("data/templates/"):
            rel_path = rel_path.replace("data/templates/", "")

        try:
            workflow = safe_read_json(self.templates_root, rel_path)
        except Exception as e:
            logger.error(f"Failed to load template file {rel_path}: {e}")
            raise ValueError("Template loading failed")

        # Merge defaults and inputs
        final_inputs = config.defaults.copy()
        final_inputs.update(inputs)

        # Apply inputs to workflow (Patch-map approach)
        # We need a way to map inputs to node widgets.
        # For MVP, we can assume specific node logic OR we can do simple substitutions if we must.
        # R8/F5 plan says: "simple string substitutions... OR a small patch-map".
        # Let's use a simple convention:
        # In the workflow, we find a node with a special internal property or we rely on node_id mapping if we had it.
        # But we don't have node IDs in the manifest.
        #
        # safer MVP approach:
        # We only support replacing specific known fields on specific node types or
        # we treat the template as having placeholders.
        #
        # Let's try to inject into standard Primitive nodes or specific nodes if we can identify them.
        # BUT wait, the plan implies we *can* submit the inputs.
        # "Render a workflow template... using only safe substitutions."
        #
        # If the template is a saved API format (Graph), it has node IDs.
        # If we just want to pass the inputs to a wrapper node (like MoltBot nodes), that's easier.
        #
        # Let's implement a naive "variable substitution" where we look for values like "{{input_name}}"
        # in the widgets_values of the workflow. This is robust enough for an MVP.
        # We traverse the dict recursively.

        rendered = self._recursive_substitute(workflow, final_inputs)
        return rendered

    def _recursive_substitute(self, data: Any, replacements: Dict[str, Any]) -> Any:
        if isinstance(data, dict):
            return {
                k: self._recursive_substitute(v, replacements) for k, v in data.items()
            }
        elif isinstance(data, list):
            return [self._recursive_substitute(item, replacements) for item in data]
        elif isinstance(data, str):
            # STRICT substitution: exact match only.
            # "Partial" replacements (e.g. "param is {{val}}") are NOT supported
            # to prevent accidental injection or malformed JSON hacks.
            for k, v in replacements.items():
                placeholder = f"{{{{{k}}}}}"
                if data == placeholder:
                    return v
            return data
        else:
            return data


# Singleton accessor
def get_template_service() -> TemplateService:
    if TemplateService._instance is None:
        TemplateService._instance = TemplateService()
    return TemplateService._instance


def is_template_allowed(template_id: str) -> bool:
    """Return True if template_id is runnable (manifest entry or `<id>.json` exists)."""
    return get_template_service().get_template_config(template_id) is not None
