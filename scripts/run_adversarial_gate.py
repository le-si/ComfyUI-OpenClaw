"""
R118 -- Adversarial Gate Runner.

Unified entry point for adversarial verification suites:
- R111: Fuzz / property-based testing (bounded, deterministic when seeded)
- R113: Mutation testing with kill-rate threshold

Supports two profiles:
- ``smoke``: Fast, bounded, deterministic -- required on PR/push CI.
- ``extended``: Deeper coverage -- nightly/manual dispatch.

Usage:
    python scripts/run_adversarial_gate.py --profile smoke
    python scripts/run_adversarial_gate.py --profile extended --seed 42
    python scripts/run_adversarial_gate.py --profile smoke --artifact-dir .tmp/adversarial

CRITICAL: keep fuzz seed/runner deterministic and bounded.
IMPORTANT: do not downgrade mutation threshold to report-only unless explicitly
           approved in roadmap.
"""

import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
from typing import Any, Dict, List


def run_fuzz_suite(seed: int, max_runs: int, artifact_dir: str) -> Dict[str, Any]:
    """
    Run R111 fuzz harness with deterministic seed and bounded iteration.

    Returns:
        Result dict with pass/fail, crash count, seed, and artifact paths.
    """
    # Set global seed for reproducibility
    random.seed(seed)

    # Import fuzz suite from tests
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))

    from test_r111_fuzz_harness import (  # type: ignore
        Fuzzer,
        FuzzStrategies,
        fuzz_is_loopback,
        fuzz_policy_bundle,
        fuzz_url_validation,
    )

    os.makedirs(artifact_dir, exist_ok=True)

    # Override the default artifact dir
    import test_r111_fuzz_harness

    test_r111_fuzz_harness.ARTIFACT_DIR = artifact_dir

    fuzzer = Fuzzer()

    import string
    from unittest.mock import patch

    from test_r111_fuzz_harness import _deterministic_getaddrinfo

    # 1. URL validation fuzz
    def url_gen():
        if random.random() < 0.2:
            return random.choice(FuzzStrategies.unsafe_strings())
        return "http://" + FuzzStrategies.random_string(
            1, 20, string.ascii_letters + ".:/"
        )

    fuzzer.fuzz_target(
        "validate_outbound_url", fuzz_url_validation, url_gen, max_runs=max_runs
    )

    # 2. Policy bundle fuzz
    def bundle_gen():
        return FuzzStrategies.random_json(depth=3)

    fuzzer.fuzz_target(
        "PolicyBundle.from_dict", fuzz_policy_bundle, bundle_gen, max_runs=max_runs
    )

    # 3. Loopback fuzz
    def ip_gen():
        if random.random() < 0.2:
            return random.choice(FuzzStrategies.unsafe_strings())
        return ".".join(str(random.randint(0, 300)) for _ in range(4))

    fuzzer.fuzz_target("is_loopback", fuzz_is_loopback, ip_gen, max_runs=max_runs)

    # 4. Path normalization
    from services.safe_io import PathTraversalError, resolve_under_root

    def path_gen():
        parts = ["foo", "..", "bar", "//", "\\", "C:", "/etc/passwd", "~", "."]
        return os.path.join(
            *[random.choice(parts) for _ in range(random.randint(1, 5))]
        )

    def fuzz_resolve(inp):
        try:
            # Use a temp dir as root
            resolve_under_root(os.path.join(artifact_dir, "_safe_root"), inp)
        except (PathTraversalError, ValueError):
            pass

    fuzzer.fuzz_target("resolve_under_root", fuzz_resolve, path_gen, max_runs=max_runs)

    return {
        "suite": "r111_fuzz",
        "seed": seed,
        "max_runs_per_target": max_runs,
        "total_crashes": len(fuzzer.crashes),
        "crash_artifacts": fuzzer.crashes,
        "passed": len(fuzzer.crashes) == 0,
    }


