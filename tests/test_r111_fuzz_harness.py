"""
R111: Fuzz/property adversarial harness.
Simple property-based testing harness for security boundaries.
"""

import ipaddress
import json
import logging
import os
import random
import socket
import string
import sys
import time
import traceback
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import patch

# Adjust path to import services
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.access_control import is_loopback
from services.policy_posture import PolicyBundle
from services.safe_io import SSRFError, validate_outbound_url

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fuzz_harness")

ARTIFACT_DIR = os.path.join(os.path.dirname(__file__), "fuzz_artifacts")
os.makedirs(ARTIFACT_DIR, exist_ok=True)


class FuzzStrategies:
    @staticmethod
    def random_string(min_len=0, max_len=100, chars=string.printable) -> str:
        return "".join(random.choices(chars, k=random.randint(min_len, max_len)))

    @staticmethod
    def unsafe_strings() -> List[str]:
        return [
            "../../../etc/passwd",
            "<script>alert(1)</script>",
            "' OR '1'='1",
            "\x00",
            "\uffff",
            "A" * 10000,  # Buffer overflow candidate
            "http://169.254.169.254/latest/meta-data/",
            "file:///etc/passwd",
            "gopher://localhost:6379/_SLAVEOF...",
        ]

    @staticmethod
    def random_json(depth=2) -> Any:
        if depth == 0:
            return random.choice(
                [
                    FuzzStrategies.random_string(),
                    random.randint(-1000, 1000),
                    random.random(),
                    True,
                    False,
                    None,
                ]
            )

        # 50% chance of complex structure
        if random.random() < 0.5:
            return random.choice(
                [FuzzStrategies.random_string(), random.randint(-1000, 1000)]
            )

        is_list = random.random() < 0.5
        if is_list:
            return [
                FuzzStrategies.random_json(depth - 1)
                for _ in range(random.randint(0, 5))
            ]
        else:
            return {
                FuzzStrategies.random_string(
                    1, 10, string.ascii_letters
                ): FuzzStrategies.random_json(depth - 1)
                for _ in range(random.randint(0, 5))
            }


class Fuzzer:
    def __init__(self):
        self.crashes = []
        self.start_time = time.time()

    def fuzz_target(
        self, name: str, target_func: Callable, input_gen: Callable, max_runs=1000
    ):
        logger.info(f"Starting fuzzing for target: {name}")
        for i in range(max_runs):
            inp = input_gen()
            try:
                target_func(inp)
            except (ValueError, TypeError, SSRFError, json.JSONDecodeError, KeyError):
                # Expected errors
                pass
            except Exception as e:
                # Unexpected crash
                logger.error(f"CRASH in {name}: {e}")
                traceback.print_exc()
                self._save_crash(name, inp, e)

        logger.info(f"Finished fuzzing {name}. Crashes: {len(self.crashes)}")

    def _save_crash(self, name: str, inp: Any, exception: Exception):
        filename = f"crash_{name}_{int(time.time()*1000)}.json"
        path = os.path.join(ARTIFACT_DIR, filename)

        crash_data = {
            "target": name,
            "input": str(inp),
            "exception": str(exception),
            "traceback": traceback.format_exc(),
        }

        with open(path, "w") as f:
            json.dump(crash_data, f, indent=2)

        self.crashes.append(path)


# --- Fuzz Targets Wrappers ---


def fuzz_url_validation(inp):
    # CRITICAL: keep DNS resolver stubbed in fuzzing; live DNS makes this harness
    # non-deterministic and can hang in CI/offline environments.
    with patch(
        "services.safe_io.socket.getaddrinfo", side_effect=_deterministic_getaddrinfo
    ):
        # Try validation. Should raise SSRFError or return tuple or raise ValueError
        validate_outbound_url(inp, allow_any_public_host=True)


def _deterministic_getaddrinfo(host, port, *_args, **_kwargs):
    """
    Fast, offline resolver stub for fuzzing.
    - Preserve IP-host behavior (private IP inputs should still be blocked).
    - Map hostname inputs to a fixed public test IP to avoid network dependency.
    """
    try:
        ipaddress.ip_address(host)
        resolved_ip = host
    except ValueError:
        resolved_ip = "93.184.216.34"

    return [
        (
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            (resolved_ip, int(port)),
        )
    ]


def fuzz_policy_bundle(inp):
    # Input should be a dict-like structure
    if isinstance(inp, str):
        try:
            inp = json.loads(inp)
        except:
            return

    if not isinstance(inp, dict):
        return

    # Try to parse
    PolicyBundle.from_dict(inp)


def fuzz_is_loopback(inp):
    if not isinstance(inp, str):
        return
    is_loopback(inp)


def run_fuzz_suite():
    fuzzer = Fuzzer()

    # 1. fuzz_url_validation
    # Mixed random strings and known dangerous payloads
    def url_gen():
        if random.random() < 0.2:
            return random.choice(FuzzStrategies.unsafe_strings())
        return "http://" + FuzzStrategies.random_string(
            1, 20, string.ascii_letters + ".:/"
        )

    fuzzer.fuzz_target(
        "validate_outbound_url", fuzz_url_validation, url_gen, max_runs=500
    )

    # 2. fuzz_policy_bundle
    def bundle_gen():
        return FuzzStrategies.random_json(depth=3)

    fuzzer.fuzz_target(
        "PolicyBundle.from_dict", fuzz_policy_bundle, bundle_gen, max_runs=500
    )

    # 3. fuzz_is_loopback
    def ip_gen():
        if random.random() < 0.2:
            return random.choice(FuzzStrategies.unsafe_strings())
        # Generate random IP-like strings
        return ".".join(str(random.randint(0, 300)) for _ in range(4))

    fuzzer.fuzz_target("is_loopback", fuzz_is_loopback, ip_gen, max_runs=500)

    # 4. fuzz_path_normalization
    from services.safe_io import PathTraversalError, resolve_under_root

    def path_gen():
        # Mix of valid relative paths, traversals, and absolute paths
        parts = ["foo", "..", "bar", "//", "\\", "C:", "/etc/passwd", "~", "."]
        return os.path.join(
            *[random.choice(parts) for _ in range(random.randint(1, 5))]
        )

    def fuzz_resolve(inp):
        try:
            # use a temp dir as root
            resolve_under_root("/tmp/safe_root", inp)
        except (PathTraversalError, ValueError):
            pass

    fuzzer.fuzz_target("resolve_under_root", fuzz_resolve, path_gen, max_runs=500)

    if fuzzer.crashes:
        print(f"FAILED: {len(fuzzer.crashes)} crashes detected. See {ARTIFACT_DIR}")
        sys.exit(1)
    else:
        print("SUCCESS: No crashes detected.")


if __name__ == "__main__":
    run_fuzz_suite()
