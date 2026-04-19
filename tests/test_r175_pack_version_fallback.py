import tempfile
import unittest
from pathlib import Path

import config


class TestR175PackVersionFallback(unittest.TestCase):
    def test_fallback_parser_handles_bom_crlf_and_double_quotes(self):
        text = '\ufeff[project]\r\nname = "ComfyUI-OpenClaw"\r\nversion = "9.9.9"\r\n'
        self.assertEqual(
            config._parse_pyproject_version_text(text, prefer_tomllib=False),
            "9.9.9",
        )

    def test_fallback_parser_handles_single_quotes_and_spacing(self):
        text = "[project]\nname='ComfyUI-OpenClaw'\nversion    =    '1.2.3'\n"
        self.assertEqual(
            config._parse_pyproject_version_text(text, prefer_tomllib=False),
            "1.2.3",
        )

    def test_fallback_parser_returns_none_when_project_section_missing(self):
        text = '[tool.black]\nline-length = 88\nversion = "7.7.7"\n'
        self.assertIsNone(
            config._parse_pyproject_version_text(text, prefer_tomllib=False)
        )

    def test_read_version_from_path_returns_none_for_missing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = Path(tmpdir) / "missing.toml"
            self.assertIsNone(
                config._read_pyproject_version_from_path(missing, prefer_tomllib=False)
            )

    def test_repo_pack_version_matches_pyproject_source_of_truth(self):
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        expected = config._read_pyproject_version_from_path(pyproject)
        self.assertEqual(config.PACK_VERSION, expected)


if __name__ == "__main__":
    unittest.main()
