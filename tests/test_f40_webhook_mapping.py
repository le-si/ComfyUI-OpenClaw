"""
Tests for F40 Webhook Mapping Engine.
"""

import unittest
from services.webhook_mapping import (
    MappingProfile,
    FieldMapping,
    CoercionType,
    apply_mapping,
    resolve_profile,
    _resolve_path,
    _set_path,
    BUILTIN_PROFILES,
)


class TestWebhookMapping(unittest.TestCase):
    def test_path_utilities(self):
        # Resolve
        data = {"a": {"b": [10, 20]}, "c": 30}
        self.assertEqual(_resolve_path(data, "a.b[1]"), (True, 20))
        self.assertEqual(_resolve_path(data, "a.b[99]"), (False, None))
        self.assertEqual(_resolve_path(data, "x.y"), (False, None))
        self.assertEqual(_resolve_path(data, "a.b"), (True, [10, 20]))

        # Set
        target = {}
        _set_path(target, "x.y.z", 100)
        self.assertEqual(target, {"x": {"y": {"z": 100}}})

    def test_apply_mapping_success(self):
        profile = MappingProfile(
            id="test",
            label="Test",
            defaults={"version": 1, "profile_id": "p1"},
            field_mappings=[
                FieldMapping("user.name", "inputs.user"),
                FieldMapping("user.age", "inputs.age", coercion=CoercionType.INT),
                FieldMapping("active", "inputs.is_active", coercion=CoercionType.BOOL),
            ],
        )
        source = {
            "user": {"name": "Alice", "age": "25"},
            "active": "yes",
        }
        mapped, warnings = apply_mapping(profile, source)

        self.assertEqual(mapped["version"], 1)
        self.assertEqual(mapped["profile_id"], "p1")
        self.assertEqual(mapped["inputs"]["user"], "Alice")
        self.assertEqual(mapped["inputs"]["age"], 25)
        self.assertTrue(mapped["inputs"]["is_active"])
        self.assertEqual(warnings, [])

    def test_apply_mapping_missing_required(self):
        profile = MappingProfile(
            id="test",
            label="Test",
            field_mappings=[
                FieldMapping("required_field", "target", required=True),
            ],
        )
        with self.assertRaises(ValueError) as cm:
            apply_mapping(profile, {})
        self.assertIn("required_field", str(cm.exception))

    def test_apply_mapping_coercion_failure(self):
        profile = MappingProfile(
            id="test",
            label="Test",
            field_mappings=[
                FieldMapping("val", "target", coercion=CoercionType.INT),
            ],
        )
        with self.assertRaises(ValueError) as cm:
            apply_mapping(profile, {"val": "not-an-int"})
        self.assertIn("Coercion failed", str(cm.exception))

    def test_resolve_profile(self):
        # Header match
        p = resolve_profile({"X-Webhook-Mapping-Profile": "github_push"})
        self.assertIsNotNone(p)
        self.assertEqual(p.id, "github_push")

        # Source hint match (header)
        p = resolve_profile({"X-Webhook-Source": "Discord"})
        self.assertIsNotNone(p)
        self.assertEqual(p.id, "discord_message")

        # No match
        p = resolve_profile({})
        self.assertIsNone(p)

    def test_github_push_builtin(self):
        profile = BUILTIN_PROFILES["github_push"]
        payload = {
            "repository": {"full_name": "user/repo"},
            "ref": "refs/heads/main",
            "sender": {"login": "dev"},
        }
        mapped, _ = apply_mapping(profile, payload)
        self.assertEqual(mapped["inputs"]["repo_name"], "user/repo")
        self.assertEqual(mapped["inputs"]["ref"], "refs/heads/main")
        self.assertEqual(mapped["inputs"]["actor"], "dev")

    def test_discord_message_builtin(self):
        profile = BUILTIN_PROFILES["discord_message"]
        payload = {
            "content": "!generate cat",
            "author": {"username": "user1"},
        }
        mapped, _ = apply_mapping(profile, payload)
        self.assertEqual(mapped["inputs"]["requirements"], "!generate cat")
        self.assertEqual(mapped["inputs"]["actor"], "user1")


if __name__ == "__main__":
    unittest.main()
