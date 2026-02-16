"""
F52: Parameter Lab backend service.
Handles bounded parameter sweep planning and experiment state persistence.
"""

from __future__ import annotations

import itertools
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from aiohttp import web
except ImportError:
    web = None  # type: ignore

if __package__ and "." in __package__:
    from ..services.access_control import require_admin_token
    from ..services.rate_limit import check_rate_limit
else:  # pragma: no cover (test-only import mode)
    from services.access_control import require_admin_token  # type: ignore
    from services.rate_limit import check_rate_limit  # type: ignore

logger = logging.getLogger("ComfyUI-OpenClaw.services.parameter_lab")

# Configuration
MAX_SWEEP_COMBINATIONS = 50  # Hard cap to prevent queue flooding
EXPERIMENT_RETENTION_COUNT = 20


@dataclass
class SweepDimension:
    node_id: str
    widget_name: str
    values: List[Any] = field(default_factory=list)
    strategy: str = "grid"  # "grid" or "random"
    count: int = 0  # Reserved for random strategy


@dataclass
class SweepPlan:
    experiment_id: str
    workflow_json: str
    dimensions: List[SweepDimension]
    runs: List[Dict[str, Any]]
    created_at: float = field(default_factory=time.time)


class SweepPlanner:
    """Generates bounded sweep plans."""

    def generate(self, workflow: str, params: List[Dict[str, Any]]) -> SweepPlan:
        if not isinstance(workflow, str) or not workflow.strip():
            raise ValueError("workflow_json is required")
        if not isinstance(params, list):
            raise ValueError("params must be a list")

        exp_id = f"exp_{uuid.uuid4().hex[:8]}"
        dimensions: List[SweepDimension] = []

        for p in params:
            if not isinstance(p, dict):
                continue
            node_id = p.get("node_id")
            widget_name = p.get("widget_name")
            if node_id is None or not isinstance(widget_name, str) or not widget_name:
                continue

            dim = SweepDimension(
                node_id=str(node_id),
                widget_name=widget_name,
                values=(
                    p.get("values", []) if isinstance(p.get("values", []), list) else []
                ),
                strategy=str(p.get("strategy", "grid")),
                count=int(p.get("count", 0) or 0),
            )
            dimensions.append(dim)

        overrides_list = self._generate_combinations(dimensions)
        if len(overrides_list) > MAX_SWEEP_COMBINATIONS:
            raise ValueError(
                f"Sweep size {len(overrides_list)} exceeds limit {MAX_SWEEP_COMBINATIONS}"
            )

        return SweepPlan(
            experiment_id=exp_id,
            workflow_json=workflow,
            dimensions=dimensions,
            runs=overrides_list,
        )

    def _generate_combinations(
        self, dimensions: List[SweepDimension]
    ) -> List[Dict[str, Any]]:
        value_lists: List[List[Any]] = []
        keys: List[str] = []

        for dim in dimensions:
            vals = dim.values
            if not vals:
                continue

            key = f"{dim.node_id}.{dim.widget_name}"
            value_lists.append(vals)
            keys.append(key)

        if not value_lists:
            return []

        runs: List[Dict[str, Any]] = []
        for combo in itertools.product(*value_lists):
            override = {}
            for idx, val in enumerate(combo):
                override[keys[idx]] = val
            runs.append(override)
        return runs


