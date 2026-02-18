"""
R114 implementation-record lint tests.
"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _load_lint_module():
    root = Path(__file__).resolve().parents[1]
    module_path = root / "scripts" / "lint_implementation_record.py"
    spec = importlib.util.spec_from_file_location("lint_impl_record_mod", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load lint_implementation_record.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestR114ImplementationRecordLint(unittest.TestCase):
    def setUp(self):
        self.mod = _load_lint_module()

    def test_keyword_trigger_requires_regression_section(self):
        content = """
# Example Record

## 1. What Changed
- Security fix for webhook replay bypass.
"""
        ok, issues = self.mod.lint_record_text(content, strict=False)
        self.assertFalse(ok)
        self.assertTrue(any("Regression Evidence" in issue for issue in issues))

    def test_complete_regression_evidence_passes(self):
        content = """
# Example Record

## 1. What Changed
- Bug fix for callback auth mismatch.

## Regression Evidence
- Defect ID: BUG-123
- Regression Test ID: tests.test_r112_triple_assert_contract.TestR112TripleAssertContract.test_tools_run_unauthorized_triple_assert
- Failing Evidence: local run 2026-02-18 (before fix) -> 403/audit mismatch
- Passing Evidence: local run 2026-02-18 (after fix) -> PASS
"""
        ok, issues = self.mod.lint_record_text(content, strict=False)
        self.assertTrue(ok)
        self.assertEqual(issues, [])

    def test_non_keyword_record_skips_lint_in_non_strict_mode(self):
        content = """
# Example Record

## 1. What Changed
- UI copy update only.
"""
        ok, issues = self.mod.lint_record_text(content, strict=False)
        self.assertTrue(ok)
        self.assertEqual(issues, [])

    def test_lint_records_strict_mode_enforces_all(self):
        with tempfile.TemporaryDirectory() as td:
            record = Path(td) / "260218-EXAMPLE_IMPLEMENTATION_RECORD.md"
            record.write_text(
                """
# Example

## 1. What Changed
- docs tweak.
""",
                encoding="utf-8",
            )
            ok, errors = self.mod.lint_records([td], strict=True)
            self.assertFalse(ok)
            self.assertTrue(any("Regression Evidence" in e for e in errors))

    def test_cli_path_does_not_append_default_directory(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "260218-TARGET_IMPLEMENTATION_RECORD.md"
            target.write_text(
                """
# Target

## Regression Evidence
- Defect ID: BUG-1
- Regression Test ID: tests.test_x
- Failing Evidence: before
- Passing Evidence: after
""",
                encoding="utf-8",
            )

            noisy_dir = Path(td) / ".planning"
            noisy_dir.mkdir(parents=True, exist_ok=True)
            noisy = noisy_dir / "260218-NOISY_IMPLEMENTATION_RECORD.md"
            noisy.write_text(
                """
# Noisy
## 1. What Changed
- missing regression fields
""",
                encoding="utf-8",
            )

            with patch.object(
                sys,
                "argv",
                [
                    "lint_implementation_record.py",
                    "--path",
                    str(target),
                    "--strict",
                ],
            ):
                rc = self.mod.main()
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
