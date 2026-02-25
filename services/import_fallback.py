"""
R64 import-fallback helpers.

Keep package-vs-top-level import resolution deterministic without broad
ImportError masking that can hide real regressions in ComfyUI package mode.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Iterable, Tuple


def is_packaged_context(package_name: str | None) -> bool:
    """
    Return True when module is loaded under a real package path (e.g. pack.api).

    Top-level test imports commonly have `__package__ == "api"` (no dot).
    """
    return bool(package_name and "." in package_name)


def import_module_dual(
    package_name: str | None,
    relative_module: str,
    absolute_module: str,
) -> ModuleType:
    """
    Import module using package-relative path in ComfyUI runtime, else top-level.

    IMPORTANT: In packaged context we do not fall back on ImportError. Doing so can
    silently mask real import regressions by resolving another top-level module.
    """
    if is_packaged_context(package_name):
        return importlib.import_module(relative_module, package_name)
    return importlib.import_module(absolute_module)


def import_attrs_dual(
    package_name: str | None,
    relative_module: str,
    absolute_module: str,
    attr_names: Iterable[str],
) -> Tuple[object, ...]:
    """Import multiple attributes from the selected module and return them in order."""
    module = import_module_dual(package_name, relative_module, absolute_module)
    return tuple(getattr(module, name) for name in attr_names)
