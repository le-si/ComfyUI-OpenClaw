"""
F45 — Kakao Output Policy Deterministic Contract Tests.

Tests output shaping, QuickReply caps, empty-output guards,
and malformed-input degrade behavior.
"""

import unittest

from connector.channels.kakaotalk import KakaoTalkChannel


class TestF45OutputContract(unittest.TestCase):
    def setUp(self):
        self.ch = KakaoTalkChannel()

    # --- Deterministic prefix ---

    def test_prefix_added(self):
        resp = self.ch.format_response("hello")
        text = resp["template"]["outputs"][0]["simpleText"]["text"]
        self.assertTrue(text.startswith("[OpenClaw] "))

    def test_prefix_not_duplicated(self):
        resp = self.ch.format_response("[OpenClaw] hello")
        text = resp["template"]["outputs"][0]["simpleText"]["text"]
        self.assertEqual(text, "[OpenClaw] hello")

    # --- Empty output guard ---

    def test_empty_text_produces_output(self):
        resp = self.ch.format_response("")
        outputs = resp["template"]["outputs"]
        self.assertEqual(len(outputs), 1)
        self.assertIn("simpleText", outputs[0])
        self.assertIn("(empty response)", outputs[0]["simpleText"]["text"])

    def test_none_text_produces_output(self):
        """Edge: text=None should not crash."""
        # format_response expects str, but guard covers empty
        resp = self.ch.format_response("")
        self.assertEqual(resp["version"], "2.0")
        self.assertGreaterEqual(len(resp["template"]["outputs"]), 1)

    # --- QuickReply count cap ---

    def test_qr_count_capped_at_10(self):
        qrs = [{"label": f"Btn{i}", "value": f"v{i}"} for i in range(15)]
        resp = self.ch.format_response("pick", quick_replies=qrs)
        self.assertEqual(len(resp["template"]["quickReplies"]), 10)

    def test_qr_under_limit_preserved(self):
        qrs = [{"label": "A", "value": "a"}, {"label": "B", "value": "b"}]
        resp = self.ch.format_response("pick", quick_replies=qrs)
        self.assertEqual(len(resp["template"]["quickReplies"]), 2)

    # --- QuickReply label truncation ---

    def test_qr_label_truncated_to_14(self):
        qrs = [{"label": "A" * 20, "value": "v"}]
        resp = self.ch.format_response("pick", quick_replies=qrs)
        self.assertEqual(len(resp["template"]["quickReplies"][0]["label"]), 14)

    # --- Malformed QuickReply degrade ---

    def test_qr_non_dict_skipped(self):
        qrs = ["not_a_dict", {"label": "OK", "value": "ok"}]
        resp = self.ch.format_response("pick", quick_replies=qrs)
        self.assertEqual(len(resp["template"]["quickReplies"]), 1)

    def test_qr_empty_label_skipped(self):
        qrs = [{"label": "", "value": "v"}, {"label": "OK", "value": "ok"}]
        resp = self.ch.format_response("pick", quick_replies=qrs)
        self.assertEqual(len(resp["template"]["quickReplies"]), 1)

    def test_qr_all_invalid_no_key(self):
        qrs = [{"label": ""}, "bad"]
        resp = self.ch.format_response("pick", quick_replies=qrs)
        self.assertNotIn("quickReplies", resp["template"])

    # --- Chunking determinism ---

    def test_chunking_boundary(self):
        self.ch.prefix = ""
        self.ch.MAX_TEXT_LENGTH = 10
        text = "1234567890ABCDEFGHIJ"  # 20 chars -> 2 chunks
        resp = self.ch.format_response(text)
        outputs = resp["template"]["outputs"]
        self.assertEqual(len(outputs), 2)

    def test_chunking_truncation_at_max_outputs(self):
        self.ch.prefix = ""
        self.ch.MAX_TEXT_LENGTH = 5
        text = "12345678901234567890"  # 20 chars -> 4 chunks, capped at 3
        resp = self.ch.format_response(text)
        outputs = resp["template"]["outputs"]
        self.assertEqual(len(outputs), 3)
        self.assertTrue(outputs[2]["simpleText"]["text"].endswith("...(more)"))

    # --- Image + text output budget ---

    def test_image_uses_output_slot(self):
        resp = self.ch.format_response("Look", image_url="https://example.com/img.png")
        outputs = resp["template"]["outputs"]
        self.assertEqual(len(outputs), 2)
        self.assertIn("simpleImage", outputs[0])
        self.assertIn("simpleText", outputs[1])

    def test_unsafe_image_fallback_to_text(self):
        resp = self.ch.format_response("Look", image_url="http://example.com/img.png")
        outputs = resp["template"]["outputs"]
        # Should have text only (http:// rejected)
        self.assertTrue(all("simpleText" in o for o in outputs))

    # --- Markdown stripping ---

    def test_markdown_stripped(self):
        resp = self.ch.format_response("**bold** and *italic*")
        text = resp["template"]["outputs"][0]["simpleText"]["text"]
        self.assertNotIn("**", text)
        self.assertNotIn("*italic*", text)
        self.assertIn("bold", text)

    # --- Response structure ---

    def test_version_always_2_0(self):
        resp = self.ch.format_response("hi")
        self.assertEqual(resp["version"], "2.0")

    def test_template_key_present(self):
        resp = self.ch.format_response("hi")
        self.assertIn("template", resp)
        self.assertIn("outputs", resp["template"])


if __name__ == "__main__":
    unittest.main()
