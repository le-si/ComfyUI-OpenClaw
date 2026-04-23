import unittest
from unittest.mock import MagicMock, patch

import services.preflight
from nodes.batch_variants import OpenClawBatchVariants
from nodes.image_to_prompt import OpenClawImageToPrompt
from nodes.prompt_planner import OpenClawPromptPlanner
from nodes.prompt_refiner import OpenClawPromptRefiner
from services.workflow_portability import (
    analyze_workflow_portability,
    get_workflow_portability_contract,
)


class TestF47WorkflowPortability(unittest.TestCase):
    def test_contract_matches_current_node_schema(self):
        contract = get_workflow_portability_contract()
        nodes = contract["nodes"]

        self.assertEqual(
            nodes["MoltbotPromptPlanner"]["return_names"],
            list(OpenClawPromptPlanner.RETURN_NAMES),
        )
        self.assertEqual(
            nodes["MoltbotPromptRefiner"]["return_names"],
            list(OpenClawPromptRefiner.RETURN_NAMES),
        )
        self.assertEqual(
            nodes["MoltbotImageToPrompt"]["return_names"],
            list(OpenClawImageToPrompt.RETURN_NAMES),
        )
        self.assertEqual(
            nodes["MoltbotBatchVariants"]["return_names"],
            list(OpenClawBatchVariants.RETURN_NAMES),
        )

    def test_analyze_workflow_portability_is_deterministic(self):
        workflow = {
            "11": {"class_type": "MoltbotBatchVariants"},
            "2": {"class_type": "MoltbotPromptPlanner"},
            "abc": {"class_type": "KSampler"},
        }

        report = analyze_workflow_portability(workflow)

        self.assertEqual(report["contract_version"], 1)
        self.assertEqual(report["summary"]["openclaw_nodes"], 2)
        self.assertTrue(report["summary"]["portable_mode_required"])
        self.assertTrue(report["summary"]["portable_mode_supported"])
        self.assertEqual(
            [entry["node_id"] for entry in report["openclaw_nodes"]],
            ["2", "11"],
        )
        self.assertEqual(
            report["detected_class_types"],
            ["MoltbotBatchVariants", "MoltbotPromptPlanner"],
        )

    def test_preflight_attaches_openclaw_missing_node_fallback(self):
        services.preflight._CACHE.clear()

        with (
            patch.object(
                services.preflight, "nodes", MagicMock(), create=True
            ) as mock_nodes,
            patch.object(
                services.preflight, "folder_paths", MagicMock(), create=True
            ) as mock_folder_paths,
        ):
            mock_nodes.NODE_CLASS_MAPPINGS = {"KSampler": object}
            mock_folder_paths.folder_names_and_paths = {}
            mock_folder_paths.get_filename_list.return_value = []

            workflow = {
                "1": {"class_type": "MoltbotPromptPlanner", "inputs": {}},
                "2": {"class_type": "KSampler", "inputs": {}},
            }

            report = services.preflight.run_preflight_check(workflow)

        self.assertFalse(report["ok"])
        self.assertEqual(report["summary"]["missing_nodes"], 1)
        self.assertEqual(
            report["missing_nodes"][0]["fallback"]["portable_mode"],
            "materialize_standard_fields",
        )
        self.assertIn(
            "positive",
            report["missing_nodes"][0]["fallback"]["standard_field_targets"],
        )
        self.assertTrue(report["portability"]["summary"]["portable_mode_required"])
        self.assertIn(
            "Portable mode guidance is available for missing OpenClaw nodes.",
            report["notes"],
        )


if __name__ == "__main__":
    unittest.main()
