import io
import json
import os
import shutil
import tempfile
import unittest
import zipfile
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from api.packs import CleanupFileResponse, PacksHandlers
from services.packs.pack_archive import PackArchive, PackError
from services.packs.pack_manifest import create_manifest


class MockMultipartReader:
    def __init__(self, field):
        self._field = field
        self._returned = False

    async def next(self):
        if self._returned:
            return None
        self._returned = True
        return self._field


class MockRequest:
    def __init__(self, reader: MockMultipartReader | None = None):
        self.match_info = {}
        self.query = {}
        self._reader = reader or MockMultipartReader(MockField("pack.zip", b""))

    async def multipart(self):
        return self._reader


class MockField:
    def __init__(self, filename, content):
        self.name = "file"
        self.filename = filename
        self.content = content
        self._chunk_gen = self._chunks()

    def _chunks(self):
        yield self.content
        yield b""

    async def read_chunk(self):
        try:
            return next(self._chunk_gen)
        except StopIteration:
            return b""


class TestPacksIntegrity(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.packs_dir = os.path.join(self.test_dir, "packs")
        os.makedirs(self.packs_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_zip_traversal_protection(self):
        """Ensure paths with '..' or absolute paths are rejected."""
        # Create a malicious zip in memory
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("../evil.txt", "attack")

        zip_buffer.seek(0)

        # Write to file
        zip_path = os.path.join(self.test_dir, "malicious.zip")
        with open(zip_path, "wb") as f:
            f.write(zip_buffer.getvalue())

        # Verify extraction raises PackError
        with self.assertRaises(PackError) as cm:
            PackArchive.extract_pack(zip_path, self.packs_dir)
        self.assertIn("Unsafe filename", str(cm.exception))

    def test_zip_bomb_protection(self):
        """Ensure high compression ratio is rejected."""
        # Create a "bomb" (highly compressible)
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            # 1MB of zeros compresses to very little
            zf.writestr("bomb.txt", b"0" * (1024 * 1024))

        zip_buffer.seek(0)
        zip_path = os.path.join(self.test_dir, "bomb.zip")
        with open(zip_path, "wb") as f:
            f.write(zip_buffer.getvalue())

        # Mock MAX_COMPRESSION_RATIO just for this test to be strict
        # The default is 100. 1MB of 0s might compress to ~1KB -> ratio 1000.
        try:
            PackArchive.extract_pack(zip_path, self.packs_dir)
            self.fail("Should have raised PackError for zip bomb")
        except PackError as e:
            self.assertIn("Compression ratio too high", str(e))

    def test_deterministic_manifest(self):
        """Ensure create_manifest produces sorted, deterministic output."""
        src_dir = os.path.join(self.test_dir, "src")
        os.makedirs(src_dir)

        # Create files in random order
        with open(os.path.join(src_dir, "b.txt"), "w") as f:
            f.write("b")
        with open(os.path.join(src_dir, "a.txt"), "w") as f:
            f.write("a")

        metadata = {
            "name": "test",
            "version": "1.0.0",
            "type": "template",
            "author": "me",
        }

        manifest_path = create_manifest(src_dir, metadata)

        with open(manifest_path, "r") as f:
            content = json.load(f)

        # Check files are sorted by path
        self.assertEqual(content["files"][0]["path"], "a.txt")
        self.assertEqual(content["files"][1]["path"], "b.txt")

        # Check content is identical if run again
        manifest_path_2 = create_manifest(src_dir, metadata)
        with open(manifest_path, "rb") as f1, open(manifest_path_2, "rb") as f2:
            self.assertEqual(f1.read(), f2.read())


class TestPacksApiAsync(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.packs_dir = os.path.join(self.test_dir, "packs")
        os.makedirs(self.packs_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    @patch("api.packs.web")
    async def test_api_import_flow(self, mock_web):
        """Test import handler flow (mocking aiohttp)."""
        # Setup handlers
        handlers = PacksHandlers(self.packs_dir)

        # Mock auth
        handlers._check_auth = AsyncMock(return_value=True)

        handlers.registry.install_pack = MagicMock(
            return_value={"name": "mypack", "version": "1.0.0"}
        )

        field = MockField("pack.zip", b"dummy_content")
        request = MockRequest(reader=MockMultipartReader(field))

        response = await handlers.import_pack_handler(request)

        self.assertIsNotNone(response)
        mock_web.json_response.assert_called_with(
            {"ok": True, "pack": {"name": "mypack", "version": "1.0.0"}}
        )
        handlers.registry.install_pack.assert_called_once()

    async def test_cleanup_file_response(self):
        """Test CleanupFileResponse deletes file."""
        # Create temp file
        fd, path = tempfile.mkstemp()
        os.close(fd)

        # Write something
        with open(path, "w") as f:
            f.write("dummy")

        resp = CleanupFileResponse(path)

        # Mock the super().prepare to behave like an async no-op (or return None)
        # We can't easily mock the super() call directly on the instance,
        # but we can patch aiohttp.web.FileResponse.prepare
        with patch(
            "aiohttp.web.FileResponse.prepare", new_callable=AsyncMock
        ) as mock_super_prepare:
            # Call prepare
            await resp.prepare(MagicMock())

            # Verify file deleted
            self.assertFalse(os.path.exists(path), "File should be deleted")


if __name__ == "__main__":
    unittest.main()
