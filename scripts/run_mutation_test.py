import ast
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple, Union

# Config
TARGET_FILES = ["services/access_control.py"]
# IMPORTANT: keep this on the repo unittest runner for .venv/CI parity.
TEST_COMMAND = [
    sys.executable,
    "scripts/run_unittests.py",
    "--start-dir",
    "tests",
    "--pattern",
    "test_access_control.py",
]

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("mutation_test")


class MutationVisitor(ast.NodeTransformer):
    def __init__(self):
        self.mutations = []
        self.current_mutation_index = -1
        self.mutation_counter = 0
        self.applied_desc = None

    def visit_Compare(self, node):
        # Mutate '==' to '!=' and vice versa
        if len(node.ops) == 1:
            op = node.ops[0]
            if isinstance(op, ast.Eq):
                self._maybe_mutate(
                    node, lambda n: [setattr(n, "ops", [ast.NotEq()])], "Eq -> NotEq"
                )
            elif isinstance(op, ast.NotEq):
                self._maybe_mutate(
                    node, lambda n: [setattr(n, "ops", [ast.Eq()])], "NotEq -> Eq"
                )
        return self.generic_visit(node)

    def visit_BoolOp(self, node):
        # Mutate 'and' to 'or' and vice versa
        op = node.op
        if isinstance(op, ast.And):
            self._maybe_mutate(node, lambda n: setattr(n, "op", ast.Or()), "And -> Or")
        elif isinstance(op, ast.Or):
            self._maybe_mutate(node, lambda n: setattr(n, "op", ast.And()), "Or -> And")
        return self.generic_visit(node)

    def _maybe_mutate(self, node, action, desc):
        if self.current_mutation_index == self.mutation_counter:
            logger.debug(f"Applying mutation {self.mutation_counter}: {desc}")
            action(node)
            self.applied_desc = desc
        self.mutation_counter += 1


def count_mutations(file_path: str) -> int:
    with open(file_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())

    visitor = MutationVisitor()
    visitor.visit(tree)
    return visitor.mutation_counter


def apply_mutation(file_path: str, mutation_index: int) -> str:
    with open(file_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())

    visitor = MutationVisitor()
    visitor.current_mutation_index = mutation_index
    visitor.visit(tree)

    # Write back
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(ast.unparse(tree))

    return getattr(visitor, "applied_desc", "Unknown")


def run_test_suite() -> bool:
    try:
        result = subprocess.run(
            TEST_COMMAND, capture_output=True, text=True, timeout=30
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except Exception as e:
        logger.error(f"Test run failed: {e}")
        return False


def run_mutation_workflow():
    report = {"total_mutants": 0, "killed": 0, "survived": 0, "details": []}

    for target in TARGET_FILES:
        target_path = os.path.abspath(target)
        backup_path = target_path + ".bak"

        if not os.path.exists(target_path):
            logger.error(f"Target file not found: {target}")
            continue

        logger.info(f"Targeting {target}...")

        # 1. Check baseline
        # shutil.copy2(target_path, backup_path) # Backup first just in case
        # But wait, run_test_suite runs on current state.

        logger.info("Running baseline tests...")
        if not run_test_suite():
            logger.error("Baseline tests failed! Cannot proceed with mutation testing.")
            return

        # 2. Count mutations
        num_mutations = count_mutations(target_path)
        logger.info(f"Found {num_mutations} mutation points in {target}")
        report["total_mutants"] += num_mutations

        # 3. Apply mutations one by one
        try:
            shutil.copy2(target_path, backup_path)

            for i in range(num_mutations):
                # Restore clean
                shutil.copy2(backup_path, target_path)

                # Apply mutation
                desc = apply_mutation(target_path, i)
                logger.info(f"Mutant {i+1}/{num_mutations}: {desc}")

                # Run tests
                passed = run_test_suite()

                if not passed:
                    logger.info("-> KILLED")
                    report["killed"] += 1
                else:
                    logger.warning("-> SURVIVED")
                    report["survived"] += 1
                    report["details"].append(
                        {
                            "file": target,
                            "mutation_index": i,
                            "description": desc,
                            "status": "SURVIVED",
                        }
                    )
        finally:
            # Restore original
            if os.path.exists(backup_path):
                shutil.copy2(backup_path, target_path)
                os.remove(backup_path)
                logger.info("Restored original file.")

    # Generate Report
    score = 0
    if report["total_mutants"] > 0:
        score = (report["killed"] / report["total_mutants"]) * 100

    logger.info("=" * 40)
    logger.info(f"Mutation Score: {score:.2f}%")
    logger.info(
        f"Total: {report['total_mutants']}, Killed: {report['killed']}, Survived: {report['survived']}"
    )
    logger.info("=" * 40)

    report_file = os.path.join(".planning", "mutation_report.json")
    with open(report_file, "w") as f:
        json.dump(report, f, indent=2)
    logger.info(f"Report saved to {report_file}")


if __name__ == "__main__":
    run_mutation_workflow()
