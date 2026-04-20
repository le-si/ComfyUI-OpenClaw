#!/usr/bin/env python3
"""
Guard the Comfy registry publish workflow so non-version pyproject edits do not
attempt to republish an already-existing node version.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _extract_toml_section(text: str, header: str) -> str | None:
    import re

    pattern = re.compile(rf"(?ms)^\[{re.escape(header)}\]\s*$\n(?P<body>.*?)(?=^\[|\Z)")
    match = pattern.search(text)
    if not match:
        return None
    return match.group("body")


def _extract_version_from_section(section_text: str) -> str | None:
    import re

    match = re.search(
        r"""(?m)^\s*version\s*=\s*["'](?P<version>[^"']+)["']\s*$""",
        section_text,
    )
    if not match:
        return None
    return match.group("version")


def read_project_version(path: Path) -> str:
    text = path.read_text(encoding="utf-8-sig")
    section = _extract_toml_section(text, "project")
    if section is None:
        raise ValueError(f"missing [project] section in {path}")
    version = _extract_version_from_section(section)
    if version is None:
        raise ValueError(f"missing project.version in {path}")
    return version


def read_previous_project_version(
    *, pyproject: Path, previous_pyproject: Path | None, previous_ref: str | None
) -> str | None:
    if previous_pyproject is not None:
        if not previous_pyproject.is_file():
            return None
        return read_project_version(previous_pyproject)

    if not previous_ref:
        return None

    repo_relative_path = pyproject.as_posix()
    result = subprocess.run(
        ["git", "show", f"{previous_ref}:{repo_relative_path}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None

    section = _extract_toml_section(result.stdout, "project")
    if section is None:
        return None
    return _extract_version_from_section(section)


def decide_should_publish(
    *, pyproject: Path, previous_pyproject: Path | None, previous_ref: str | None
) -> tuple[bool, str, str, str | None]:
    current_version = read_project_version(pyproject)
    previous_version = read_previous_project_version(
        pyproject=pyproject,
        previous_pyproject=previous_pyproject,
        previous_ref=previous_ref,
    )
    if previous_version is None:
        return True, "no_previous_version", current_version, None
    if previous_version == current_version:
        return False, "version_unchanged", current_version, previous_version
    return True, "version_changed", current_version, previous_version


def _write_outputs(
    *, output_path: Path | None, should_publish: bool, reason: str, current_version: str, previous_version: str | None
) -> None:
    lines = [
        f"should_publish={'true' if should_publish else 'false'}",
        f"reason={reason}",
        f"current_version={current_version}",
        f"previous_version={previous_version or ''}",
    ]
    if output_path is None:
        for line in lines:
            print(line)
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(f"{line}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Decide whether the Comfy registry publish workflow should run."
    )
    parser.add_argument("--pyproject", default="pyproject.toml")
    parser.add_argument("--previous-pyproject", default=None)
    parser.add_argument("--previous-ref", default=None)
    parser.add_argument("--github-output", default=None)
    args = parser.parse_args(argv)

    pyproject = Path(args.pyproject)
    previous_pyproject = (
        Path(args.previous_pyproject) if args.previous_pyproject else None
    )
    output_path = Path(args.github_output) if args.github_output else None

    should_publish, reason, current_version, previous_version = decide_should_publish(
        pyproject=pyproject,
        previous_pyproject=previous_pyproject,
        previous_ref=args.previous_ref,
    )
    _write_outputs(
        output_path=output_path,
        should_publish=should_publish,
        reason=reason,
        current_version=current_version,
        previous_version=previous_version,
    )
    print(
        "Registry publish guard: "
        f"should_publish={should_publish} reason={reason} "
        f"current_version={current_version} previous_version={previous_version or 'none'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
