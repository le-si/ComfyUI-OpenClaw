#!/usr/bin/env python3
"""Decide whether a workflow-verified fix has closing evidence on the branch it landed on.

A fix that is verified by a CI workflow can be accepted on a topic branch, merged, and leave the
branch it landed on still displaying the pre-fix failure. GitHub shows a workflow's status from
its most recent run on that branch, so until a run happens there the repository advertises a
defect that no longer exists. On a weekly schedule that window is a week wide, and anyone who
triages the red state spends their time on a fixed bug.

The decision is separated from the fetching on purpose. `evaluate_closure` is pure, so the whole
rule is testable offline against captured payloads and the repository's own gate never needs a
network. `main` does the two lookups and prints what the rule found.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

COMPLETED = "completed"
SUCCESS = "success"


def _completed_runs(runs: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Completed runs only, newest first by their own timestamp.

    Ordering is taken from `createdAt` rather than from the caller's list order. A paged or
    re-serialized payload arrives in whatever order its producer chose, and trusting position
    would silently evaluate the wrong run.

    An in-progress run is not a verdict, so it cannot be "the latest run" for this purpose;
    otherwise a fix would appear unproven the moment someone started an unrelated run.
    """
    completed = [run for run in runs or [] if str(run.get("status", "")) == COMPLETED]
    return sorted(
        completed, key=lambda run: str(run.get("createdAt", "")), reverse=True
    )


def evaluate_closure(
    runs: Iterable[Mapping[str, Any]],
    *,
    workflow: str,
    branch: str,
    fix_commit: str,
    branch_commits: Sequence[str],
) -> list[str]:
    """Return one reason per unmet closure requirement; an empty list means closed.

    `branch_commits` is the set of commit ids at or after `fix_commit` on `branch`, supplied by
    the caller. Passing it in rather than shelling out keeps this function free of both the
    network and the working tree, which is what lets the offline gate cover the actual rule.

    All applicable reasons are returned together instead of stopping at the first, because a run
    that both failed and predates the fix is a different situation from either alone.
    """
    reasons: list[str] = []
    ordered = _completed_runs(runs)

    if not ordered:
        reasons.append(
            f"no completed run of {workflow} on {branch}: the fix has never been exercised "
            "where it shipped"
        )
        return reasons

    latest = ordered[0]
    run_id = latest.get("databaseId", "unknown")
    head = str(latest.get("headSha", ""))
    created = latest.get("createdAt", "unknown")

    if str(latest.get("conclusion", "")) != SUCCESS:
        reasons.append(
            f"latest completed run {run_id} of {workflow} on {branch} concluded "
            f"{latest.get('conclusion')!r}, not {SUCCESS!r}"
        )

    known = {str(commit) for commit in branch_commits or ()}
    if head not in known:
        reasons.append(
            f"latest completed run {run_id} of {workflow} on {branch} ran at {head or '<none>'} "
            f"({created}), which is not at or after {fix_commit}: the status shown for this "
            "workflow predates the fix and is stale rather than passing"
        )

    return reasons


def _gh_runs(workflow: str, branch: str, limit: int) -> list[Mapping[str, Any]]:
    completed = subprocess.run(
        [
            "gh",
            "run",
            "list",
            "--workflow",
            workflow,
            "--branch",
            branch,
            "--limit",
            str(limit),
            "--json",
            "databaseId,conclusion,status,headSha,createdAt",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    parsed = json.loads(completed.stdout or "[]")
    return parsed if isinstance(parsed, list) else []


def _branch_commits(fix_commit: str, branch: str) -> list[str]:
    completed = subprocess.run(
        ["git", "rev-list", f"{fix_commit}^..{branch}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workflow", required=True, help="Workflow file name, e.g. ci.yml"
    )
    parser.add_argument("--branch", default="main", help="Branch the fix landed on")
    parser.add_argument(
        "--fix-commit", required=True, help="Commit that carries the fix"
    )
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)

    try:
        runs = _gh_runs(args.workflow, args.branch, args.limit)
        commits = _branch_commits(args.fix_commit, args.branch)
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError) as exc:
        print(f"WORKFLOW-CLOSURE-ERROR: {exc}")
        return 2

    reasons = evaluate_closure(
        runs,
        workflow=args.workflow,
        branch=args.branch,
        fix_commit=args.fix_commit,
        branch_commits=commits,
    )
    if reasons:
        for reason in reasons:
            print(f"WORKFLOW-CLOSURE-FAIL: {reason}")
        return 1
    print(
        f"WORKFLOW-CLOSURE-PASS: {args.workflow} has a successful run on {args.branch} "
        f"at or after {args.fix_commit}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
