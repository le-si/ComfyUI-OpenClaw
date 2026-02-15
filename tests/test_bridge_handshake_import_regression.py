"""
Regression tests for bridge handshake import stability.
"""

import importlib
import importlib.util
import sys
import types
import unittest
from pathlib import Path


class TestBridgeHandshakeImportRegression(unittest.TestCase):
    def test_package_mode_import_without_top_level_services(self):
        """
        Ensure bridge_handshake works in package mode even when top-level
        `services` imports are unavailable.
        """
        repo_root = Path(__file__).resolve().parents[1]
        services_dir = repo_root / "services"
        module_path = services_dir / "bridge_handshake.py"

        package_root = "_openclaw_import_regression_pkg"
        package_services = f"{package_root}.services"
        module_name = f"{package_services}.bridge_handshake"

        original_sys_path = list(sys.path)
        removed_services_modules = {}

        try:
            # Remove current repo root/cwd from sys.path so absolute imports like
            # `services.*` fail in this test context.
            repo_root_resolved = repo_root.resolve()
            filtered_path = []
            for entry in sys.path:
                if not entry:
                    continue
                try:
                    if Path(entry).resolve() == repo_root_resolved:
                        continue
                except Exception:
                    pass
                filtered_path.append(entry)
            sys.path = filtered_path

            # Remove already-loaded top-level services modules for strict isolation.
            for name in list(sys.modules):
                if name == "services" or name.startswith("services."):
                    removed_services_modules[name] = sys.modules.pop(name)

            # Build a synthetic package namespace backed by the real repo paths.
            root_mod = types.ModuleType(package_root)
            root_mod.__path__ = [str(repo_root)]
            services_mod = types.ModuleType(package_services)
            services_mod.__path__ = [str(services_dir)]
            sys.modules[package_root] = root_mod
            sys.modules[package_services] = services_mod

            importlib.invalidate_caches()
            spec = importlib.util.spec_from_file_location(module_name, module_path)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            self.assertTrue(callable(module.verify_handshake))
            ok, _, _ = module.verify_handshake(module.BRIDGE_PROTOCOL_VERSION)
            self.assertTrue(ok)
        finally:
            # Clean test namespace modules.
            for name in list(sys.modules):
                if name == package_root or name.startswith(package_root + "."):
                    sys.modules.pop(name, None)

            # Restore prior top-level services modules and sys.path.
            sys.modules.update(removed_services_modules)
            sys.path = original_sys_path


if __name__ == "__main__":
    unittest.main()
