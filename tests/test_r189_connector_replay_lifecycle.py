import unittest

from services.connector_replay_lifecycle import (
    ConnectorReplayLifecycle,
    ReplayClaimCode,
    ReplayLifecycleState,
)


class TestConnectorReplayLifecycle(unittest.TestCase):
    def test_duplicate_in_flight_rejected(self):
        lifecycle = ConnectorReplayLifecycle(ttl_sec=30)

        first = lifecycle.claim("telegram:update:1", metadata={"platform": "telegram"})
        second = lifecycle.claim("telegram:update:1")

        self.assertTrue(first.accepted)
        self.assertEqual(first.code, ReplayClaimCode.CLAIMED.value)
        self.assertFalse(second.accepted)
        self.assertEqual(second.code, ReplayClaimCode.DUPLICATE_IN_FLIGHT.value)
        self.assertEqual(second.record.state, ReplayLifecycleState.CLAIMED.value)

    def test_retryable_release_allows_reclaim(self):
        lifecycle = ConnectorReplayLifecycle(ttl_sec=30)

        first = lifecycle.claim("slack:interaction:1")
        lifecycle.release_retryable("slack:interaction:1", reason="send_failed")
        second = lifecycle.claim("slack:interaction:1")

        self.assertTrue(first.accepted)
        self.assertTrue(second.accepted)
        self.assertEqual(second.code, ReplayClaimCode.RETRY_CLAIMED.value)
        self.assertEqual(second.record.claim_count, 2)
        self.assertEqual(second.record.state, ReplayLifecycleState.CLAIMED.value)

    def test_success_commit_is_terminal_for_duplicates(self):
        lifecycle = ConnectorReplayLifecycle(ttl_sec=30)

        lifecycle.claim("feishu:callback:1")
        lifecycle.commit_success("feishu:callback:1", reason="delivered")
        duplicate = lifecycle.claim("feishu:callback:1")

        self.assertFalse(duplicate.accepted)
        self.assertEqual(duplicate.code, ReplayClaimCode.DUPLICATE_AFTER_SUCCESS.value)
        self.assertEqual(duplicate.record.state, ReplayLifecycleState.DELIVERED.value)

    def test_terminal_failure_does_not_reclaim(self):
        lifecycle = ConnectorReplayLifecycle(ttl_sec=30)

        lifecycle.claim("kakao:webhook:1")
        lifecycle.fail_terminal("kakao:webhook:1", reason="invalid_policy")
        duplicate = lifecycle.claim("kakao:webhook:1")

        self.assertFalse(duplicate.accepted)
        self.assertEqual(
            duplicate.code, ReplayClaimCode.DUPLICATE_AFTER_TERMINAL_FAILURE.value
        )
        self.assertEqual(
            duplicate.record.state, ReplayLifecycleState.TERMINAL_FAILURE.value
        )

    def test_expired_claim_can_be_reclaimed(self):
        lifecycle = ConnectorReplayLifecycle(ttl_sec=5)

        lifecycle.claim("whatsapp:webhook:1", now=10.0)
        reclaimed = lifecycle.claim("whatsapp:webhook:1", now=16.0)

        self.assertTrue(reclaimed.accepted)
        self.assertEqual(reclaimed.code, ReplayClaimCode.CLAIMED.value)


if __name__ == "__main__":
    unittest.main()
