"""\
Smoke test: simulate ComfyUI custom node loader behavior.

Goal: Ensure this repo can be imported when loaded by file path (as ComfyUI does),
without relying on the caller to pre-configure sys.path.

This catches issues like:
- `ModuleNotFoundError: No module named 'services.*'`
- broken relative imports when module is loaded as a package via file loader
"""

import importlib
import importlib.util
import os
import sys
import unittest
from pathlib import Path


class TestComfyUICustomNodeLoaderImport(unittest.TestCase):
    def test_init_import_via_file_loader(self):
        repo_root = Path(__file__).resolve().parent.parent
        init_path = repo_root / "__init__.py"
        self.assertTrue(init_path.exists())

        # Ensure state dir is writable. Prefer an explicit env var (CI sets this),
        # otherwise fall back to a repo-local folder, otherwise a system temp dir.
        import tempfile

        def _try_writable_dir(p: Path) -> str | None:
            try:
                p.mkdir(parents=True, exist_ok=True)
                probe = p / "._write_probe"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink(missing_ok=True)
                return str(p)
            except Exception:
                return None

        chosen = None
        env_dir = os.environ.get("OPENCLAW_STATE_DIR") or os.environ.get(
            "MOLTBOT_STATE_DIR"
        )
        if env_dir:
            chosen = _try_writable_dir(Path(env_dir))

        if not chosen:
            chosen = _try_writable_dir(
                repo_root / "openclaw_state" / "_unittest" / "loader_smoke"
            )

        if not chosen:
            try:
                chosen = tempfile.mkdtemp(prefix="openclaw_state_loader_smoke_")
            except Exception:
                chosen = None

        if not chosen:
            self.skipTest(
                "No writable OPENCLAW_STATE_DIR/MOLTBOT_STATE_DIR available for import smoke test"
            )

        os.environ["OPENCLAW_STATE_DIR"] = str(chosen)
        os.environ["MOLTBOT_STATE_DIR"] = str(chosen)

        name = "comfyui_openclaw_loader_smoke"

        # Simulate ComfyUI: load module by file path as a *package*.
        spec = importlib.util.spec_from_file_location(
            name,
            str(init_path),
            submodule_search_locations=[str(repo_root)],
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)

        old_sys_path = list(sys.path)
        try:
            # Make the test meaningful: remove the repo root if it was already on sys.path.
            sys.path = [
                p
                for p in sys.path
                if os.path.abspath(p) != os.path.abspath(str(repo_root))
            ]

            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)

            self.assertTrue(hasattr(module, "NODE_CLASS_MAPPINGS"))
            self.assertIn("MoltbotPromptPlanner", module.NODE_CLASS_MAPPINGS)
            planner_cls = module.NODE_CLASS_MAPPINGS["MoltbotPromptPlanner"]
            self.assertEqual(planner_cls.__name__, "OpenClawPromptPlanner")

            # After import, `services.*` should be importable because __init__.py must self-heal sys.path.
            llm_mod = importlib.import_module("services.llm_client")
            self.assertTrue(hasattr(llm_mod, "LLMClient"))
            planner_mod = importlib.import_module("nodes.prompt_planner")
            self.assertIs(
                planner_mod.MoltbotPromptPlanner, planner_mod.OpenClawPromptPlanner
            )
        finally:
            sys.path = old_sys_path
            sys.modules.pop(name, None)


if __name__ == "__main__":
    unittest.main()
