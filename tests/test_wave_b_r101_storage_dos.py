import unittest
from unittest.mock import MagicMock

from services.idempotency_store import IdempotencyStore
from services.rate_limit import RateLimiter


class TestR101StorageDoS(unittest.TestCase):
    def setUp(self):
        IdempotencyStore.reset_singleton()

    def tearDown(self):
        IdempotencyStore.reset_singleton()

    def test_cleanup_called_in_durable_mode_eventually(self):
        backend = MagicMock()
        backend.check_and_record.return_value = (False, None)

        store = IdempotencyStore()
        store.configure_durable(backend=backend, strict_mode=False)
        store.check_and_record("wave_b_key", ttl=60)

        backend.cleanup.assert_called()
        backend.check_and_record.assert_called_once()

    def test_connector_and_trigger_quotas_exist(self):
        limiter = RateLimiter()
        self.assertIn("connector", limiter.defaults)
        self.assertIn("trigger", limiter.defaults)
        self.assertEqual(limiter.defaults["connector"][0], 20)
        self.assertEqual(limiter.defaults["trigger"][0], 60)


if __name__ == "__main__":
    unittest.main()
