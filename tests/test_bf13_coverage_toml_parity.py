import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CoverageTomlParityContractTests(unittest.TestCase):
    def test_ci_unit_test_lane_installs_coverage_with_toml_support(self):
        ci_workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'python -m pip install numpy pillow aiohttp "coverage[toml]"', ci_workflow
        )
        self.assertNotIn(
            "python -m pip install numpy pillow aiohttp coverage\n", ci_workflow
        )

    def test_local_bootstrap_scripts_install_coverage_with_toml_support(self):
        pre_push = (ROOT / "scripts" / "pre_push_checks.sh").read_text(encoding="utf-8")
        windows_full = (ROOT / "scripts" / "run_full_tests_windows.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            'pip_install_or_fail "required for backend coverage gate" "coverage[toml]"',
            pre_push,
        )
        self.assertIn('Invoke-Checked "pip install coverage[toml]"', windows_full)
        self.assertNotIn(
            'pip_install_or_fail "required for backend coverage gate" coverage',
            pre_push,
        )
        self.assertNotIn(
            'Invoke-Checked "pip install coverage" { & $venvPython -m pip install coverage }',
            windows_full,
        )


if __name__ == "__main__":
    unittest.main()
