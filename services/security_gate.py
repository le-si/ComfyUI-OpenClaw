"""
S41 Hardened Enforcement Gate.

Enforces mandatory security controls when running in HARDENED profile.
Fails startup if critical controls are missing or misconfigured.
"""

import logging
from typing import List, Tuple

from .runtime_profile import get_runtime_profile, is_hardened_mode

logger = logging.getLogger(__name__)


class SecurityGate:
    """
    Startup gate that strictly enforces security controls.
    """

    @staticmethod
    def verify_mandatory_controls() -> Tuple[bool, List[str]]:
        """
        Check if all mandatory controls for the current profile are active.
        Returns: (passed: bool, failure_reasons: List[str])
        """
        settings_issues = []

        # 1. Access Control (Auth)
        try:
            from .access_control import is_auth_configured

            if not is_auth_configured():
                settings_issues.append(
                    "Authentication is NOT configured (Admin Token missing)"
                )
        except ImportError:
            settings_issues.append("Could not import access_control service")

        # 2. Egress Policy (SSRF)
        from .runtime_config import get_config

        config = get_config()

        if config.allow_any_public_llm_host:
            settings_issues.append(
                "OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST is enabled (Egress check bypassed)"
            )

        if config.allow_insecure_base_url:
            settings_issues.append(
                "OPENCLAW_ALLOW_INSECURE_BASE_URL is enabled (SSRF check bypassed)"
            )

        # 3. Webhook Security (if Webhook module enabled)
        from .modules import ModuleCapability, is_module_enabled

        if is_module_enabled(ModuleCapability.WEBHOOK):
            # Check if webhook auth is configured (loose check via config,
            # ideally check specific auth mode but config implies it)
            if not config.webhook_auth_mode:
                settings_issues.append(
                    "Webhook module enabled but OPENCLAW_WEBHOOK_AUTH_MODE not set"
                )

        # 4. Redaction
        # (Redaction is always strictly imported in hardened mode; ensure it didn't fail)
        try:
            from .redaction import redact_text

            if not callable(redact_text):
                settings_issues.append("Redaction service is not callable")
        except ImportError:
            settings_issues.append("Redaction service failed to import")

        failures = []

        # In HARDENED mode, any issue is a failure.
        if is_hardened_mode():
            if settings_issues:
                failures.extend(settings_issues)

        return (len(failures) == 0), failures


def enforce_startup_gate() -> None:
    """
    Run the security gate.
    If in HARDENED mode and checks fail -> Raise SystemExit.
    If in MINIMAL mode and checks fail -> Log warnings.
    """
    is_hardened = is_hardened_mode()
    mode_str = "HARDENED" if is_hardened else "MINIMAL"

    logger.info(f"Running S41 Security Gate ({mode_str} profile)...")

    passed, issues = SecurityGate.verify_mandatory_controls()

    if passed:
        logger.info("Security Gate: PASS")
        return

    # Handle failures
    error_msg = f"Security Gate FAILED ({len(issues)} issues):\n" + "\n".join(
        [f"- {i}" for i in issues]
    )

    if is_hardened:
        logger.critical(error_msg)
        logger.critical(
            "FATAL: Hardened profile requires all controls to pass. Startup aborted."
        )
        # S41 Fail-Closed
        raise RuntimeError(error_msg)
    else:
        # Minimal mode: Warning only
        logger.warning(error_msg)
        logger.warning(
            "Continuing startup in MINIMAL mode (Security warnings present)."
        )
