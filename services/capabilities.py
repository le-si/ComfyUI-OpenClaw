"""
Capabilities Service (R19).
Provides capability probing for frontend version compatibility.
"""

import os

if __package__ and "." in __package__:
    from ..config import PACK_NAME, PACK_VERSION
else:  # pragma: no cover (test-only import mode)
    from config import PACK_NAME, PACK_VERSION

from .runtime_profile import get_runtime_profile

API_VERSION = 1


def get_capabilities() -> dict:
    """
    Return capability surface for frontend probing.
    """
    return {
        "api_version": API_VERSION,
        "runtime_profile": get_runtime_profile().value,
        "pack": {
            "name": PACK_NAME,
            "version": PACK_VERSION,
        },
        "features": {
            "webhook_submit": True,
            "logs_tail": True,
            # Legacy flag (kept for older frontends/tests).
            # Do not remove without a migration window + frontend update.
            "doctor": True,
            "job_monitor": True,
            "callback_delivery": True,
            "presets": True,
            "approvals": True,
            "assist_planner": True,
            "assist_refiner": True,
            "scheduler": True,
            "triggers": True,
            "packs": True,
            # R42/F28/R47: Explorer + Preflight + Checkpoints
            "explorer": True,
            "preflight": True,
            "checkpoints": True,
            # R70/F39/R73: Settings contract + UX degradation + Provider governance
            "settings_contract": True,
            "provider_governance": True,
            # F40/R71/R72: Webhook mapping + Job events + Operator doctor
            "webhook_mapping": True,
            "job_events": True,
            "operator_doctor": True,
        },
    }
