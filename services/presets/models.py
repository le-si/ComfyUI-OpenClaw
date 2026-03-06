"""
Preset Service Models (F22).
"""

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

try:
    from ..tenant_context import DEFAULT_TENANT_ID, normalize_tenant_id
except Exception:  # pragma: no cover
    DEFAULT_TENANT_ID = "default"

    def normalize_tenant_id(value, *, field_name="tenant_id"):  # type: ignore
        text = str(value or "").strip().lower()
        if not text:
            raise ValueError(f"{field_name} must be non-empty")
        return text


@dataclass
class Preset:
    """
    Represents a Moltbot Preset (Local-First).
    Used for prompts, settings, or variations.
    """

    id: str
    name: str
    category: str = "general"  # e.g., "prompt", "parameters", "full"
    tags: List[str] = field(default_factory=list)
    content: Dict[str, Any] = field(default_factory=dict)
    tenant_id: str = DEFAULT_TENANT_ID

    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @classmethod
    def new(
        cls,
        name: str,
        content: Dict[str, Any],
        category: str = "general",
        tags: List[str] = None,
    ):
        return cls(
            id=str(uuid.uuid4()),
            name=name,
            content=content,
            category=category,
            tags=tags or [],
            tenant_id=DEFAULT_TENANT_ID,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def validate_content(self) -> None:
        """
        Milestone E: Validate content schema based on category.
        Raises ValueError if invalid.
        """
        if self.category == "prompt":
            # Must have positive/negative prompts
            if not isinstance(self.content, dict):
                raise ValueError("Content must be a JSON object")
            if "positive" not in self.content or "negative" not in self.content:
                raise ValueError(
                    "Prompt preset must contain 'positive' and 'negative' fields"
                )

        elif self.category == "params":
            # Must have 'params' object
            if not isinstance(self.content, dict):
                raise ValueError("Content must be a JSON object")
            if "params" not in self.content or not isinstance(
                self.content["params"], dict
            ):
                raise ValueError("Params preset must contain a 'params' dictionary")

        # 'general' category is freeform, no validation needed

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Preset":
        if "tenant_id" not in data:
            data = dict(data)
            data["tenant_id"] = DEFAULT_TENANT_ID
        preset = Preset(**data)
        preset.tenant_id = normalize_tenant_id(preset.tenant_id, field_name="tenant_id")
        return preset
