"""
Tests for R94: Config merge-patch hardening (runtime_config.py).
"""

import sys
import unittest

sys.path.insert(0, ".")

from services.runtime_config import _merge_config_value


class TestMergeConfigValue(unittest.TestCase):
    """_merge_config_value non-destructive merge tests."""

    def test_scalar_overwrite(self):
        self.assertEqual(_merge_config_value("old", "new"), "new")
        self.assertEqual(_merge_config_value(1, 2), 2)
        self.assertEqual(_merge_config_value(True, False), False)

    def test_dict_recursive_merge(self):
        base = {"a": 1, "b": {"c": 2, "d": 3}}
        patch = {"b": {"c": 99, "e": 4}}
        result = _merge_config_value(base, patch)
        self.assertEqual(result, {"a": 1, "b": {"c": 99, "d": 3, "e": 4}})

    def test_id_keyed_list_merge(self):
        base = [
            {"id": "m1", "name": "model1", "temp": 0.7},
            {"id": "m2", "name": "model2", "temp": 0.5},
        ]
        patch = [
            {"id": "m1", "temp": 0.9},
            {"id": "m3", "name": "model3", "temp": 0.3},
        ]
        result = _merge_config_value(base, patch, key="models")
        self.assertEqual(len(result), 3)
        # m1 updated
        m1 = next(r for r in result if r["id"] == "m1")
        self.assertEqual(m1["temp"], 0.9)
        self.assertEqual(m1["name"], "model1")  # preserved
        # m2 unchanged
        m2 = next(r for r in result if r["id"] == "m2")
        self.assertEqual(m2["temp"], 0.5)
        # m3 appended
        m3 = next(r for r in result if r["id"] == "m3")
        self.assertEqual(m3["name"], "model3")

    def test_id_keyed_base_non_id_patch_keeps_base(self):
        """S/R94: if base is id-keyed but patch is not, keep base."""
        base = [{"id": "a", "v": 1}]
        patch = ["raw_string"]
        result = _merge_config_value(base, patch, key="test")
        self.assertEqual(result, base)

    def test_non_id_list_overwrite(self):
        base = [1, 2, 3]
        patch = [4, 5]
        result = _merge_config_value(base, patch)
        self.assertEqual(result, [4, 5])

    def test_empty_base_list(self):
        base = []
        patch = [{"id": "x", "v": 1}]
        result = _merge_config_value(base, patch)
        self.assertEqual(result, [{"id": "x", "v": 1}])

    def test_none_base(self):
        result = _merge_config_value(None, {"a": 1})
        self.assertEqual(result, {"a": 1})

    def test_type_mismatch_patch_wins(self):
        result = _merge_config_value("old_string", {"a": 1})
        self.assertEqual(result, {"a": 1})


if __name__ == "__main__":
    unittest.main()
