import pathlib
import runpy
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _read_simple_toml_value(section_name: str, key_name: str):
    current_section = None
    for raw_line in (ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_section = line.strip("[]")
            continue
        if current_section == section_name and "=" in line:
            key, value = [part.strip() for part in line.split("=", 1)]
            if key == key_name:
                return int(value.split("#", 1)[0].strip())
    raise AssertionError(f"missing pyproject value: [{section_name}] {key_name}")


class R173PackageHygieneTests(unittest.TestCase):
    def test_developer_helpers_are_not_tracked_at_repo_root(self):
        forbidden_root_helpers = {
            "debug_s35_import.py",
            "verify_s30_doctor.py",
        }
        for helper_name in forbidden_root_helpers:
            self.assertFalse(
                (ROOT / helper_name).exists(),
                f"{helper_name} should live under scripts/devtools, not repo root",
            )

        expected_devtools = {
            "scripts/devtools/debug_s35_import.py",
            "scripts/devtools/verify_s30_doctor.py",
        }
        for helper_path in expected_devtools:
            self.assertTrue(
                (ROOT / helper_path).is_file(),
                f"missing relocated helper: {helper_path}",
            )

    def test_relocated_developer_helpers_resolve_repo_root(self):
        for helper_path in (
            "scripts/devtools/debug_s35_import.py",
            "scripts/devtools/verify_s30_doctor.py",
        ):
            namespace = runpy.run_path(str(ROOT / helper_path))
            self.assertEqual(namespace["ROOT"], ROOT)

    def test_python_formatter_line_lengths_are_aligned(self):
        ruff_line_length = _read_simple_toml_value("tool.ruff", "line-length")
        black_line_length = _read_simple_toml_value("tool.black", "line-length")
        isort_line_length = _read_simple_toml_value("tool.isort", "line_length")

        self.assertEqual(ruff_line_length, black_line_length)
        self.assertEqual(black_line_length, isort_line_length)

    def test_package_hygiene_contract_documents_artifact_and_cache_ownership(self):
        from services.package_hygiene import get_package_hygiene_contract

        contract = get_package_hygiene_contract()

        retained_artifacts = {
            artifact["path"]: artifact for artifact in contract["retained_artifacts"]
        }
        self.assertIn("package-lock.json", retained_artifacts)
        self.assertEqual(retained_artifacts["package-lock.json"]["owner"], "frontend")
        self.assertIn("npm ci", retained_artifacts["package-lock.json"]["rationale"])

        cache_owners = {cache["id"]: cache for cache in contract["cache_ownership"]}
        self.assertEqual(cache_owners["runtime_state_cache"]["owner"], "state_dir")
        self.assertFalse(cache_owners["runtime_state_cache"]["tracked"])
        self.assertEqual(
            cache_owners["repo_local_tool_cache"]["cleanup"],
            "safe_to_delete_when_tools_are_not_running",
        )

        helper_paths = {helper["path"] for helper in contract["developer_helpers"]}
        self.assertEqual(
            helper_paths,
            {
                "scripts/devtools/debug_s35_import.py",
                "scripts/devtools/verify_s30_doctor.py",
            },
        )


if __name__ == "__main__":
    unittest.main()
