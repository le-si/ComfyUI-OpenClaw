from enum import Enum
from typing import List, Optional, TypedDict


class PackType(str, Enum):
    PRESET = "preset"
    TEMPLATE = "template"
    PROFILE = "profile"


class PackMetadata(TypedDict, total=False):
    name: str
    version: str
    type: PackType
    author: str
    description: Optional[str]
    # New preferred field name (ComfyUI-OpenClaw)
    min_openclaw_version: str
    # Legacy field name (ComfyUI-moltbot)
    min_moltbot_version: str


class PackManifestItem(TypedDict):
    path: str
    sha256: str
    size_bytes: int


class PackManifest(TypedDict):
    files: List[PackManifestItem]
    generated_at: str  # ISO8601
