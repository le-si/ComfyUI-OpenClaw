"""
R83 Runtime Profile Contract.

Defines the authoritative source of truth for the application's runtime security profile.
This replaces ad-hoc environment variable checks with a single resolver.

Profiles:
- MINIMAL (default): Greatest compatibility, least restrictive.
- HARDENED: Enforces all security controls (auth, egress, replay, redaction) fail-closed.
"""

import enum
import logging
import os

logger = logging.getLogger(__name__)


class RuntimeProfile(enum.Enum):
    """
    Enumeration of supported runtime profiles.
    """

    MINIMAL = "minimal"
    HARDENED = "hardened"


class ProfileResolver:
    """
    Deterministic resolver for the active runtime profile.
    """

    ENV_VAR = "OPENCLAW_RUNTIME_PROFILE"
    DEFAULT_PROFILE = RuntimeProfile.MINIMAL

    @classmethod
    def resolve(cls) -> RuntimeProfile:
        """
        Resolve the active profile from environment variables.

        Returns:
            RuntimeProfile: The active profile.
        """
        val = os.environ.get(cls.ENV_VAR, "").lower().strip()

        if val == "hardened":
            return RuntimeProfile.HARDENED

        if val and val != "minimal":
            logger.warning(
                f"Unknown {cls.ENV_VAR}='{val}', falling back to {cls.DEFAULT_PROFILE.value}"
            )

        return cls.DEFAULT_PROFILE

    @classmethod
    def is_hardened(cls) -> bool:
        """
        Helper to check if the current profile is HARDENED.
        """
        return cls.resolve() == RuntimeProfile.HARDENED


# Singleton accessor for convenience
def get_runtime_profile() -> RuntimeProfile:
    """Get the current active runtime profile."""
    return ProfileResolver.resolve()


def is_hardened_mode() -> bool:
    """Check if the application is running in HARDENED mode."""
    return ProfileResolver.is_hardened()
