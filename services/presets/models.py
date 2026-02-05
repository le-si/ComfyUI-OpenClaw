"""
Preset Service Models (F22).
"""

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


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
        return Preset(**data)
