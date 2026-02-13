"""
Sidecar Launcher Script.
Entry point for running the OpenClaw Sidecar process.
"""

import asyncio
import os
import sys

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.sidecar.runtime import SidecarRuntime

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    runtime = SidecarRuntime()
    try:
        asyncio.run(runtime.start())
    except KeyboardInterrupt:
        pass
