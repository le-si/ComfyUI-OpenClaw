"""
S59 — Webhook Mapping Privilege Clamp Tests.

Tests privileged-field injection rejection, type confusion attacks,
post-map schema gate, and allowlist overrides.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.webhook_mapping import (
    ALLOWED_PRIVILEGED_OVERRIDES,
    PRIVILEGED_FIELDS,
    CoercionType,
    FieldMapping,
    MappingProfile,
    apply_mapping,
    validate_canonical_schema,
)


class TestS59PrivilegedFieldClamp(unittest.TestCase):
    """S59: Privileged field injection and escalation tests."""

    # ------------------------------------------------------------------
    # Privileged field rejection
    # ------------------------------------------------------------------

    def test_mapping_to_template_id_blocked_by_default(self):
        """Mapping that targets template_id is blocked unless allowlisted."""
        profile = MappingProfile(
            id="evil_profile",
            label="Evil Profile",
            field_mappings=[
                FieldMapping(
                    source_path="user_input",
                    target_path="template_id",
                    coercion=CoercionType.STRING,
                ),
            ],
        )
        with self.assertRaises(ValueError) as ctx:
            apply_mapping(profile, {"user_input": "malicious_template"})
        self.assertIn("S59", str(ctx.exception))
        self.assertIn("privileged", str(ctx.exception))

    def test_mapping_to_pack_id_blocked(self):
        """pack_id is a privileged field."""
        profile = MappingProfile(
            id="test_profile",
            label="Test",
            field_mappings=[
                FieldMapping(
                    source_path="data",
                    target_path="pack_id",
                    coercion=CoercionType.STRING,
                ),
            ],
        )
        with self.assertRaises(ValueError):
            apply_mapping(profile, {"data": "injected_pack"})

    def test_mapping_to_tool_path_blocked(self):
        """tool_path is a privileged field."""
        profile = MappingProfile(
            id="test_profile",
            label="Test",
            field_mappings=[
                FieldMapping(
                    source_path="cmd",
                    target_path="tool_path",
                    coercion=CoercionType.STRING,
                ),
            ],
        )
        with self.assertRaises(ValueError):
            apply_mapping(profile, {"cmd": "/bin/sh"})

    def test_mapping_to_execution_target_blocked(self):
        """execution_target is a privileged field."""
        profile = MappingProfile(
            id="test_profile",
            label="Test",
            field_mappings=[
                FieldMapping(
                    source_path="target",
                    target_path="execution_target",
                    coercion=CoercionType.STRING,
                ),
            ],
        )
        with self.assertRaises(ValueError):
            apply_mapping(profile, {"target": "remote_exec"})

    def test_mapping_to_admin_override_blocked(self):
        """admin_override is a privileged field."""
        profile = MappingProfile(
            id="test_profile",
            label="Test",
            field_mappings=[
                FieldMapping(
                    source_path="flag",
                    target_path="admin_override",
                    coercion=CoercionType.BOOL,
                ),
            ],
        )
        with self.assertRaises(ValueError):
            apply_mapping(profile, {"flag": "true"})

    def test_privileged_fields_constant_completeness(self):
        """All critical fields are in the PRIVILEGED_FIELDS set."""
        expected = {
            "template_id",
            "profile_id",
            "pack_id",
            "tool_path",
            "execution_target",
            "execution_mode",
            "admin_override",
        }
        self.assertEqual(PRIVILEGED_FIELDS, expected)

    # ------------------------------------------------------------------
    # Allowlist override
    # ------------------------------------------------------------------

    def test_allowlisted_privileged_field_succeeds(self):
        """Explicitly allowlisted (profile, field) pair passes the clamp."""
        profile = MappingProfile(
            id="trusted_profile",
            label="Trusted",
            field_mappings=[
                FieldMapping(
                    source_path="tid",
                    target_path="template_id",
                    coercion=CoercionType.STRING,
                ),
            ],
        )
        # Add allowlist entry
        ALLOWED_PRIVILEGED_OVERRIDES.add(("trusted_profile", "template_id"))
        try:
            result, warnings = apply_mapping(profile, {"tid": "my_template"})
            self.assertEqual(result["template_id"], "my_template")
        finally:
            ALLOWED_PRIVILEGED_OVERRIDES.discard(("trusted_profile", "template_id"))

    def test_builtin_generic_profile_allowlisted(self):
        """The builtin 'generic' profile is allowlisted for template_id and profile_id."""
        self.assertIn(("generic", "template_id"), ALLOWED_PRIVILEGED_OVERRIDES)
        self.assertIn(("generic", "profile_id"), ALLOWED_PRIVILEGED_OVERRIDES)

    # ------------------------------------------------------------------
    # Non-privileged field mapping works normally
    # ------------------------------------------------------------------

    def test_non_privileged_field_mapping_unaffected(self):
        """Mapping to non-privileged fields works as before."""
        profile = MappingProfile(
            id="normal_profile",
            label="Normal",
            field_mappings=[
                FieldMapping(
                    source_path="name",
                    target_path="inputs.user_name",
                    coercion=CoercionType.STRING,
                ),
            ],
        )
        result, _ = apply_mapping(profile, {"name": "Ray"})
        self.assertEqual(result["inputs"]["user_name"], "Ray")

    # ------------------------------------------------------------------
    # Nested privileged field path
    # ------------------------------------------------------------------

    def test_privileged_field_nested_path_blocked(self):
        """Mapping to 'template_id.sub' is blocked (root is template_id)."""
        profile = MappingProfile(
            id="sneaky_profile",
            label="Sneaky",
            field_mappings=[
                FieldMapping(
                    source_path="val",
                    target_path="template_id.override",
                    coercion=CoercionType.STRING,
                ),
            ],
        )
        with self.assertRaises(ValueError):
            apply_mapping(profile, {"val": "injected"})


class TestS59CanonicalSchemaGate(unittest.TestCase):
    """S59: Post-map canonical schema validation tests."""

    def test_valid_payload_passes(self):
        """Well-formed canonical payload passes validation."""
        payload = {
            "template_id": "my_template",
            "profile_id": "default",
            "version": 1,
            "inputs": {"key": "value"},
        }
        ok, errors = validate_canonical_schema(payload)
        self.assertTrue(ok)
        self.assertEqual(errors, [])

    def test_missing_template_id_fails(self):
        """Missing template_id is rejected."""
        payload = {"profile_id": "default", "version": 1}
        ok, errors = validate_canonical_schema(payload)
        self.assertFalse(ok)
        self.assertTrue(any("template_id" in e for e in errors))

    def test_empty_template_id_fails(self):
        """Empty template_id is rejected."""
        payload = {"template_id": "", "version": 1}
        ok, errors = validate_canonical_schema(payload)
        self.assertFalse(ok)
        self.assertTrue(any("template_id" in e for e in errors))

    def test_type_mismatch_template_id(self):
        """Non-string template_id is rejected."""
        payload = {"template_id": 12345, "version": 1}
        ok, errors = validate_canonical_schema(payload)
        self.assertFalse(ok)
        self.assertTrue(any("Type mismatch" in e for e in errors))

    def test_type_mismatch_version(self):
        """Non-int version is rejected."""
        payload = {"template_id": "ok", "version": "not_a_number"}
        ok, errors = validate_canonical_schema(payload)
        self.assertFalse(ok)
        self.assertTrue(any("version" in e for e in errors))

    def test_type_mismatch_inputs_not_dict(self):
        """Non-dict inputs is rejected."""
        payload = {"template_id": "ok", "inputs": "not_a_dict"}
        ok, errors = validate_canonical_schema(payload)
        self.assertFalse(ok)
        self.assertTrue(any("inputs" in e for e in errors))

    def test_oversize_inputs_rejected(self):
        """Inputs exceeding MAX_PAYLOAD_SIZE are rejected."""
        # Create a payload with inputs larger than 256KB
        payload = {
            "template_id": "ok",
            "inputs": {"big_data": "x" * (300 * 1024)},
        }
        ok, errors = validate_canonical_schema(payload)
        self.assertFalse(ok)
        self.assertTrue(any("exceeds max size" in e for e in errors))

    def test_minimal_valid_payload(self):
        """Minimal payload with just template_id passes."""
        payload = {"template_id": "something"}
        ok, errors = validate_canonical_schema(payload)
        self.assertTrue(ok)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
