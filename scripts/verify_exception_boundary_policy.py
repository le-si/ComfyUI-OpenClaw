"""Verify selected broad-exception boundary policy.

This intentionally checks only modules listed in tests/exception_boundary_policy.json.
The repo still has too many historical broad catches for a global BLE001-style
rule to be useful.
"""

from __future__ import annotations

import argparse
import ast
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

VALID_CLASSIFICATIONS = {
    "allowed_boundary_guard",
    "needs_narrowing",
    "needs_follow_up_test_coverage",
}


@dataclass(frozen=True)
class BroadCatch:
    path: str
    line: int
    scope: str
    catch_type: str


class _BroadCatchVisitor(ast.NodeVisitor):
    def __init__(self, path: Path):
        self.path = path.as_posix()
        self.scope_stack: list[str] = []
        self.catches: list[BroadCatch] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope_stack.append(node.name)
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope_stack.append(node.name)
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        catch_type = _catch_type_name(node.type)
        if catch_type in {"bare", "Exception", "BaseException"}:
            self.catches.append(
                BroadCatch(
                    path=self.path,
                    line=node.lineno,
                    scope=".".join(self.scope_stack) or "<module>",
                    catch_type=catch_type,
                )
            )
        self.generic_visit(node)


def _catch_type_name(node: ast.expr | None) -> str:
    if node is None:
        return "bare"
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Tuple):
        names = {_catch_type_name(item) for item in node.elts}
        if "BaseException" in names:
            return "BaseException"
        if "Exception" in names:
            return "Exception"
    return ""


def iter_broad_catches(path: Path) -> Iterable[BroadCatch]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    visitor = _BroadCatchVisitor(path)
    visitor.visit(tree)
    return tuple(visitor.catches)


def load_policy(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_exception_boundary_policy(
    repo_root: Path,
    policy: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    modules = policy.get("selected_modules")
    if not isinstance(modules, dict) or not modules:
        return ["policy selected_modules must be a non-empty object"]

    for rel_path, module_policy in sorted(modules.items()):
        path = repo_root / rel_path
        if not path.is_file():
            failures.append(f"{rel_path}: selected module does not exist")
            continue

        allowed = module_policy.get("broad_catches")
        if not isinstance(allowed, list):
            failures.append(f"{rel_path}: broad_catches must be a list")
            continue

        entries_by_scope: dict[str, dict[str, Any]] = {}
        for index, entry in enumerate(allowed):
            if not isinstance(entry, dict):
                failures.append(f"{rel_path}: broad_catches[{index}] must be an object")
                continue
            scope = entry.get("scope")
            classification = entry.get("classification")
            reason = entry.get("reason")
            if not isinstance(scope, str) or not scope:
                failures.append(f"{rel_path}: broad_catches[{index}] missing scope")
                continue
            if scope in entries_by_scope:
                failures.append(f"{rel_path}: duplicate broad-catch scope {scope}")
            entries_by_scope[scope] = entry
            if classification not in VALID_CLASSIFICATIONS:
                failures.append(
                    f"{rel_path}:{scope}: invalid classification {classification!r}"
                )
            if not isinstance(reason, str) or not reason.strip():
                failures.append(f"{rel_path}:{scope}: missing reason")

        catches = tuple(iter_broad_catches(path))
        counts = Counter(catch.scope for catch in catches)
        for catch in catches:
            if catch.scope not in entries_by_scope:
                failures.append(
                    f"{rel_path}:{catch.line}: undocumented broad catch in {catch.scope}"
                )

        for scope, entry in entries_by_scope.items():
            expected_count = entry.get("expected_count", 1)
            if not isinstance(expected_count, int) or expected_count < 1:
                failures.append(f"{rel_path}:{scope}: expected_count must be >= 1")
                continue
            actual_count = counts.get(scope, 0)
            if actual_count != expected_count:
                failures.append(
                    f"{rel_path}:{scope}: expected {expected_count} broad catch(es), found {actual_count}"
                )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument(
        "--policy",
        default="tests/exception_boundary_policy.json",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    policy_path = repo_root / args.policy
    failures = validate_exception_boundary_policy(repo_root, load_policy(policy_path))
    if failures:
        for failure in failures:
            print(f"EXCEPTION-BOUNDARY-FAIL: {failure}")
        return 1
    print("EXCEPTION-BOUNDARY-PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
