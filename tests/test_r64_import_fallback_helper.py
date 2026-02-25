import importlib
import sys
import tempfile
import unittest
from pathlib import Path

from services.import_fallback import (
    import_attrs_dual,
    import_module_dual,
    is_packaged_context,
)


class TestR64ImportFallbackHelper(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        sys.path.insert(0, str(self.tmp_path))
        self.addCleanup(lambda: sys.path.remove(str(self.tmp_path)))
        self._touched_modules = set()

    def _write(self, rel_path: str, content: str) -> None:
        path = self.tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _track_import(self, name: str):
        self._touched_modules.add(name)
        return importlib.import_module(name)

    def tearDown(self):
        for name in sorted(self._touched_modules, key=len, reverse=True):
            sys.modules.pop(name, None)

    def test_is_packaged_context(self):
        self.assertTrue(is_packaged_context("pack.api"))
        self.assertFalse(is_packaged_context("api"))
        self.assertFalse(is_packaged_context(""))
        self.assertFalse(is_packaged_context(None))

    def test_top_level_mode_uses_absolute_module(self):
        self._write("r64_absmod.py", "VALUE = 7\n")
        mod = import_module_dual("api", "..ignored", "r64_absmod")
        self._touched_modules.add("r64_absmod")
        self.assertEqual(mod.VALUE, 7)

    def test_packaged_mode_uses_relative_module(self):
        self._write("r64pkg/__init__.py", "")
        self._write("r64pkg/sub/__init__.py", "")
        self._write("r64pkg/shared_mod.py", "VALUE = 'relative'\n")
        self._write("r64pkg_shared_mod.py", "VALUE = 'absolute'\n")

        mod = import_module_dual("r64pkg.sub", "..shared_mod", "r64pkg_shared_mod")
        self._touched_modules.update({"r64pkg", "r64pkg.sub", "r64pkg.shared_mod"})
        self.assertEqual(mod.VALUE, "relative")

    def test_packaged_mode_does_not_silently_fallback_on_import_error(self):
        self._write("r64_guard_pkg/__init__.py", "")
        self._write("r64_guard_pkg/sub/__init__.py", "")
        self._write("r64_guard_fallback.py", "VALUE = 'fallback'\n")

        with self.assertRaises(ModuleNotFoundError):
            import_module_dual(
                "r64_guard_pkg.sub",
                "..missing_relative",
                "r64_guard_fallback",
            )
        # Fallback module should not be imported when packaged relative import fails.
        self.assertNotIn("r64_guard_fallback", sys.modules)

    def test_import_attrs_dual_returns_ordered_attributes(self):
        self._write("r64_attrs.py", "A=1\nB=2\n")
        a, b = import_attrs_dual("api", "..ignored", "r64_attrs", ("A", "B"))
        self._touched_modules.add("r64_attrs")
        self.assertEqual((a, b), (1, 2))


if __name__ == "__main__":
    unittest.main()
