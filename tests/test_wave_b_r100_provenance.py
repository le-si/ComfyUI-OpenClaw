import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TestR100Provenance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wave_b_r100_")
        self.artifacts = os.path.join(self.tmp, "dist")
        os.makedirs(self.artifacts, exist_ok=True)
        self.provenance_json = os.path.join(self.artifacts, "provenance.json")
        with open(
            os.path.join(self.artifacts, "artifact_a.txt"), "w", encoding="utf-8"
        ) as f:
            f.write("artifact-a")

        repo_root = Path(__file__).resolve().parents[1]
        self.generate_script = str(repo_root / "scripts" / "generate_provenance.py")
        self.verify_script = str(repo_root / "scripts" / "verify_provenance.py")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, *args],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_provenance_lifecycle(self):
        generate = self._run(self.generate_script, self.artifacts, self.provenance_json)
        self.assertEqual(generate.returncode, 0, generate.stderr + generate.stdout)
        self.assertTrue(os.path.exists(self.provenance_json))

        with open(self.provenance_json, "r", encoding="utf-8") as f:
            payload = json.load(f)
        self.assertIn("artifact_a.txt", payload.get("artifacts", {}))

        verify_ok = self._run(self.verify_script, self.artifacts, self.provenance_json)
        self.assertEqual(verify_ok.returncode, 0, verify_ok.stderr + verify_ok.stdout)

        with open(
            os.path.join(self.artifacts, "artifact_a.txt"), "w", encoding="utf-8"
        ) as f:
            f.write("tampered")
        verify_tampered = self._run(
            self.verify_script, self.artifacts, self.provenance_json
        )
        self.assertNotEqual(verify_tampered.returncode, 0)
        self.assertIn("Checksum mismatch", verify_tampered.stdout)

    def test_unlisted_file_detection(self):
        generate = self._run(self.generate_script, self.artifacts, self.provenance_json)
        self.assertEqual(generate.returncode, 0, generate.stderr + generate.stdout)

        with open(
            os.path.join(self.artifacts, "artifact_b.txt"), "w", encoding="utf-8"
        ) as f:
            f.write("not listed")

        verify = self._run(self.verify_script, self.artifacts, self.provenance_json)
        self.assertNotEqual(verify.returncode, 0)
        self.assertIn("Unlisted artifact found", verify.stdout)


if __name__ == "__main__":
    unittest.main()
