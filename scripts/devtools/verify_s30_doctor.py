"""
Verify S30 Security Doctor output.
"""

import os
from pathlib import Path
import sys

# Add project root to path
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.security_doctor import run_security_doctor


def main():
    print("Running Security Doctor...")
    # Enable tools/transforms for full check
    os.environ["OPENCLAW_ENABLE_EXTERNAL_TOOLS"] = "true"
    os.environ["OPENCLAW_ENABLE_TRANSFORMS"] = "true"
    os.environ["OPENCLAW_ADMIN_TOKEN"] = "test-admin"
    os.environ["OPENCLAW_OBSERVABILITY_TOKEN"] = "test-obs"

    report = run_security_doctor(remediate=False)
    print(report.to_human())

    if report.has_failures:
        print("\nFAILURE: Security Doctor reported failures.")
        sys.exit(1)
    else:
        print("\nSUCCESS: Security Doctor reported no failures.")


if __name__ == "__main__":
    main()
