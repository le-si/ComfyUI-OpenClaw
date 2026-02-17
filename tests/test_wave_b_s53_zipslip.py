import os
import shutil
import tempfile
import unittest
import zipfile

from services.packs.pack_archive import PackArchive, PackError


class TestS53ZipSlip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wave_b_s53_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unicode_traversal(self):
        zip_path = os.path.join(self.tmp, "unicode_traversal.zip")
        # U+FF0E FULLWIDTH FULL STOP x2 normalizes to ".." under NFKC.
        unsafe_name = "\uFF0E\uFF0E/evil.txt"

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(unsafe_name, "payload")

        with self.assertRaisesRegex(PackError, "Unsafe filename"):
            PackArchive.extract_pack(zip_path, os.path.join(self.tmp, "out"))

    def test_drive_relative_path_rejected(self):
        zip_path = os.path.join(self.tmp, "drive_relative.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("C:evil.txt", "payload")

        with self.assertRaisesRegex(PackError, "Unsafe filename"):
            PackArchive.extract_pack(zip_path, os.path.join(self.tmp, "out2"))


if __name__ == "__main__":
    unittest.main()
