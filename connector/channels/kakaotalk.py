"""
KakaoTalk Channel Adapter (F45).

Implements deterministic output policy for Kakao i Open Builder:
1.  **Text Formatting**:
    -   Prefix policy (e.g., "[OpenClaw] ").
    -   Length limit enforcement (1000 chars for SimpleText).
    -   Markdown stripping (Kakao doesn't support MD).
2.  **Rich Media**:
    -   SimpleImage / BasicCard support.
    -   Bounded fallback: if media fails or exceeds limits, degrades to text.
3.  **Chunking**:
    -   Splits long messages into multiple bubbles if supported, or truncates with indicator.
"""

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class KakaoTalkChannel:
    """
    Handles message formatting and delivery payload construction for KakaoTalk.
    """

    # Kakao constants
    MAX_TEXT_LENGTH = 1000
    MAX_OUTPUTS = 3  # Max bubbles per response (SkillResponse restriction)

    def __init__(self, config: Optional[Any] = None):
        self.config = config
        # Policy defaults
        self.prefix = "[OpenClaw] "
        self.strip_markdown = True

    def format_response(
        self,
        text: str,
        image_url: Optional[str] = None,
        quick_replies: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """
        Constructs a SkillResponse v2.0 payload.
        Applies F45 policy: prefixing, sanitization, capping.
        Supports QuickReplies (F44).
        """
        outputs = []

        # 1. Image (SimpleImage) - strict validation
        if image_url:
            if self._is_safe_url(image_url):
                outputs.append(
                    {
                        "simpleImage": {
                            "imageUrl": image_url,
                            "altText": text[:100] if text else "Image",
                        }
                    }
                )
            else:
                logger.warning(f"Skipping unsafe/invalid image URL: {image_url}")
                # Fallback: append URL to text if reasonable
                if text:
                    text += f"\n(Image: {image_url})"
                else:
                    text = f"(Image: {image_url})"

        # 2. Text (SimpleText)
        if text:
            sanitized_text = self._sanitize_text(text)
            chunks = self._chunk_text(sanitized_text)

            # Add text chunks up to remaining output slots
            remaining_slots = self.MAX_OUTPUTS - len(outputs)
            for chunk in chunks[:remaining_slots]:
                outputs.append({"simpleText": {"text": chunk}})

            if len(chunks) > remaining_slots:
                # If we have more text than slots, append a truncation indicator to the last slot
                last_output = outputs[-1]
                if "simpleText" in last_output:
                    last_output["simpleText"]["text"] += "\n...(more)"

        response = {"version": "2.0", "template": {"outputs": outputs}}

        # 3. QuickReplies (defensive: skip malformed entries)
        if quick_replies:
            qr_payloads = []
            for qr in quick_replies:
                if not isinstance(qr, dict):
                    logger.warning(f"Skipping non-dict quick_reply entry: {type(qr)}")
                    continue
                label = str(qr.get("label", "")).strip()
                if not label:
                    logger.warning(f"Skipping quick_reply with empty label: {qr}")
                    continue
                value = str(qr.get("value", label)).strip() or label
                qr_payloads.append(
                    {
                        "label": label[:14],  # Kakao label limit: 14 chars
                        "action": "message",
                        "messageText": value,
                    }
                )
            if qr_payloads:
                response["template"]["quickReplies"] = qr_payloads

        return response

    def _sanitize_text(self, text: str) -> str:
        """
        Applies prefix and Markdown stripping.
        """
        if self.strip_markdown:
            # Basic MD stripping: **bold**, *italic*, `code`, [link](url)
            text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
            text = re.sub(r"\*(.*?)\*", r"\1", text)
            text = re.sub(r"`(.*?)`", r"\1", text)
            text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)

        # Ensure prefix
        if not text.startswith(self.prefix):
            return f"{self.prefix}{text}"
        return text

    def _chunk_text(self, text: str) -> List[str]:
        """
        Splits text into chunks respecting MAX_TEXT_LENGTH.
        """
        chunks = []
        while text:
            if len(text) <= self.MAX_TEXT_LENGTH:
                chunks.append(text)
                break

            # Find split point
            split_at = self.MAX_TEXT_LENGTH
            # Try to split at newline or space
            last_newline = text.rfind("\n", 0, split_at)
            if last_newline != -1:
                split_at = last_newline + 1
            else:
                last_space = text.rfind(" ", 0, split_at)
                if last_space != -1:
                    split_at = last_space + 1

            chunks.append(text[:split_at])
            text = text[split_at:]

        return chunks

    def _is_safe_url(self, url: str) -> bool:
        """
        URL safety check for Kakao SimpleImage.

        - HTTPS only (Kakao requires secure URLs for images).
        - Rejects internal/private IPs (RFC 1918, loopback, link-local).
        """
        import ipaddress
        from urllib.parse import urlparse

        try:
            parsed = urlparse(url)
        except Exception:
            return False

        # Scheme: HTTPS only
        if parsed.scheme != "https":
            return False

        hostname = parsed.hostname or ""
        if not hostname:
            return False

        # Check if hostname is a raw IP and reject private/reserved ranges
        try:
            addr = ipaddress.ip_address(hostname)
            if (
                addr.is_private
                or addr.is_loopback
                or addr.is_link_local
                or addr.is_reserved
            ):
                return False
        except ValueError:
            # Not a raw IP — hostname is a domain name, allow it
            pass

        return True
