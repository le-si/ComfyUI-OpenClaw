"""
Unit tests for WP2 Service and Module Registries.
"""

import unittest

from services.modules import (
    ModuleCapability,
    ModuleRegistry,
    enable_module,
    is_module_enabled,
)
from services.registry import ServiceRegistry


class TestServiceRegistry(unittest.TestCase):

    def setUp(self):
        ServiceRegistry.reset()

    def test_register_resolve(self):
        obj = {"foo": "bar"}
        ServiceRegistry.register("test_svc", obj)
        self.assertEqual(ServiceRegistry.get("test_svc"), obj)
        self.assertTrue(ServiceRegistry.has("test_svc"))
        self.assertIsNone(ServiceRegistry.get("missing"))

    def test_reset(self):
        ServiceRegistry.register("svc1", 1)
        ServiceRegistry.reset()
        self.assertFalse(ServiceRegistry.has("svc1"))


class TestModuleRegistry(unittest.TestCase):

    def setUp(self):
        ModuleRegistry.reset()

    def test_enable_check(self):
        self.assertFalse(is_module_enabled(ModuleCapability.CONNECTOR))
        enable_module(ModuleCapability.CONNECTOR)
        self.assertTrue(is_module_enabled(ModuleCapability.CONNECTOR))
        self.assertIn("connector", ModuleRegistry.get_enabled_list())

    def test_lock(self):
        enable_module(ModuleCapability.CORE)
        ModuleRegistry.lock()
        enable_module(ModuleCapability.BRIDGE)  # Should be ignored

        self.assertTrue(is_module_enabled(ModuleCapability.CORE))
        self.assertFalse(is_module_enabled(ModuleCapability.BRIDGE))


if __name__ == "__main__":
    unittest.main()
