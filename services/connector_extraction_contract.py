"""
Machine-readable connector extraction feasibility contract.
"""

from __future__ import annotations

import copy
from typing import Any, Dict

try:
    from .sidecar_secret_refs import get_sidecar_service_secret_ref_policy
except ImportError:  # pragma: no cover
    from services.sidecar_secret_refs import (
        get_sidecar_service_secret_ref_policy,  # type: ignore
    )

CONNECTOR_EXTRACTION_CONTRACT_VERSION = 1

_CONNECTOR_EXTRACTION_CONTRACT: Dict[str, Any] = {
    "version": CONNECTOR_EXTRACTION_CONTRACT_VERSION,
    "decision": {
        "id": "stay_in_repo_attached_subsystem",
        "status": "recommended_now",
        "go_no_go": "no_go_for_split_now",
        "summary": (
            "Keep the connector in-repo as an optional attached subsystem for now; "
            "do not split it into a standalone package or separate repo yet."
        ),
        "future_candidate": "optional_extra_package_after_shared_contract_extraction",
    },
    "candidate_packaging_options": [
        {
            "id": "stay_in_repo_attached_subsystem",
            "status": "recommended_now",
            "summary": "Current attached-subsystem model under the ComfyUI package boundary.",
        },
        {
            "id": "optional_extra_package_after_shared_contract_extraction",
            "status": "future_candidate",
            "summary": (
                "Potential future split after installation, callback, delivery, and "
                "config/auth seams are independently versioned."
            ),
        },
        {
            "id": "sidecar_only_distribution",
            "status": "no_go_now",
            "summary": (
                "Not currently viable because operator workflows still depend on the "
                "embedded OpenClaw package/runtime and its local APIs."
            ),
        },
        {
            "id": "separate_repo_or_primary_connector_package",
            "status": "no_go_now",
            "summary": (
                "Not currently viable because connector and shared services still "
                "have bidirectional runtime coupling."
            ),
        },
    ],
    "minimum_stable_seam_families": [
        {
            "id": "installation_registry_and_token_refs",
            "summary": (
                "Workspace/account installation lifecycle, token-reference ownership, "
                "tenant scoping, and diagnostics must stay stable before extraction."
            ),
            "entrypoints": [
                "services/connector_installation_registry.py",
                "connector/platforms/slack_installation_manager.py",
                "connector/platforms/feishu_installation_manager.py",
                "api/connector_contracts.py",
            ],
        },
        {
            "id": "interactive_callback_security_contract",
            "summary": (
                "Signed callback envelopes, replay/idempotency checks, action-policy "
                "mapping, and installation resolution must stay shared."
            ),
            "entrypoints": [
                "services/connector_callback_contract.py",
                "connector/security_profile.py",
                "connector/transport_contract.py",
                "connector/platforms/feishu_webhook.py",
            ],
        },
        {
            "id": "delivery_and_result_bridge",
            "summary": (
                "Connector submission, result polling, and callback delivery depend "
                "on stable backend APIs and result shapes."
            ),
            "entrypoints": [
                "connector/openclaw_client.py",
                "connector/results_poller.py",
                "services/callback_delivery.py",
                "api/webhook_submit.py",
            ],
        },
        {
            "id": "config_auth_and_tenant_boundary",
            "summary": (
                "Connector runtime config, admin token expectations, tenant header "
                "behavior, and server-side secret ownership must remain explicit."
            ),
            "entrypoints": [
                "connector/config.py",
                "services/runtime_config.py",
                "services/tenant_context.py",
                "services/secret_store.py",
            ],
        },
    ],
    "current_blockers": [
        {
            "id": "bidirectional_runtime_imports",
            "summary": (
                "Shared services import connector transport/config types while "
                "connector adapters import shared service contracts."
            ),
        },
        {
            "id": "shared_secret_and_state_ownership",
            "summary": (
                "Installation token refs, tenant-aware secret store usage, and "
                "connector state persistence still live in shared repo services."
            ),
        },
        {
            "id": "local_backend_api_contract_not_versioned_for_external_package",
            "summary": (
                "Connector client/result flows still assume in-repo backend API "
                "evolution rather than a separately versioned public package contract."
            ),
        },
        {
            "id": "sidecar_runtime_still_imports_connector_package_directly",
            "summary": (
                "The sidecar runtime under `services/sidecar` still imports connector "
                "config/client modules directly."
            ),
        },
    ],
    "service_env_secret_ref_boundary": get_sidecar_service_secret_ref_policy(),
}


def get_connector_extraction_contract() -> Dict[str, Any]:
    return copy.deepcopy(_CONNECTOR_EXTRACTION_CONTRACT)
