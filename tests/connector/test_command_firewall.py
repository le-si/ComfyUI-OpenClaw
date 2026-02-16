"""
Unit tests for R97 Command Firewall.
"""

import unittest

from connector.command_firewall import CommandFirewall


class TestCommandFirewall(unittest.TestCase):
    def setUp(self):
        self.firewall = CommandFirewall()

    def test_allowlist(self):
        # Allowed
        self.assertTrue(self.firewall.validate_suggestion("/run valid").is_safe)
        self.assertTrue(self.firewall.validate_suggestion("/status").is_safe)

        # Denied
        res = self.firewall.validate_suggestion("/evil command")
        self.assertFalse(res.is_safe)
        self.assertIn("command_not_allowed", res.safety_reason)

    def test_unsafe_patterns(self):
        # Chaining
        res = self.firewall.validate_suggestion("/run img; rm -rf /")
        self.assertFalse(res.is_safe)
        self.assertIn("unsafe_pattern", res.safety_reason)
        self.assertEqual(res.code, "firewall_unsafe_pattern")
        self.assertEqual(res.severity, "high")

        # Subshell
        res = self.firewall.validate_suggestion("/run $(whoami)")
        self.assertFalse(res.is_safe)
        self.assertIn("unsafe_pattern", res.safety_reason)

    def test_normalization(self):
        # /run template prompt="foo bar" --approval
        raw = '/run my-template prompt="foo bar" --approval size=1024'
        res = self.firewall.validate_suggestion(raw)

        self.assertTrue(res.is_safe, f"Failed: {res.safety_reason}")
        self.assertEqual(res.command, "/run")
        self.assertIn("my-template", res.args)
        self.assertIn("--approval", res.args)
        self.assertEqual(res.flags["prompt"], "foo bar")
        self.assertEqual(res.flags["size"], "1024")

        # Check canonical output string
        rendered = res.to_string()
        self.assertIn('prompt="foo bar"', rendered)
        self.assertIn("size=1024", rendered)
        # Flags are sorted in to_string
        # Expected: /run prompt="foo bar" size=1024 my-template --approval
        # Wait, the flags logic in to_string assumes key=value flags.
        # But we also have positional args like template_id.

        # The skeleton to_string:
        # parts = [self.command]
        # for k in sorted(self.flags.keys()): ... parts.append(f"{k}={v}")
        # parts.extend(self.args)

        # So output should be: /run prompt="foo bar" size=1024 my-template --approval
        # "my-template" and "--approval" are in args.

        self.assertTrue(rendered.startswith("/run"))
        self.assertTrue('prompt="foo bar"' in rendered)

    def test_contract_fields(self):
        res = self.firewall.validate_suggestion("/run valid")
        contract = res.to_contract()
        self.assertEqual(contract["code"], "firewall_allow")
        self.assertEqual(contract["action"], "allow")


if __name__ == "__main__":
    unittest.main()
