"""
Safe log tail helper for observability.
Reads last N lines of a log file without loading entire file into memory.
"""

import os
from typing import List


def tail_log(path: str, lines: int = 200) -> List[str]:
    """
    Return last `lines` lines from the log file at `path`.

    Args:
        path: Absolute path to log file.
        lines: Number of lines to return (clamped 1-2000).

    Returns:
        List of log lines (most recent last).
    """
    # Clamp lines
    lines = max(1, min(2000, lines))

    if not os.path.isfile(path):
        return []

    try:
        # Read file in reverse efficiently
        # For simplicity, read entire file if small, otherwise use seek
        file_size = os.path.getsize(path)

        if file_size == 0:
            return []

        # For files under 1MB, just read all and take last N
        if file_size < 1024 * 1024:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
                return [line.rstrip("\n\r") for line in all_lines[-lines:]]

        # For larger files, read from end
        # Estimate bytes to read (assume avg 200 bytes per line)
        estimated_bytes = lines * 200

        with open(path, "rb") as f:
            # Seek to near end
            seek_pos = max(0, file_size - estimated_bytes)
            f.seek(seek_pos)

            # Read to end
            data = f.read()

        # Decode and split
        text = data.decode("utf-8", errors="replace")
        all_lines = text.splitlines()

        # If we didn't start at beginning, first line may be partial
        if seek_pos > 0 and len(all_lines) > 0:
            all_lines = all_lines[1:]  # Drop partial first line

        return all_lines[-lines:]

    except Exception:
        return []
