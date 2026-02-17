import unittest

from services.capabilities import get_capabilities


class TestF51Actions(unittest.TestCase):

    def test_action_capabilities(self):
        """Verify action capability matrix is present and correct."""
        caps = get_capabilities()
        self.assertIn("actions", caps)

        actions = caps["actions"]
        # Check specific expected actions
        self.assertIn("doctor", actions)
        self.assertIn("doctor_fix", actions)

        # Verify strict contract
        self.assertFalse(actions["doctor"]["mutating"])
        self.assertTrue(actions["doctor_fix"]["mutating"])

        # Verify disabled future hooks
        self.assertFalse(actions["install_node"]["enabled"])


if __name__ == "__main__":
    unittest.main()
