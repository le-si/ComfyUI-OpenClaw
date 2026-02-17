"""
Tests for R91: Runtime Provenance v2 (operator_doctor.py).
Environment-agnostic — tests verify structure, not specific values.
"""

import json
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, ".")

from services.operator_doctor import (
    DoctorReport,
    RuntimeProvenance,
    _detect_managers,
    _detect_python_source,
    check_runtime_provenance,
)


class TestRuntimeProvenance(unittest.TestCase):
    """RuntimeProvenance dataclass tests."""

    def test_all_fields(self):
        p = RuntimeProvenance(
            runtime="python",
            executable="/usr/bin/python3",
            path_executable="/usr/bin/python3",
            version="3.11.0",
            source="system",
            status="ok",
        )
        self.assertEqual(p.runtime, "python")
        self.assertEqual(p.executable, "/usr/bin/python3")
        self.assertEqual(p.source, "system")
        self.assertEqual(p.status, "ok")
        self.assertEqual(p.managers, [])

    def test_with_managers(self):
        p = RuntimeProvenance(
            runtime="python",
            executable="/home/user/.pyenv/python",
            path_executable="/home/user/.pyenv/python",
            version="3.12.0",
            source="manager",
            status="ok",
            managers=["pyenv"],
        )
        self.assertEqual(p.managers, ["pyenv"])

    def test_to_dict(self):
        p = RuntimeProvenance(
            runtime="node",
            executable="/usr/bin/node",
            path_executable="/usr/bin/node",
            version="v20.0.0",
            source="system",
            status="ok",
        )
        d = p.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["runtime"], "node")
        self.assertEqual(d["version"], "v20.0.0")
        self.assertIn("managers", d)


class TestDetectPythonSource(unittest.TestCase):
    """Detect python source — environment-agnostic structure tests."""

    def test_returns_valid_source(self):
        """Source must be one of the valid provenance sources."""
        src = _detect_python_source()
        self.assertIn(src, ("system", "venv", "conda", "manager", "shim"))

    def test_venv_detection_mocked(self):
        with patch("sys.executable", "/home/user/.venv/bin/python"):
            with patch("sys.prefix", "/home/user/.venv"):
                with patch("sys.base_prefix", "/usr"):
                    with patch.dict("os.environ", {}, clear=True):
                        src = _detect_python_source()
                        self.assertEqual(src, "venv")


class TestDetectManagers(unittest.TestCase):
    """Detect version managers — verify return type."""

    def test_returns_list(self):
        mgrs = _detect_managers()
        self.assertIsInstance(mgrs, list)

    def test_no_managers_when_env_clean(self):
        with patch.dict(
            "os.environ",
            {},
            clear=True,
        ):
            mgrs = _detect_managers()
            self.assertEqual(mgrs, [])

    def test_pyenv_detected_when_env_set(self):
        with patch.dict(
            "os.environ",
            {"PYENV_ROOT": "/home/user/.pyenv"},
            clear=True,
        ):
            mgrs = _detect_managers()
            self.assertIn("pyenv", mgrs)

    def test_conda_detected_when_env_set(self):
        with patch.dict(
            "os.environ",
            {"CONDA_PREFIX": "/home/user/miniconda3"},
            clear=True,
        ):
            mgrs = _detect_managers()
            self.assertIn("conda", mgrs)


class TestCheckRuntimeProvenance(unittest.TestCase):
    """Integration: check_runtime_provenance populates report."""

    def test_populates_runtime_provenance(self):
        report = DoctorReport()
        check_runtime_provenance(report)
        env = report.environment
        self.assertIn("runtime_provenance", env)

        # runtime_provenance is JSON string
        prov = json.loads(env["runtime_provenance"])
        self.assertIsInstance(prov, list)
        self.assertGreaterEqual(len(prov), 2)  # at least python + node

        # Check python record
        py_rec = next((r for r in prov if r["runtime"] == "python"), None)
        self.assertIsNotNone(py_rec)
        self.assertIn("executable", py_rec)
        self.assertIn("source", py_rec)
        self.assertIn("status", py_rec)
        self.assertIn("version", py_rec)

    def test_backward_compat_keys(self):
        report = DoctorReport()
        check_runtime_provenance(report)
        env = report.environment
        self.assertIn("sys_executable", env)
        self.assertIn("path_python", env)


if __name__ == "__main__":
    unittest.main()
