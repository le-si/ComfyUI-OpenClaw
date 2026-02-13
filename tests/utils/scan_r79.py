"""
R79 Egress Compliance Scanner.
Checks codebase for forbidden outbound network primitives.
"""

import os
import sys

# Safe egress primitives
ALLOWED_FILES = {
    "services/safe_io.py",
    "services/llm_client.py",
    "services/webhook_auth.py",
    "connector/base.py",
    # Legacy/Approved Egress Paths
    "api/config.py",
    "services/queue_submit.py",
    # Connector Implementations
    "connector/llm_client.py",
    "connector/openclaw_client.py",
    "connector/platforms/discord_gateway.py",
    "connector/platforms/line_webhook.py",
    "connector/platforms/telegram_polling.py",
    "connector/platforms/wechat_webhook.py",
    "connector/platforms/whatsapp_webhook.py",
    # Providers
    "services/providers/anthropic.py",
    "services/providers/openai_compat.py",
    "services/providers/openai.py",
}

FORBIDDEN = [
    ("requests.get(", "Direct requests.get"),
    ("requests.post(", "Direct requests.post"),
    ("requests.put(", "Direct requests.put"),
    ("requests.delete(", "Direct requests.delete"),
    ("requests.request(", "Direct requests.request"),
    ("requests.Session(", "Direct requests.Session"),
    ("urllib.request.urlopen(", "Direct urllib urlopen"),
    ("urllib3.PoolManager(", "Direct urllib3.PoolManager"),
    ("urllib3.request(", "Direct urllib3.request"),
    ("httpx.Client(", "Direct httpx.Client"),
    ("httpx.AsyncClient(", "Direct httpx.AsyncClient"),
    ("httpx.request(", "Direct httpx.request"),
    ("httpx.get(", "Direct httpx.get"),
    ("httpx.post(", "Direct httpx.post"),
    ("aiohttp.ClientSession", "Direct aiohttp ClientSession"),
    ("aiohttp.request(", "Direct aiohttp.request"),
]

SKIP_DIRS = [
    "tests",
    "venv",
    ".git",
    "__pycache__",
    "node_modules",
    "scripts",
    "REFERENCE",
    ".agent",
    ".planning",
]


def scan():
    print("Starting R79 Egress Compliance Scan...")
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    violations = []

    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [
            d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
        ]

        for f in filenames:
            if not f.endswith(".py"):
                continue

            full_path = os.path.join(dirpath, f)
            rel_path = os.path.relpath(full_path, root_dir).replace("\\", "/")

            if rel_path in ALLOWED_FILES:
                continue

            try:
                with open(full_path, "r", encoding="utf-8") as f_obj:
                    content = f_obj.read()

                    for pattern, desc in FORBIDDEN:
                        if pattern in content:
                            violations.append(f"{rel_path}: Found {desc}")
            except Exception as e:
                print(f"Error reading {rel_path}: {e}")

    if violations:
        print("\n[FAIL] R79 Violations Found:")
        for v in violations:
            print(f"  - {v}")
        sys.exit(1)
    else:
        print("\n[PASS] No egress violations found.")
        sys.exit(0)


if __name__ == "__main__":
    scan()
