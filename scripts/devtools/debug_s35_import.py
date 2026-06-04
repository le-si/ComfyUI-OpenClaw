"""
Debug script for S35 Transform Isolation.
Verifies that the correct executor (TransformProcessRunner) is allowed/loaded.
"""

import sys
from pathlib import Path

# Ensure project root is in path
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.constrained_transforms import get_transform_executor
from services.transform_runner import TransformProcessRunner


def main():
    print("Checking S35 Transform Executor...")
    executor = get_transform_executor()
    print(f"Executor Type: {type(executor)}")

    if isinstance(executor, TransformProcessRunner):
        print("SUCCESS: TransformProcessRunner is active (Isolation Enabled).")
    else:
        print("WARNING: TransformProcessRunner is NOT active.")
        # Check if feature flag is enabled
        from services.transform_common import is_transforms_enabled

        print(f"Feature Flag Enabled: {is_transforms_enabled()}")


if __name__ == "__main__":
    main()
