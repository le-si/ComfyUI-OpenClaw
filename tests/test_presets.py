"""
Tests for Preset Storage (F22).
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.presets.models import Preset
from services.presets.storage import PresetStore


class TestPresetStorage(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.store = PresetStore(storage_dir=self.tmp_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir)

    def test_crud(self):
        """Test Create/Read/Update/Delete cycle."""

        # Create
        p1 = Preset.new(
            "My Prompt", {"text": "Hello"}, category="prompt", tags=["test"]
        )
        self.assertTrue(self.store.save_preset(p1))

        # Read
        loaded = self.store.get_preset(p1.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.name, "My Prompt")
        self.assertEqual(loaded.content["text"], "Hello")

        # Update
        loaded.name = "Updated Name"
        self.assertTrue(self.store.save_preset(loaded))

        reloaded = self.store.get_preset(p1.id)
        self.assertEqual(reloaded.name, "Updated Name")

        # Delete
        self.assertTrue(self.store.delete_preset(p1.id))
        self.assertIsNone(self.store.get_preset(p1.id))

    def test_list_filtering(self):
        """Test listing with filters."""
        p1 = Preset.new("P1", {}, category="cat1")
        p2 = Preset.new("P2", {}, category="cat2")
        self.store.save_preset(p1)
        self.store.save_preset(p2)

        # All
        all_presets = self.store.list_presets()
        self.assertEqual(len(all_presets), 2)

        # Filter cat1
        cat1 = self.store.list_presets(category="cat1")
        self.assertEqual(len(cat1), 1)
        self.assertEqual(cat1[0].id, p1.id)

    def test_persistence(self):
        """Test file persistence."""
        p = Preset.new("Persistent", {})
        self.store.save_preset(p)

        # Verify file exists
        path = self.tmp_dir / f"{p.id}.json"
        self.assertTrue(path.exists())

        # New store instance should verify
        store2 = PresetStore(storage_dir=self.tmp_dir)
        loaded = store2.get_preset(p.id)
        self.assertEqual(loaded.name, "Persistent")

    def test_multi_tenant_visibility_filter(self):
        """S49: preset visibility must be tenant-isolated in multi-tenant mode."""
        p1 = Preset.new("A", {})
        p1.tenant_id = "tenant-a"
        p2 = Preset.new("B", {})
        p2.tenant_id = "tenant-b"
        self.store.save_preset(p1)
        self.store.save_preset(p2)

        with patch.dict("os.environ", {"OPENCLAW_MULTI_TENANT_ENABLED": "1"}):
            tenant_a = self.store.list_presets(tenant_id="tenant-a")
            self.assertEqual(len(tenant_a), 1)
            self.assertEqual(tenant_a[0].tenant_id, "tenant-a")

            self.assertIsNone(self.store.get_preset(p2.id, tenant_id="tenant-a"))
            self.assertFalse(self.store.delete_preset(p2.id, tenant_id="tenant-a"))


if __name__ == "__main__":
    unittest.main()
