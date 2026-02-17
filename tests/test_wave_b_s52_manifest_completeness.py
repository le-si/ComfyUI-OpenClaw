import hashlib
import os
import shutil
import tempfile
import unittest

from services.packs.pack_manifest import validate_manifest_integrity


class TestS52ManifestCompleteness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wave_b_s52_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unlisted_files_rejected(self):
        pack_json_path = os.path.join(self.tmp, "pack.json")
        with open(pack_json_path, "w", encoding="utf-8") as f:
            f.write('{"name":"demo"}')

        with open(pack_json_path, "rb") as f:
            pack_hash = hashlib.sha256(f.read()).hexdigest()

        with open(os.path.join(self.tmp, "evil.txt"), "w", encoding="utf-8") as f:
            f.write("hidden payload")

        manifest = {
            "files": [
                {
                    "path": "pack.json",
                    "sha256": pack_hash,
                }
            ]
        }
        errors = validate_manifest_integrity(self.tmp, manifest)
        self.assertTrue(any("Unlisted file found: evil.txt" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
