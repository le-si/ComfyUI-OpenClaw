"""
Tests for Template Service (R8/F5).
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.append(os.getcwd())

from services.templates import TemplateService


class TestTemplateService(unittest.TestCase):

    def setUp(self):
        # Create a temp directory for templates
        self.test_dir = tempfile.mkdtemp()

        # Create manifest
        self.manifest = {
            "version": 1,
            "templates": {"t1": {"path": "t1.json", "allowed_inputs": ["input1"]}},
        }
        with open(os.path.join(self.test_dir, "manifest.json"), "w") as f:
            json.dump(self.manifest, f)

        # Create template file
        self.template_data = {"node1": {"inputs": {"text": "{{input1}}"}}}
        with open(os.path.join(self.test_dir, "t1.json"), "w") as f:
            json.dump(self.template_data, f)

        # Create an additional template file that is NOT in the manifest.
        # Policy: `<template_id>.json` on disk should be runnable even without a manifest entry.
        with open(os.path.join(self.test_dir, "t2.json"), "w") as f:
            json.dump({"node1": {"inputs": {"text": "{{input_any}}"}}}, f)

        self.service = TemplateService(templates_root=self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_load_manifest(self):
        """Test manifest loading."""
        config = self.service.get_template_config("t1")
        self.assertIsNotNone(config)
        self.assertEqual(config.path, "t1.json")
        self.assertEqual(config.allowed_inputs, ["input1"])

    def test_unknown_template(self):
        """Test unknown template raises ValueError."""
        with self.assertRaises(ValueError):
            self.service.render_template("unknown", {})

    def test_extra_input_is_ignored(self):
        """Extra inputs should not raise (policy: no per-template input allowlist)."""
        rendered = self.service.render_template("t1", {"forbidden": "val"})
        # Placeholder remains because `forbidden` does not match any placeholder in the template.
        self.assertEqual(rendered["node1"]["inputs"]["text"], "{{input1}}")

    def test_render_substitution(self):
        """Test variable substitution."""
        rendered = self.service.render_template("t1", {"input1": "hello"})
        self.assertEqual(rendered["node1"]["inputs"]["text"], "hello")

    def test_strict_substitution_only(self):
        """Test that partial substitution is NOT performed."""
        # Update template to have partial placeholder
        with open(os.path.join(self.test_dir, "t1.json"), "w") as f:
            json.dump({"node1": {"inputs": {"text": "prefix {{input1}} suffix"}}}, f)

        # Should NOT replace because "prefix {{input1}} suffix" != "{{input1}}"
        rendered = self.service.render_template("t1", {"input1": "hello"})
        self.assertEqual(
            rendered["node1"]["inputs"]["text"], "prefix {{input1}} suffix"
        )

    def test_file_based_template_without_manifest_entry(self):
        """Templates present as `<id>.json` should be runnable even if not in manifest.json."""
        rendered = self.service.render_template("t2", {"input_any": "hello"})
        self.assertEqual(rendered["node1"]["inputs"]["text"], "hello")


if __name__ == "__main__":
    unittest.main()
