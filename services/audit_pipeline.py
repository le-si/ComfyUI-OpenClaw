"""Audit persistence sinks and chain verification helpers."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol


class AuditSink(Protocol):
    def append_entry(
        self, entry: Dict[str, Any], *, last_hash: Optional[str]
    ) -> str: ...


@dataclass
class AuditVerificationIssue:
    code: str
    file_path: str
    line_number: int
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "message": self.message,
        }


@dataclass
class AuditVerificationResult:
    ok: bool
    files_checked: List[str]
    entries_checked: int
    window_start_prev_hash: str
    terminal_hash: str
    window_truncated: bool
    issues: List[AuditVerificationIssue] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "files_checked": list(self.files_checked),
            "entries_checked": self.entries_checked,
            "window_start_prev_hash": self.window_start_prev_hash,
            "terminal_hash": self.terminal_hash,
            "window_truncated": self.window_truncated,
            "issues": [issue.to_dict() for issue in self.issues],
        }


def _rotated_chain_paths(base_path: str) -> List[str]:
    base = Path(base_path)
    parent = base.parent if str(base.parent) else Path(".")
    if not parent.exists():
        return []
    pattern = re.compile(rf"^{re.escape(base.name)}\.(\d+)$")
    matches = []
    for child in parent.iterdir():
        if not child.is_file():
            continue
        match = pattern.match(child.name)
        if not match:
            continue
        matches.append((int(match.group(1)), str(child)))
    matches.sort(reverse=True)
    return [path for _, path in matches]


def iter_audit_chain_paths(base_path: str) -> List[str]:
    paths = _rotated_chain_paths(base_path)
    if os.path.exists(base_path):
        paths.append(base_path)
    return paths


def _tail_hash_from_file(path: str) -> Optional[str]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            if size <= 0:
                return None
            step = min(size, 8192)
            handle.seek(size - step)
            data = handle.read().decode("utf-8", errors="ignore")
    except Exception:
        return None

    lines = [line.strip() for line in data.splitlines() if line.strip()]
    if not lines:
        return None
    try:
        payload = json.loads(lines[-1])
    except Exception:
        return None
    value = payload.get("entry_hash")
    return value if isinstance(value, str) and value else None


def read_last_entry_hash_from_chain(base_path: str) -> str:
    newest_first = [base_path] if os.path.exists(base_path) else []
    newest_first.extend(reversed(_rotated_chain_paths(base_path)))
    for path in newest_first:
        value = _tail_hash_from_file(path)
        if value:
            return value
    return "GENESIS"


class LocalFileAuditSink:
    def __init__(
        self,
        *,
        path: str,
        max_bytes: int,
        backups: int,
        chain_hash: Callable[[str, Dict[str, Any]], str],
    ) -> None:
        self.path = path
        self.max_bytes = max_bytes
        self.backups = backups
        self._chain_hash = chain_hash

    def _ensure_parent_dir(self) -> None:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        except Exception:
            pass

    def rotate_if_needed(self) -> None:
        if self.max_bytes <= 0 or self.backups < 0:
            return
        if not os.path.exists(self.path):
            return
        if os.path.getsize(self.path) < self.max_bytes:
            return
        if self.backups == 0:
            os.remove(self.path)
            return
        for idx in range(self.backups, 0, -1):
            src = f"{self.path}.{idx}"
            dst = f"{self.path}.{idx + 1}"
            if os.path.exists(src):
                if idx == self.backups:
                    os.remove(src)
                else:
                    os.replace(src, dst)
        os.replace(self.path, f"{self.path}.1")

    def append_entry(self, entry: Dict[str, Any], *, last_hash: Optional[str]) -> str:
        self._ensure_parent_dir()
        self.rotate_if_needed()
        prev_hash = last_hash or read_last_entry_hash_from_chain(self.path)
        event_hash = self._chain_hash(prev_hash, entry)
        wrapped = dict(entry)
        wrapped["prev_hash"] = prev_hash
        wrapped["entry_hash"] = event_hash
        line = json.dumps(wrapped, sort_keys=True, ensure_ascii=True) + "\n"
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(line)
        return event_hash


def verify_audit_chain(
    base_path: str,
    *,
    chain_hash: Callable[[str, Dict[str, Any]], str],
) -> AuditVerificationResult:
    files = iter_audit_chain_paths(base_path)
    issues: List[AuditVerificationIssue] = []
    if not files:
        issues.append(
            AuditVerificationIssue(
                code="missing_chain",
                file_path=base_path,
                line_number=0,
                message="No audit log files found for verification.",
            )
        )
        return AuditVerificationResult(
            ok=False,
            files_checked=[],
            entries_checked=0,
            window_start_prev_hash="GENESIS",
            terminal_hash="GENESIS",
            window_truncated=False,
            issues=issues,
        )

    previous_entry_hash: Optional[str] = None
    window_start_prev_hash = "GENESIS"
    entries_checked = 0

    for file_path in files:
        with open(file_path, "r", encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    wrapped = json.loads(line)
                except Exception as exc:
                    issues.append(
                        AuditVerificationIssue(
                            code="invalid_json",
                            file_path=file_path,
                            line_number=line_number,
                            message=f"Invalid JSON entry: {exc}",
                        )
                    )
                    return AuditVerificationResult(
                        ok=False,
                        files_checked=files,
                        entries_checked=entries_checked,
                        window_start_prev_hash=window_start_prev_hash,
                        terminal_hash=previous_entry_hash or "GENESIS",
                        window_truncated=window_start_prev_hash != "GENESIS",
                        issues=issues,
                    )

                prev_hash = wrapped.get("prev_hash")
                entry_hash = wrapped.get("entry_hash")
                if not isinstance(prev_hash, str) or not isinstance(entry_hash, str):
                    issues.append(
                        AuditVerificationIssue(
                            code="missing_hash_fields",
                            file_path=file_path,
                            line_number=line_number,
                            message="Audit entry is missing string prev_hash/entry_hash fields.",
                        )
                    )
                    return AuditVerificationResult(
                        ok=False,
                        files_checked=files,
                        entries_checked=entries_checked,
                        window_start_prev_hash=window_start_prev_hash,
                        terminal_hash=previous_entry_hash or "GENESIS",
                        window_truncated=window_start_prev_hash != "GENESIS",
                        issues=issues,
                    )

                if previous_entry_hash is None:
                    window_start_prev_hash = prev_hash
                elif prev_hash != previous_entry_hash:
                    issues.append(
                        AuditVerificationIssue(
                            code="prev_hash_mismatch",
                            file_path=file_path,
                            line_number=line_number,
                            message=(
                                "Audit chain continuity failed: prev_hash does not match "
                                "the preceding entry_hash."
                            ),
                        )
                    )
                    return AuditVerificationResult(
                        ok=False,
                        files_checked=files,
                        entries_checked=entries_checked,
                        window_start_prev_hash=window_start_prev_hash,
                        terminal_hash=previous_entry_hash or "GENESIS",
                        window_truncated=window_start_prev_hash != "GENESIS",
                        issues=issues,
                    )

                payload = dict(wrapped)
                payload.pop("prev_hash", None)
                payload.pop("entry_hash", None)
                expected_hash = chain_hash(prev_hash, payload)
                if entry_hash != expected_hash:
                    issues.append(
                        AuditVerificationIssue(
                            code="entry_hash_mismatch",
                            file_path=file_path,
                            line_number=line_number,
                            message="Audit entry_hash does not match the persisted payload.",
                        )
                    )
                    return AuditVerificationResult(
                        ok=False,
                        files_checked=files,
                        entries_checked=entries_checked,
                        window_start_prev_hash=window_start_prev_hash,
                        terminal_hash=previous_entry_hash or "GENESIS",
                        window_truncated=window_start_prev_hash != "GENESIS",
                        issues=issues,
                    )

                previous_entry_hash = entry_hash
                entries_checked += 1

    return AuditVerificationResult(
        ok=True,
        files_checked=files,
        entries_checked=entries_checked,
        window_start_prev_hash=window_start_prev_hash,
        terminal_hash=previous_entry_hash or "GENESIS",
        window_truncated=window_start_prev_hash != "GENESIS",
        issues=[],
    )
