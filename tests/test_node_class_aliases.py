import os
import sys
import unittest

sys.path.append(os.getcwd())

from nodes.batch_variants import MoltbotBatchVariants, OpenClawBatchVariants
from nodes.image_to_prompt import MoltbotImageToPrompt, OpenClawImageToPrompt
from nodes.prompt_planner import MoltbotPromptPlanner, OpenClawPromptPlanner
from nodes.prompt_refiner import MoltbotPromptRefiner, OpenClawPromptRefiner


class TestNodeClassAliases(unittest.TestCase):
    def test_legacy_aliases_resolve_to_openclaw_classes(self):
        self.assertIs(MoltbotPromptPlanner, OpenClawPromptPlanner)
        self.assertIs(MoltbotBatchVariants, OpenClawBatchVariants)
        self.assertIs(MoltbotImageToPrompt, OpenClawImageToPrompt)
        self.assertIs(MoltbotPromptRefiner, OpenClawPromptRefiner)


if __name__ == "__main__":
    unittest.main()
