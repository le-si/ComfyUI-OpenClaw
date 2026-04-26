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
    def _frontend_subgraph_workflow(self, *, container_mode=0):
        return {
            "nodes": [
                {
                    "id": 5,
                    "type": "subgraph-def-a",
                    "mode": container_mode,
                }
            ],
            "definitions": {
                "subgraphs": [
                    {
                        "id": "subgraph-def-a",
                        "name": "OpenClaw portability subgraph",
                        "nodes": [
                            {"id": 7, "type": "MoltbotPromptPlanner", "inputs": {}},
                            {
                                "id": 8,
                                "type": "MissingCustomNode",
                                "inputs": {"ckpt_name": "missing-model.safetensors"},
                            },
                        ],
                    }
                ]
            },
        }

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

    def test_frontend_workflow_active_subgraph_reports_openclaw_nodes(self):
        report = analyze_workflow_portability(
            self._frontend_subgraph_workflow(container_mode=0)
        )

        self.assertEqual(report["summary"]["openclaw_nodes"], 1)
        self.assertEqual(report["summary"]["suppressed_openclaw_nodes"], 0)
        self.assertEqual(report["openclaw_nodes"][0]["node_id"], "5:7")
        self.assertEqual(
            report["detected_class_types"],
            ["MoltbotPromptPlanner"],
        )

    def test_frontend_workflow_muted_subgraph_suppresses_openclaw_nodes(self):
        report = analyze_workflow_portability(
            self._frontend_subgraph_workflow(container_mode=2)
        )

        self.assertEqual(report["summary"]["openclaw_nodes"], 0)
        self.assertFalse(report["summary"]["portable_mode_required"])
        self.assertEqual(report["summary"]["suppressed_openclaw_nodes"], 1)
        self.assertEqual(report["suppressed_openclaw_nodes"][0]["node_id"], "5:7")
        self.assertEqual(
            report["suppressed_openclaw_nodes"][0]["inactive_reason"],
            "ancestor_inactive",
        )

    def test_api_prompt_muted_root_node_suppresses_openclaw_nodes(self):
        workflow = {
            "1": {"class_type": "MoltbotPromptPlanner", "mode": 2, "inputs": {}},
            "2": {"class_type": "KSampler", "inputs": {}},
        }

        report = analyze_workflow_portability(workflow)

        self.assertEqual(report["summary"]["openclaw_nodes"], 0)
        self.assertEqual(report["summary"]["suppressed_openclaw_nodes"], 1)
        self.assertEqual(report["suppressed_openclaw_nodes"][0]["node_id"], "1")
        self.assertEqual(
            report["suppressed_openclaw_nodes"][0]["inactive_reason"],
            "self_inactive",
        )

    def test_frontend_workflow_bypassed_subgraph_suppresses_preflight_findings(self):
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

            report = services.preflight.run_preflight_check(
                self._frontend_subgraph_workflow(container_mode=4)
            )

        self.assertTrue(report["ok"])
        self.assertEqual(report["summary"]["missing_nodes"], 0)
        self.assertEqual(report["summary"]["missing_models"], 0)
        self.assertEqual(report["summary"]["suppressed_missing_nodes"], 2)
        self.assertEqual(report["summary"]["suppressed_missing_models"], 1)
        self.assertEqual(
            [item["node_id"] for item in report["suppressed_missing_nodes"]],
            ["5:7", "5:8"],
        )
        self.assertIn(
            "Inactive subgraph branches were suppressed from actionable diagnostics.",
            report["notes"],
        )

    def test_api_prompt_bypassed_root_node_suppresses_preflight_findings(self):
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

            report = services.preflight.run_preflight_check(
                {
                    "1": {
                        "class_type": "MoltbotPromptPlanner",
                        "mode": 4,
                        "inputs": {"ckpt_name": "missing-model.safetensors"},
                    }
                }
            )

        self.assertTrue(report["ok"])
        self.assertEqual(report["summary"]["missing_nodes"], 0)
        self.assertEqual(report["summary"]["missing_models"], 0)
        self.assertEqual(report["summary"]["suppressed_missing_nodes"], 1)
        self.assertEqual(report["summary"]["suppressed_missing_models"], 1)
        self.assertEqual(report["suppressed_missing_nodes"][0]["node_id"], "1")
        self.assertEqual(
            report["suppressed_missing_nodes"][0]["inactive_reason"],
            "self_inactive",
        )

    def test_api_prompt_without_subgraph_metadata_remains_supported(self):
        workflow = {
            "11": {"class_type": "MoltbotBatchVariants"},
            "2": {"class_type": "MoltbotPromptPlanner"},
        }

        report = analyze_workflow_portability(workflow)

        self.assertEqual(report["summary"]["openclaw_nodes"], 2)
        self.assertEqual(report["summary"]["suppressed_openclaw_nodes"], 0)
        self.assertEqual(
            [entry["node_id"] for entry in report["openclaw_nodes"]], ["2", "11"]
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
