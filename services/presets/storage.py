"""
Preset Storage Service (F22).
Local-first, JSON-based storage.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from ..paths import get_presets_dir
from .models import Preset

logger = logging.getLogger("ComfyUI-OpenClaw.services.presets")


class PresetStore:
    """
    Manages persistence of Presets.
    """

    def __init__(self, storage_dir: Optional[Path] = None):
        self.storage_dir = storage_dir or get_presets_dir()

    def _get_path(self, preset_id: str) -> Path:
        return self.storage_dir / f"{preset_id}.json"

    def list_presets(
        self, category: Optional[str] = None, tag: Optional[str] = None
    ) -> List[Preset]:
        """List all presets, optionally filtered."""
        presets = []
        try:
            for file_path in self.storage_dir.glob("*.json"):
                try:
                    p = self._load_file(file_path)
                    if p:
                        if category and p.category != category:
                            continue
                        if tag and tag not in p.tags:
                            continue
                        presets.append(p)
                except Exception as e:
                    logger.warning(f"Failed to load preset {file_path}: {e}")
        except OSError as e:
            logger.error(f"Failed to list presets dir: {e}")
            return []

        # Sort by updated_at desc
        presets.sort(key=lambda x: x.updated_at, reverse=True)
        return presets

    def get_preset(self, preset_id: str) -> Optional[Preset]:
        """Get a specific preset."""
        path = self._get_path(preset_id)
        if not path.exists():
            return None
        return self._load_file(path)

    def save_preset(self, preset: Preset) -> bool:
        """Save/Update a preset."""
        path = self._get_path(preset.id)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(preset.to_dict(), f, indent=2, ensure_ascii=False)
            logger.info(f"Saved preset {preset.id} ({preset.name})")
            return True
        except Exception as e:
            logger.error(f"Failed to save preset {preset.id}: {e}")
            return False

    def delete_preset(self, preset_id: str) -> bool:
        """Delete a preset."""
        path = self._get_path(preset_id)
        if not path.exists():
            return False
        try:
            path.unlink()
            logger.info(f"Deleted preset {preset_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete preset {preset_id}: {e}")
            return False

    def _load_file(self, path: Path) -> Optional[Preset]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return Preset.from_dict(data)
        except Exception:
            return None


# Singleton
preset_store = PresetStore()
