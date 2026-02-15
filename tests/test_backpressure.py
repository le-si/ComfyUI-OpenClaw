import time
import unittest

from services.observability.backpressure import BoundedQueue


class TestBoundedQueue(unittest.TestCase):

    def test_capacity_and_drops(self):
        """Test strict capacity enforcement and drop counting."""
        q = BoundedQueue[int](capacity=2)

        # 1. Fill to capacity
        self.assertTrue(q.enqueue(1))
        self.assertTrue(q.enqueue(2))

        stats = q.stats()
        self.assertEqual(stats.current_size, 2)
        self.assertEqual(stats.high_watermark, 2)
        self.assertEqual(stats.total_dropped, 0)

        # 2. Overflow (Drop Oldest)
        self.assertFalse(q.enqueue(3))

        stats = q.stats()
        self.assertEqual(stats.current_size, 2)
        self.assertEqual(stats.total_dropped, 1)
        self.assertGreater(stats.last_drop_ts, 0)

        # Check content (1 should be dropped, 2 and 3 remain)
        items = q.get_all()
        self.assertEqual(items, [2, 3])

    def test_stats_tracking(self):
        """Test cumulative stats logic."""
        q = BoundedQueue[str](capacity=5)

        q.enqueue("a")
        q.enqueue("b")
        self.assertEqual(q.stats().high_watermark, 2)

        q.get_all()  # Just reading doesn't change state

        q.clear()
        stats = q.stats()
        self.assertEqual(stats.current_size, 0)
        self.assertEqual(stats.high_watermark, 0)
        self.assertEqual(stats.total_enqueued, 0)


if __name__ == "__main__":
    unittest.main()
