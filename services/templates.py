"""
Template Service (R8/F5).
Loads manifest and renders templates for execution.

- Enforces strict allowlist from manifest
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
        self._load_manifest()

    def _load_manifest(self):
        """Load and validate the template manifest."""
        try:
            data = safe_read_json(self.templates_root, MANIFEST_PATH)
            if data.get("version") != 1:
                logger.error(f"Unsupported manifest version: {data.get('version')}")
                return

            for t_id, t_cfg in data.get("templates", {}).items():
                self.manifest[t_id] = TemplateConfig(
                    path=t_cfg["path"],
                    allowed_inputs=t_cfg.get("allowed_inputs", []),
                    defaults=t_cfg.get("defaults", {}),
                )
            logger.info(f"Loaded {len(self.manifest)} templates from manifest")
        except FileNotFoundError:
            logger.warning("Manifest not found, no templates available")
        except Exception as e:
            logger.error(f"Failed to load manifest: {e}")

    def get_template_config(self, template_id: str) -> Optional[TemplateConfig]:
        return self.manifest.get(template_id)

    def render_template(
        self, template_id: str, inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Render a template into a ComfyUI prompt workflow.

        Args:
            template_id: allowlisted template ID
            inputs: input values to inject

        Returns:
            Rendered ComfyUI workflow JSON (dict)

        Raises:
            ValueError: if template not found or inputs invalid
        """
        config = self.get_template_config(template_id)
        if not config:
            raise ValueError(f"Unknown template: {template_id}")

        # Validate inputs against allowlist
        for key in inputs:
            if key not in config.allowed_inputs:
                raise ValueError(f"Input not allowed: {key}")

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
        # "Render a workflow template... using only allowlisted inputs."
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
    """Return True if template_id exists in the manifest allowlist."""
    return get_template_service().get_template_config(template_id) is not None
