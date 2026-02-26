"""
R95: Management query pagination + bounded-scan helpers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional


@dataclass
class PaginationMeta:
    limit: int
    offset: int = 0
    cursor: Optional[int] = None
    warnings: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "limit": self.limit,
            "offset": self.offset,
            "warnings": list(self.warnings),
        }
        if self.cursor is not None:
            payload["cursor"] = self.cursor
        return payload


@dataclass
class BoundedScanResult:
    items: List[Any]
    scanned: int
    skipped_malformed: int
    truncated: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scanned": self.scanned,
            "skipped_malformed": self.skipped_malformed,
            "truncated": self.truncated,
        }


def _warn(code: str, field: str, raw: Any, normalized: Any) -> Dict[str, Any]:
    return {
        "code": code,
        "field": field,
        "raw": "" if raw is None else str(raw),
        "normalized": normalized,
    }


def _parse_int(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except Exception:
        return None


def normalize_limit_offset(
    query: Dict[str, Any],
    *,
    default_limit: int,
    max_limit: int,
    default_offset: int = 0,
    max_offset: Optional[int] = None,
) -> PaginationMeta:
    warnings: List[Dict[str, Any]] = []

    raw_limit = query.get("limit")
    limit = _parse_int(raw_limit)
    if limit is None:
        if raw_limit is not None:
            warnings.append(
                _warn("R95_INVALID_LIMIT", "limit", raw_limit, default_limit)
            )
        limit = default_limit
    if limit < 1:
        warnings.append(_warn("R95_LIMIT_BELOW_MIN", "limit", raw_limit, 1))
        limit = 1
    if limit > max_limit:
        warnings.append(_warn("R95_LIMIT_CLAMPED", "limit", raw_limit, max_limit))
        limit = max_limit

    raw_offset = query.get("offset")
    offset = _parse_int(raw_offset)
    if offset is None:
        if raw_offset is not None:
            warnings.append(
                _warn("R95_INVALID_OFFSET", "offset", raw_offset, default_offset)
            )
        offset = default_offset
    if offset < 0:
        warnings.append(_warn("R95_OFFSET_BELOW_MIN", "offset", raw_offset, 0))
        offset = 0
    if max_offset is not None and offset > max_offset:
        warnings.append(_warn("R95_OFFSET_CLAMPED", "offset", raw_offset, max_offset))
        offset = max_offset

    return PaginationMeta(limit=limit, offset=offset, warnings=warnings)


def normalize_cursor_limit(
    query: Dict[str, Any],
    *,
    cursor_key: str = "since",
    default_cursor: int = 0,
    min_cursor: int = 0,
    default_limit: int,
    max_limit: int,
) -> PaginationMeta:
    page = normalize_limit_offset(
        query,
        default_limit=default_limit,
        max_limit=max_limit,
        default_offset=0,
    )
    raw_cursor = query.get(cursor_key)
    cursor = _parse_int(raw_cursor)
    if cursor is None:
        if raw_cursor is not None:
            page.warnings.append(
                _warn(
                    "R95_INVALID_CURSOR",
                    cursor_key,
                    raw_cursor,
                    default_cursor,
                )
            )
        cursor = default_cursor
    if cursor < min_cursor:
        page.warnings.append(
            _warn("R95_CURSOR_BELOW_MIN", cursor_key, raw_cursor, min_cursor)
        )
        cursor = min_cursor
    page.cursor = cursor
    return page


def bounded_scan_collect(
    records: Iterable[Any],
    *,
    skip: int,
    take: int,
    scan_cap: int,
    serializer: Callable[[Any], Any],
) -> BoundedScanResult:
    if scan_cap < 1:
        scan_cap = 1

    scanned = 0
    skipped_valid = 0
    skipped_malformed = 0
    items: List[Any] = []

    for rec in records:
        if scanned >= scan_cap:
            break
        scanned += 1
        try:
            payload = serializer(rec)
        except (AttributeError, TypeError, ValueError):
            skipped_malformed += 1
            continue

        if skipped_valid < skip:
            skipped_valid += 1
            continue

        items.append(payload)
        if len(items) >= take:
            break

    truncated = scanned >= scan_cap and len(items) < take
    return BoundedScanResult(
        items=items,
        scanned=scanned,
        skipped_malformed=skipped_malformed,
        truncated=truncated,
    )
