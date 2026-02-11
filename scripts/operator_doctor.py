#!/usr/bin/env python
"""
R72 — Operator Doctor CLI convenience script.

Usage:
    python scripts/operator_doctor.py
    python scripts/operator_doctor.py --json
    python scripts/operator_doctor.py --pack-root /path/to/pack
"""

import os
import sys

# Ensure pack root is on sys.path for imports
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PACK_ROOT = os.path.dirname(SCRIPT_DIR)
if PACK_ROOT not in sys.path:
    sys.path.insert(0, PACK_ROOT)

from services.operator_doctor import main

if __name__ == "__main__":
    main()
