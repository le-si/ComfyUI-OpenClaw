import json
import os
import sys
import unittest

# Ensure we can import the module from current directory
sys.path.append(os.getcwd())

from models.schemas import GenerationParams
from nodes.batch_variants import MoltbotBatchVariants


class TestBatchVariants(unittest.TestCase):
    def setUp(self):
        self.node = MoltbotBatchVariants()
        self.default_args = {
            "positive": "A cute cat",
            "negative": "ugly",
            "count": 4,
            "seed_base": 100,
            "seed_policy": "increment",
            "variant_policy": "none",
            "params_json": "{}",
            "sweep_start": 0.0,
            "sweep_end": 0.0,
        }

    def test_seed_increment(self):
        """Test simple seed incrementation."""
        pos, neg, params = self.node.generate_variants(**self.default_args)

        self.assertEqual(len(pos), 4)
        self.assertEqual(len(params), 4)

        seeds = [json.loads(p)["seed"] for p in params]
        self.assertEqual(seeds, [100, 101, 102, 103])

    def test_sweep_cfg(self):
        """Test CFG sweep from 7.0 to 10.0."""
        args = self.default_args.copy()
        args["variant_policy"] = "cfg_sweep"
        args["sweep_start"] = 7.0
        args["sweep_end"] = 10.0
        args["count"] = 4

        _, _, params = self.node.generate_variants(**args)
        cfgs = [json.loads(p)["cfg"] for p in params]

        # Expected: 7.0, 8.0, 9.0, 10.0
        self.assertEqual(cfgs[0], 7.0)
        self.assertEqual(cfgs[-1], 10.0)
        # Check midpoint roughly
        self.assertAlmostEqual(cfgs[1], 8.0)
        self.assertAlmostEqual(cfgs[2], 9.0)

    def test_sweep_steps(self):
        """Test Steps sweep."""
        args = self.default_args.copy()
        args["variant_policy"] = "steps_sweep"
        args["sweep_start"] = 20.0
        args["sweep_end"] = 28.0
        args["count"] = 3

        _, _, params = self.node.generate_variants(**args)
        steps = [json.loads(p)["steps"] for p in params]

        # 0 -> 20, 1 -> 24, 2 -> 28
        self.assertEqual(steps, [20, 24, 28])

    def test_sweep_size(self):
        """Test Size sweep with clamping and rounding."""
        args = self.default_args.copy()
        args["variant_policy"] = "size_sweep"
        args["sweep_start"] = 512.0
        args["sweep_end"] = 1000.0  # Should be rounded to nearest 8
        args["count"] = 2

        _, _, params = self.node.generate_variants(**args)
        p1 = json.loads(params[0])
        p2 = json.loads(params[1])

        self.assertEqual(p1["width"], 512)
        self.assertEqual(p1["height"], 512)

        # 1000 -> rounded to nearest 8 is 1000 // 8 * 8 = 125 * 8 = 1000. Wait, 1000/8=125.
        # Let's try 1023 (should be 1016)
        args["sweep_end"] = 1023.0
        _, _, params = self.node.generate_variants(**args)
        p2 = json.loads(params[1])
        self.assertEqual(p2["width"], 1016)

    def test_seed_randomized_determinism(self):
        """Test that randomized seed policy is deterministic given same inputs."""
        args = self.default_args.copy()
        args["seed_policy"] = "randomized"
        args["seed_base"] = 12345

        # Run 1
        _, _, params1 = self.node.generate_variants(**args)
        seeds1 = [json.loads(p)["seed"] for p in params1]

        # Run 2
        _, _, params2 = self.node.generate_variants(**args)
        seeds2 = [json.loads(p)["seed"] for p in params2]

        # Should be identical
        self.assertEqual(seeds1, seeds2)

        # Should differ from increment
        # 12345, 12346, 12347...
        self.assertNotEqual(seeds1, [12345, 12346, 12347, 12348])

    def test_invalid_json_fallback(self):
        """Test that invalid JSON doesn't crash but results in defaults."""
        args = self.default_args.copy()
        args["params_json"] = "{INVALID"

        _, _, params = self.node.generate_variants(**args)
        # Should rely on schema defaults (cfg=7.0, steps=20)
        p = json.loads(params[0])
        self.assertEqual(p["cfg"], 7.0)


if __name__ == "__main__":
    unittest.main()