def run_mutation_suite(threshold: float, artifact_dir: str) -> Dict[str, Any]:
    """
    Run R113 mutation test with kill-rate threshold enforcement.

    Returns:
        Result dict with score, pass/fail, and report path.
    """
    script = os.path.join(os.path.dirname(__file__), "run_mutation_test.py")

    if not os.path.isfile(script):
        return {
            "suite": "r113_mutation",
            "passed": False,
            "error": f"Mutation test script not found: {script}",
            "score": 0.0,
            "threshold": threshold,
        }

    try:
        report_path = os.path.join(
            os.path.dirname(__file__), "..", ".planning", "mutation_report.json"
        )
        # IMPORTANT: remove stale report before each run to avoid parsing
        # previous results when the current mutation subprocess fails early.
        if os.path.isfile(report_path):
            os.remove(report_path)

        result = subprocess.run(
            [sys.executable, script],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=os.path.join(os.path.dirname(__file__), ".."),
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )

        # Parse report
        score = 0.0
        report_data: Dict[str, Any] = {}

        if os.path.isfile(report_path):
            with open(report_path, "r", encoding="utf-8") as f:
                report_data = json.load(f)
            total = report_data.get("total_mutants", 0)
            killed = report_data.get("killed", 0)
            if total > 0:
                score = (killed / total) * 100.0
        else:
            # Fallback: parse score from runner log when report is missing.
            combined = f"{result.stdout or ''}\n{result.stderr or ''}"
            m = re.search(r"Mutation Score:\s*([0-9]+(?:\.[0-9]+)?)%", combined)
            if m:
                score = float(m.group(1))

        passed = score >= threshold

        error = None
        # CRITICAL: treat mutation subprocess return code as authoritative.
        # If mutation runner exits non-zero, this suite must fail even if a stale
        # score could be parsed from logs.
        if result.returncode != 0:
            error = (
                "mutation subprocess failed "
                f"(rc={result.returncode}); "
                "see stdout_tail/stderr_tail for details"
            )
            passed = False
        elif not report_data:
            # IMPORTANT: this indicates diagnostic degradation (e.g., runner did
            # not emit report). Keep it visible in manifest for CI triage.
            error = "mutation report missing; used score fallback from process output"

        return {
            "suite": "r113_mutation",
            "score": round(score, 2),
            "threshold": threshold,
            "total_mutants": report_data.get("total_mutants", 0),
            "killed": report_data.get("killed", 0),
            "survived": report_data.get("survived", 0),
            "passed": passed,
            "report_path": report_path if os.path.isfile(report_path) else None,
            "stdout_tail": result.stdout[-500:] if result.stdout else "",
            "stderr_tail": result.stderr[-500:] if result.stderr else "",
            "returncode": result.returncode,
            "error": error,
        }

    except subprocess.TimeoutExpired:
        return {
            "suite": "r113_mutation",
            "passed": False,
            "error": "Mutation test timed out (300s)",
            "score": 0.0,
            "threshold": threshold,
        }
    except Exception as e:
        return {
            "suite": "r113_mutation",
            "passed": False,
            "error": str(e),
            "score": 0.0,
            "threshold": threshold,
        }


def build_manifest(
    profile: str,
    seed: int,
    fuzz_result: Dict[str, Any],
    mutation_result: Dict[str, Any],
    artifact_dir: str,
    elapsed_sec: float,
) -> Dict[str, Any]:
    """Build machine-readable JSON manifest for CI artifact upload."""
    overall_passed = fuzz_result["passed"] and mutation_result["passed"]

    manifest = {
        "r118_version": "1.0",
        "profile": profile,
        "seed": seed,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "elapsed_sec": round(elapsed_sec, 2),
        "decision": "PASS" if overall_passed else "FAIL",
        "suites": {
            "r111_fuzz": fuzz_result,
            "r113_mutation": mutation_result,
        },
        "artifact_dir": os.path.abspath(artifact_dir),
        "replay_command": (
            f"python scripts/run_adversarial_gate.py "
            f"--profile {profile} --seed {seed} "
            f"--artifact-dir {artifact_dir}"
        ),
    }

    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="R118 Adversarial Gate Runner")
    parser.add_argument(
        "--profile",
        choices=["smoke", "extended"],
        default="smoke",
        help="Execution profile (default: smoke)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for deterministic replay (default: random)",
    )
    parser.add_argument(
        "--artifact-dir",
        default=".tmp/adversarial",
        help="Directory for crash artifacts and manifests",
    )
    parser.add_argument(
        "--mutation-threshold",
        type=float,
        default=None,
        help="Mutation kill-rate threshold %% (overrides profile default)",
    )
    args = parser.parse_args()

    # Profile defaults
    if args.profile == "smoke":
        fuzz_max_runs = 200
        mutation_threshold = args.mutation_threshold or 20.0
    else:  # extended
        fuzz_max_runs = 2000
        mutation_threshold = args.mutation_threshold or 80.0

    seed = args.seed if args.seed is not None else random.randint(0, 2**31)
    artifact_dir = os.path.abspath(args.artifact_dir)
    os.makedirs(artifact_dir, exist_ok=True)

    print(f"R118 Adversarial Gate -- profile={args.profile}, seed={seed}")
    print(f"  Fuzz: {fuzz_max_runs} runs/target")
    print(f"  Mutation threshold: {mutation_threshold}%")
    print(f"  Artifacts: {artifact_dir}")
    print("-" * 60)

    start = time.time()

    # Run fuzz suite
    print("\n[R111] Running fuzz suite...")
    fuzz_result = run_fuzz_suite(seed, fuzz_max_runs, artifact_dir)
    fuzz_status = "PASS" if fuzz_result["passed"] else "FAIL"
    print(f"[R111] {fuzz_status} -- {fuzz_result['total_crashes']} crashes")

    # Run mutation suite
    print("\n[R113] Running mutation suite...")
    mutation_result = run_mutation_suite(mutation_threshold, artifact_dir)
    mutation_status = "PASS" if mutation_result["passed"] else "FAIL"
    print(
        f"[R113] {mutation_status} -- "
        f"score={mutation_result.get('score', 0)}% "
        f"(threshold={mutation_threshold}%)"
    )

    elapsed = time.time() - start

    # Build and write manifest
    manifest = build_manifest(
        args.profile, seed, fuzz_result, mutation_result, artifact_dir, elapsed
    )

    manifest_path = os.path.join(artifact_dir, "adversarial_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"\n{'=' * 60}")
    print(f"R118 DECISION: {manifest['decision']}")
    print(f"Manifest: {manifest_path}")
    print(f"Elapsed: {elapsed:.1f}s")

    return 0 if manifest["decision"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
