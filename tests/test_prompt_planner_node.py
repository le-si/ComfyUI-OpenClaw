import os
import sys
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())

from nodes.prompt_planner import OpenClawPromptPlanner


class _Profile:
    def __init__(self, profile_id):
        self.id = profile_id


class _Registry:
    def __init__(self, ids, default_id):
        self._ids = ids
        self._default_id = default_id

    def list_profiles(self):
        return [_Profile(profile_id) for profile_id in self._ids]

    def get_default_profile_id(self):
        return self._default_id


class TestPromptPlannerNode(unittest.TestCase):
    def test_input_types_reads_registry_profiles(self):
        registry = _Registry(["Alpha", "Beta"], "Beta")
        with patch("nodes.prompt_planner.get_planner_registry", return_value=registry):
            input_types = OpenClawPromptPlanner.INPUT_TYPES()

        profile_meta = input_types["required"]["profile"]
        self.assertEqual(profile_meta[0], ["Alpha", "Beta"])
        self.assertEqual(profile_meta[1]["default"], "Beta")
