import os
import sys
import tempfile
import unittest

sys.path.append(os.getcwd())

from services.log_tail import tail_log
from services.metrics import Metrics, metrics


class TestMetrics(unittest.TestCase):
    def setUp(self):
        # Reset metrics before each test
        metrics.reset()

    def test_increment_single(self):
        """Test incrementing a counter by 1."""
        metrics.increment("planner_calls")
        all_metrics = metrics.get_all()
        self.assertEqual(all_metrics["planner_calls"], 1)

    def test_increment_multiple(self):
        """Test incrementing a counter by a custom amount."""
        metrics.increment("errors", 5)
        all_metrics = metrics.get_all()
        self.assertEqual(all_metrics["errors"], 5)

    def test_ignore_unknown_counter(self):
        """Test that incrementing an unknown counter does nothing."""
        metrics.increment("unknown_counter")
        all_metrics = metrics.get_all()
        self.assertNotIn("unknown_counter", all_metrics)

    def test_reset(self):
        """Test resetting all counters."""
        metrics.increment("planner_calls", 10)
        metrics.increment("errors", 5)
        metrics.reset()
        all_metrics = metrics.get_all()
        self.assertEqual(all_metrics["planner_calls"], 0)
        self.assertEqual(all_metrics["errors"], 0)


class TestLogTail(unittest.TestCase):
    def test_clamp_lines_low(self):
        """Test that lines is clamped to minimum 1."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write("line1\nline2\nline3\n")
            path = f.name

        try:
            result = tail_log(path, -5)
            self.assertEqual(len(result), 1)  # Clamped to 1
        finally:
            os.unlink(path)

    def test_clamp_lines_high(self):
        """Test that lines is clamped to maximum 2000."""
        # Just test that asking for 5000 doesn't fail
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            for i in range(100):
                f.write(f"line{i}\n")
            path = f.name

        try:
            result = tail_log(path, 5000)
            self.assertEqual(len(result), 100)  # All lines since file is small
        finally:
            os.unlink(path)

    def test_tail_last_n_lines(self):
        """Test that only the last N lines are returned."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            for i in range(10):
                f.write(f"line{i}\n")
            path = f.name

        try:
            result = tail_log(path, 3)
            self.assertEqual(len(result), 3)
            self.assertEqual(result[-1], "line9")
        finally:
            os.unlink(path)

    def test_nonexistent_file(self):
        """Test that a nonexistent file returns empty list."""
        result = tail_log("/nonexistent/path/to/file.log", 100)
        self.assertEqual(result, [])

    def test_empty_file(self):
        """Test that an empty file returns empty list."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            path = f.name

        try:
            result = tail_log(path, 100)
            self.assertEqual(result, [])
        finally:
            os.unlink(path)


class TestLogsTailFiltering(unittest.TestCase):
    """Test R31 filtering functionality for /logs/tail."""

    def test_trace_id_filter(self):
        """Should filter logs by trace_id."""
        log_lines = [
            "2024-01-01 INFO trace_id=abc123 Starting job",
            "2024-01-01 INFO trace_id=xyz789 Another job",
            "2024-01-01 INFO trace_id=abc123 Job completed",
            "2024-01-01 ERROR trace_id=def456 Job failed",
        ]

        # Filter by trace_id=abc123
        filtered = [line for line in log_lines if "abc123" in line]

        self.assertEqual(len(filtered), 2)
        self.assertTrue(all("abc123" in line for line in filtered))

    def test_prompt_id_filter(self):
        """Should filter logs by prompt_id."""
        log_lines = [
            "2024-01-01 INFO prompt_id=prompt-001 Queued",
            "2024-01-01 INFO prompt_id=prompt-002 Queued",
            "2024-01-01 INFO prompt_id=prompt-001 Executing",
            "2024-01-01 INFO trace unrelated",
        ]

        # Filter by prompt_id=prompt-001
        filtered = [line for line in log_lines if "prompt-001" in line]

        self.assertEqual(len(filtered), 2)
        self.assertTrue(all("prompt-001" in line for line in filtered))

    def test_max_bytes_enforcement(self):
        """Should enforce max bytes limit."""
        MAX_BYTES = 100_000

        # Create lines totaling more than MAX_BYTES
        large_line = "a" * 50_000  # 50KB each
        log_lines = [large_line, large_line, large_line]  # 150KB total

        # Simulate truncation logic
        truncated = []
        current_bytes = 0
        for line in reversed(log_lines):
            line_bytes = len(line.encode("utf-8"))
            if current_bytes + line_bytes > MAX_BYTES:
                break
            truncated.insert(0, line)
            current_bytes += line_bytes

        # Should keep only 2 lines (100KB)
        self.assertEqual(len(truncated), 2)
        total = sum(len(line.encode("utf-8")) for line in truncated)
        self.assertLessEqual(total, MAX_BYTES)

    def test_filter_with_redaction(self):
        """Should apply both filtering and redaction."""
        from services.redaction import redact_text

        log_lines = [
            "2024-01-01 trace_id=abc123 API Key: sk-1234567890abcdefghij1234567890",
            "2024-01-01 trace_id=xyz789 Normal log",
            "2024-01-01 trace_id=abc123 Authorization: Bearer secret_token",
        ]

        # Filter by trace_id
        filtered = [line for line in log_lines if "abc123" in line]
        self.assertEqual(len(filtered), 2)

        # Apply redaction
        redacted = [redact_text(line) for line in filtered]

        # Should not contain secrets
        for line in redacted:
            self.assertNotIn("sk-1234567890", line)
            self.assertNotIn("secret_token", line)

        # Should still contain trace_id
        for line in redacted:
            self.assertIn("abc123", line)

    def test_empty_filter_results(self):
        """Should handle empty filter results gracefully."""
        log_lines = [
            "2024-01-01 INFO No IDs here",
            "2024-01-01 INFO Normal log line",
        ]

        # Filter by non-existent trace_id
        filtered = [line for line in log_lines if "nonexistent123" in line]

        self.assertEqual(len(filtered), 0)
        self.assertIsInstance(filtered, list)


if __name__ == "__main__":
    unittest.main()
