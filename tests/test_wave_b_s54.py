import os
import shutil
import tempfile
import unittest

from services.tool_runner import SandboxProfile


class TestS54PathTraversal(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="wave_b_s54_")
        self.allowed = os.path.join(self.root, "allowed")
        self.sibling = os.path.join(self.root, "allowed_evil")
        os.makedirs(self.allowed, exist_ok=True)
        os.makedirs(self.sibling, exist_ok=True)

        self.profile = SandboxProfile(
            network=False,
            allow_fs_read=[self.allowed],
            allow_fs_write=[self.allowed],
            allow_network_hosts=[],
        )

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_prefix_bypass(self):
        nested_ok = os.path.join(self.allowed, "safe.txt")
        sibling_bad = os.path.join(self.sibling, "escape.txt")

        ok = self.profile.validate_fs_access([nested_ok], write=False)
        bad = self.profile.validate_fs_access([sibling_bad], write=False)

        self.assertEqual(ok, [])
        self.assertTrue(any("not in allow_fs_read" in item for item in bad))

    def test_symlink_escape(self):
        outside_file = os.path.join(self.root, "outside.txt")
        with open(outside_file, "w", encoding="utf-8") as f:
            f.write("outside")

        link_path = os.path.join(self.allowed, "link_to_outside.txt")
        try:
            os.symlink(outside_file, link_path)
        except (AttributeError, NotImplementedError, OSError):
            self.skipTest("Symlink creation not available in this environment")

        violations = self.profile.validate_fs_access([link_path], write=False)
        self.assertTrue(any("not in allow_fs_read" in item for item in violations))


if __name__ == "__main__":
    unittest.main()
