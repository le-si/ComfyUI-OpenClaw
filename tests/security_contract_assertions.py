"""
R112 shared assertions for security reject/degrade contracts.

Contract triad:
1) HTTP status
2) machine-readable response code (`code` or `error`)
3) audit emission (`action`, `outcome`, optional status/reason)
"""

from __future__ import annotations

import json
from typing import Any


def decode_json_response(response: Any) -> dict:
    raw = getattr(response, "text", "")
    if callable(raw):
        raw = raw()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def assert_security_reject_contract(
    testcase: Any,
    *,
    response: Any,
    expected_status: int,
    expected_code: str,
    audit_mock: Any,
    expected_action: str,
    expected_outcome: str,
    expected_audit_status: int | None = None,
    expected_reason: str | None = None,
) -> dict:
    testcase.assertEqual(response.status, expected_status)
    body = decode_json_response(response)
    code = body.get("code") or body.get("error")
    testcase.assertEqual(code, expected_code)

    testcase.assertTrue(
        getattr(audit_mock, "called", False),
        "Expected audit event, but emit_audit_event was not called.",
    )

    matched = False
    for call in audit_mock.call_args_list:
        kwargs = call.kwargs or {}
        if kwargs.get("action") != expected_action:
            continue
        if kwargs.get("outcome") != expected_outcome:
            continue
        if expected_audit_status is not None:
            testcase.assertEqual(kwargs.get("status_code"), expected_audit_status)
        if expected_reason is not None:
            details = kwargs.get("details") or {}
            testcase.assertEqual(details.get("reason"), expected_reason)
        matched = True
        break

    testcase.assertTrue(
        matched,
        (
            "Expected audit contract not found: "
            f"action={expected_action}, outcome={expected_outcome}"
        ),
    )
    return body