class ExperimentStore:
    """Persists experiment metadata."""

    def __init__(self, state_dir: Path):
        self.store_dir = state_dir / "experiments"
        self.store_dir.mkdir(parents=True, exist_ok=True)

    def _enforce_retention(self) -> None:
        """Delete oldest experiments if count exceeds limit."""
        try:
            files = [(f, f.stat().st_mtime) for f in self.store_dir.glob("exp_*.json")]
            files.sort(key=lambda item: item[1], reverse=True)
            for file_path, _ in files[EXPERIMENT_RETENTION_COUNT:]:
                try:
                    file_path.unlink()
                    logger.info("Pruned old experiment: %s", file_path.name)
                except Exception as exc:
                    logger.warning("Failed to prune %s: %s", file_path.name, exc)
        except Exception as exc:
            logger.warning("Retention check failed: %s", exc)

    def save_plan(self, plan: SweepPlan) -> None:
        path = self.store_dir / f"{plan.experiment_id}.json"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(asdict(plan), handle, indent=2)
        self._enforce_retention()

    def get_plan(self, exp_id: str) -> Optional[Dict[str, Any]]:
        path = self.store_dir / f"{exp_id}.json"
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return None

    def list_experiments(self) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        files = sorted(
            self.store_dir.glob("exp_*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for file_path in files:
            try:
                with open(file_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                results.append(
                    {
                        "id": data["experiment_id"],
                        "created_at": data.get("created_at"),
                        "run_count": len(data.get("runs", [])),
                    }
                )
            except Exception:
                continue
        return results

    def update_experiment(
        self, exp_id: str, run_id: str, output: Any = None, status: Optional[str] = None
    ) -> bool:
        path = self.store_dir / f"{exp_id}.json"
        if not path.exists():
            return False

        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)

            if "results" not in data:
                data["results"] = {}
            if run_id not in data["results"]:
                data["results"][run_id] = {}

            if output is not None:
                data["results"][run_id]["output"] = output
            if status is not None:
                data["results"][run_id]["status"] = status
            data["updated_at"] = time.time()

            with open(path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
            return True
        except Exception as exc:
            logger.error("Failed to update experiment %s: %s", exp_id, exc)
            return False


_planner = SweepPlanner()
_store: Optional[ExperimentStore] = None


def get_store() -> ExperimentStore:
    global _store
    if _store is None:
        try:
            from ..config import OPENCLAW_STATE_DIR

            state_dir = Path(OPENCLAW_STATE_DIR)
        except ImportError:
            state_dir = Path("./openclaw_state")
        _store = ExperimentStore(state_dir)
    return _store


def _require_admin(request: web.Request) -> Optional[web.Response]:
    """
    CRITICAL: All /lab routes are admin-grade mutating surfaces and must keep
    auth + rate limit gates to avoid remote abuse and queue-flood vectors.
    """
    if not check_rate_limit(request, "admin"):
        return web.json_response(
            {"ok": False, "error": "rate_limit_exceeded"}, status=429
        )

    allowed, err = require_admin_token(request)
    if not allowed:
        return web.json_response(
            {"ok": False, "error": err or "unauthorized"}, status=403
        )
    return None


async def create_sweep_handler(request: web.Request) -> web.Response:
    if web is None:
        raise RuntimeError("aiohttp not available")

    deny = _require_admin(request)
    if deny:
        return deny

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid_json"}, status=400)

    if not isinstance(data, dict):
        return web.json_response({"ok": False, "error": "invalid_payload"}, status=400)

    workflow = data.get("workflow_json")
    params = data.get("params", [])

    try:
        plan = _planner.generate(workflow, params)
        get_store().save_plan(plan)
        return web.json_response({"ok": True, "plan": asdict(plan)})
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:
        logger.error("Sweep creation failed: %s", exc)
        return web.json_response({"ok": False, "error": "internal_error"}, status=500)


async def list_experiments_handler(request: web.Request) -> web.Response:
    if web is None:
        raise RuntimeError("aiohttp not available")

    deny = _require_admin(request)
    if deny:
        return deny

    experiments = get_store().list_experiments()
    return web.json_response({"ok": True, "experiments": experiments})


async def get_experiment_handler(request: web.Request) -> web.Response:
    if web is None:
        raise RuntimeError("aiohttp not available")

    deny = _require_admin(request)
    if deny:
        return deny

    exp_id = request.match_info.get("exp_id")
    if not exp_id:
        return web.json_response({"ok": False, "error": "missing_id"}, status=400)

    plan = get_store().get_plan(exp_id)
    if not plan:
        return web.json_response({"ok": False, "error": "not_found"}, status=404)
    return web.json_response({"ok": True, "experiment": plan})


async def update_experiment_handler(request: web.Request) -> web.Response:
    if web is None:
        raise RuntimeError("aiohttp not available")

    deny = _require_admin(request)
    if deny:
        return deny

    exp_id = request.match_info.get("exp_id")
    run_id = request.match_info.get("run_id")
    if not exp_id or not run_id:
        return web.json_response({"ok": False, "error": "missing_id"}, status=400)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid_json"}, status=400)

    if not isinstance(data, dict):
        return web.json_response({"ok": False, "error": "invalid_payload"}, status=400)

    success = get_store().update_experiment(
        exp_id, run_id, output=data.get("output"), status=data.get("status")
    )
    if success:
        return web.json_response({"ok": True})
    return web.json_response({"ok": False, "error": "update_failed"}, status=500)
