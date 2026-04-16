"""Security Doctor domain check registry."""

from .security_doctor_connector_checks import check_connector_security_posture
from .security_doctor_endpoint_checks import (
    check_api_key_posture,
    check_csrf_no_origin_override,
    check_endpoint_exposure,
    check_feature_flags,
    check_public_shared_surface_boundary,
    check_ssrf_posture,
    check_token_boundaries,
    check_vulnerability_advisories,
)
from .security_doctor_runtime_checks import (
    check_comfyui_runtime,
    check_hardening_wave2,
    check_redaction_drift,
    check_runtime_guardrails,
    check_s45_exposure_posture,
    check_state_dir_permissions,
)

SECURITY_DOCTOR_CHECKS = (
    check_s45_exposure_posture,
    check_endpoint_exposure,
    check_public_shared_surface_boundary,
    check_token_boundaries,
    check_ssrf_posture,
    check_state_dir_permissions,
    check_redaction_drift,
    check_comfyui_runtime,
    check_runtime_guardrails,
    check_csrf_no_origin_override,
    check_feature_flags,
    check_vulnerability_advisories,
    check_api_key_posture,
    check_connector_security_posture,
    check_hardening_wave2,
)

__all__ = [
    "SECURITY_DOCTOR_CHECKS",
    "check_endpoint_exposure",
    "check_public_shared_surface_boundary",
    "check_csrf_no_origin_override",
    "check_token_boundaries",
    "check_ssrf_posture",
    "check_state_dir_permissions",
    "check_redaction_drift",
    "check_comfyui_runtime",
    "check_feature_flags",
    "check_api_key_posture",
    "check_vulnerability_advisories",
    "check_connector_security_posture",
    "check_hardening_wave2",
    "check_s45_exposure_posture",
    "check_runtime_guardrails",
]
