"""
Tests for R71 Job Event Stream (SSE).
"""

import unittest

from services.job_events import (
    JobEvent,
    JobEventStore,
    JobEventType,
    get_job_event_store,
)


class TestJobEventStore(unittest.TestCase):
    def setUp(self):
        self.store = JobEventStore(max_size=5)

    def test_emit_and_retrieve(self):
        store = self.store

        # Emit a few events
        store.emit(JobEventType.QUEUED, "p1", "t1")
        store.emit(JobEventType.RUNNING, "p1", "t1")
        last_evt = store.emit(JobEventType.COMPLETED, "p1", "t1")

        # Verify sequence
        self.assertEqual(last_evt.seq, 3)
        self.assertEqual(store.latest_seq(), 3)
        self.assertEqual(store.size, 3)

        # Retrieve all
        events = store.events_since(0)
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0].event_type, "queued")
        self.assertEqual(events[2].event_type, "completed")

    def test_buffer_rotation(self):
        store = self.store

        # Emit 6 events (capacity 5)
        for i in range(1, 7):
            store.emit(JobEventType.QUEUED, f"p{i}")

        # Should drop the first one (seq 1)
        self.assertEqual(store.size, 5)
        self.assertEqual(store.latest_seq(), 6)

        events = store.events_since(0)
        self.assertEqual(len(events), 5)
        self.assertEqual(events[0].seq, 2)  # First event is now seq=2
        self.assertEqual(events[-1].seq, 6)

    def test_events_since_filter(self):
        store = self.store
        store.emit(JobEventType.QUEUED, "p1")
        store.emit(JobEventType.QUEUED, "p2")
        store.emit(JobEventType.QUEUED, "p1")

        # Filter by prompt_id
        p1_events = store.events_since(0, prompt_id="p1")
        self.assertEqual(len(p1_events), 2)
        self.assertEqual(p1_events[0].prompt_id, "p1")
        self.assertEqual(p1_events[1].prompt_id, "p1")

    def test_sse_format(self):
        evt = JobEvent(
            seq=123,
            event_type="test",
            prompt_id="p1",
            trace_id="t1",
            timestamp=1000.0,
            data={"foo": "bar"},
        )
        sse = evt.to_sse()
        expected_lines = [
            "id: 123",
            "event: test",
            'data: {"event_type":"test","prompt_id":"p1","trace_id":"t1","timestamp":1000.0,"data":{"foo":"bar"}}',
            "",
            "",
        ]
        self.assertEqual(sse, "\n".join(expected_lines))


if __name__ == "__main__":
    unittest.main()
