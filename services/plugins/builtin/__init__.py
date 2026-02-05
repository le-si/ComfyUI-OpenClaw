"""
Built-in Plugins package.
"""

from . import audit_log, model_alias, params_clamp


def register_all():
    """Register all built-in plugins."""
    model_alias.register()
    params_clamp.register()
    audit_log.register()
