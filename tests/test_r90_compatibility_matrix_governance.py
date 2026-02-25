"""
R90 compatibility matrix governance tests.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from services.compatibility_matrix_governance import (
    detect_anchor_drift,
    read_matrix_document,
    run_refresh_workflow,
    validate_metadata,
)
from services.operator_doctor import DoctorReport, check_compatibility_matrix_governance


REPO_ROOT = Path(__file__).resolve().parents[1]


class TestR90CompatMatrixGovernance(unittest.TestCase):
    def test_repo_matrix_has_valid_metadata(self):
        doc = read_matrix_document(REPO_ROOT / "docs" / "release" / "compatibility_matrix.md")
        self.assertTrue(doc["has_meta"], msg=doc["issues"])
        validation = validate_metadata(doc["metadata"])
        self.assertTrue(validation["ok"], msg=validation)
        self.assertIn(validation["status"], ("fresh", "warning", "stale"))

    def test_detect_anchor_drift(self):
        published = {
            "comfyui": "a",
            "comfyui_frontend": "b",
            "desktop": "c",
        }
        observed = {
            "comfyui": "a",
            "comfyui_frontend": "b2",
            "desktop": "unknown",
        }
        drift = detect_anchor_drift(published, observed)
        self.assertFalse(drift["ok"])
        self.assertEqual(drift["code"], "R90_ANCHOR_DRIFT")
        self.assertEqual(drift["drift"][0]["anchor"], "comfyui_frontend")

    def test_validate_stale_metadata(self):
        metadata = {
            "schema_version": 1,
            "last_validated_date": "2020-01-01",
            "policy": {"warn_age_days": 1, "max_age_days": 2},
            "anchors": {
                "comfyui": "unknown",
                "comfyui_frontend": "unknown",
                "desktop": "unknown",
            },
        }
        validation = validate_metadata(metadata)
        self.assertTrue(validation["ok"])
        self.assertEqual(validation["status"], "stale")
        self.assertEqual(validation["code"], "R90_MATRIX_STALE")

    def test_refresh_workflow_dry_run_and_apply(self):
        src = REPO_ROOT / "docs" / "release" / "compatibility_matrix.md"
        with tempfile.TemporaryDirectory() as td:
            matrix = Path(td) / "compatibility_matrix.md"
            matrix.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

            dry = run_refresh_workflow(
                matrix_path=matrix,
                observed_anchors={
                    "comfyui": "core-1",
                    "comfyui_frontend": "fe-1",
                    "desktop": "desktop-1",
                },
                apply=False,
                updated_by="test",
            )
            dry_payload = dry.to_dict()
            self.assertIn("collect", dry_payload["stages"])
            self.assertEqual(dry_payload["stages"]["publish"]["mode"], "dry-run")
            self.assertFalse(dry_payload["stages"]["publish"]["updated"])

            applied = run_refresh_workflow(
                matrix_path=matrix,
                observed_anchors={
                    "comfyui": "core-2",
                    "comfyui_frontend": "fe-2",
                    "desktop": "desktop-2",
                },
                apply=True,
                updated_by="test",
            )
            self.assertTrue(applied.ok)
            doc = read_matrix_document(matrix)
            self.assertEqual(doc["metadata"]["anchors"]["comfyui"], "core-2")
            self.assertEqual(doc["metadata"]["evidence"]["updated_by"], "test")

    def test_operator_doctor_warns_when_matrix_stale(self):
        with tempfile.TemporaryDirectory() as td:
            pack_root = Path(td)
            matrix_path = pack_root / "docs" / "release"
            matrix_path.mkdir(parents=True, exist_ok=True)
            matrix_path.joinpath("compatibility_matrix.md").write_text(
                (
                    "# Compatibility Matrix\n\n"
                    "```openclaw-compat-matrix-meta\n"
                    + json.dumps(
                        {
                            "schema_version": 1,
                            "last_validated_date": "2020-01-01",
                            "policy": {"warn_age_days": 1, "max_age_days": 2},
                            "anchors": {
                                "comfyui": "unknown",
                                "comfyui_frontend": "unknown",
                                "desktop": "unknown",
                            },
                        }
                    )
                    + "\n```\n\nbody\n"
                ),
                encoding="utf-8",
            )
            report = DoctorReport()
            check_compatibility_matrix_governance(report, pack_root)
            checks = {c.name: c for c in report.checks}
            self.assertIn("compatibility_matrix_governance", checks)
            self.assertEqual(checks["compatibility_matrix_governance"].severity, "warn")
            self.assertEqual(report.environment["compat_matrix_validation_code"], "R90_MATRIX_STALE")

    def test_script_smoke_emits_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            matrix = Path(td) / "compatibility_matrix.md"
            matrix.write_text(
                (REPO_ROOT / "docs" / "release" / "compatibility_matrix.md").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
            out = Path(td) / "evidence.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "compatibility_matrix_refresh.py"),
                    "--matrix-path",
                    str(matrix),
                    "--anchor-comfyui",
                    "core-x",
                    "--anchor-frontend",
                    "fe-x",
                    "--anchor-desktop",
                    "desk-x",
                    "--output",
                    str(out),
                    "--pretty",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertIn("stages", payload)
            self.assertIn("collect", payload["stages"])
            self.assertIn("R90_PUBLISH_DRY_RUN", payload["decision_codes"])


if __name__ == "__main__":
    unittest.main()

