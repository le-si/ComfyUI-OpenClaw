import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "registry_publish_guard.py"
PUBLISH_WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"


def _pyproject_text(version: str) -> str:
    return "[project]\n" 'name = "comfyui-openclaw"\n' f'version = "{version}"\n'


class RegistryPublishVersionGuardTests(unittest.TestCase):
    def test_publish_workflow_uses_version_guard_before_registry_publish(self):
        workflow = PUBLISH_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("fetch-depth: 2", workflow)
        self.assertIn("scripts/registry_publish_guard.py", workflow)
        self.assertIn(
            "if: steps.publish_guard.outputs.should_publish == 'true'", workflow
        )

    def test_same_version_sets_should_publish_false(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            current = tmp / "current.toml"
            previous = tmp / "previous.toml"
            output = tmp / "github_output.txt"
            current.write_text(_pyproject_text("0.8.8"), encoding="utf-8")
            previous.write_text(_pyproject_text("0.8.8"), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--pyproject",
                    str(current),
                    "--previous-pyproject",
                    str(previous),
                    "--github-output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                cwd=str(ROOT),
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            output_text = output.read_text(encoding="utf-8")
            self.assertIn("should_publish=false", output_text)

    def test_changed_version_sets_should_publish_true(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            current = tmp / "current.toml"
            previous = tmp / "previous.toml"
            output = tmp / "github_output.txt"
            current.write_text(_pyproject_text("0.8.9"), encoding="utf-8")
            previous.write_text(_pyproject_text("0.8.8"), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--pyproject",
                    str(current),
                    "--previous-pyproject",
                    str(previous),
                    "--github-output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                cwd=str(ROOT),
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            output_text = output.read_text(encoding="utf-8")
            self.assertIn("should_publish=true", output_text)

    def test_missing_previous_pyproject_defaults_to_publish_true(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            current = tmp / "current.toml"
            output = tmp / "github_output.txt"
            current.write_text(_pyproject_text("0.8.8"), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--pyproject",
                    str(current),
                    "--previous-pyproject",
                    str(tmp / "missing.toml"),
                    "--github-output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                cwd=str(ROOT),
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            output_text = output.read_text(encoding="utf-8")
            self.assertIn("should_publish=true", output_text)


if __name__ == "__main__":
    unittest.main()
