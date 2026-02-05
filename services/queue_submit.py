"""
Queue Submit Service (F5 + R33).
Submits prompt workflows to ComfyUI execution queue with execution budgets.

- Uses internal HTTP call to POST /prompt
- Handles client_id and extra metadata
- R33: Applies concurrency caps and render size budgets
"""

import json
import logging
import uuid
from typing import Any, Dict, Optional

import aiohttp

logger = logging.getLogger("ComfyUI-OpenClaw.services.queue")

import os

# ComfyUI internal server URL fallback
COMFYUI_URL = (
    os.environ.get("OPENCLAW_COMFYUI_URL")
    or os.environ.get("MOLTBOT_COMFYUI_URL")
    or "http://127.0.0.1:8188"
)


async def submit_prompt(
    prompt_workflow: Dict[str, Any],
    client_id: Optional[str] = None,
    extra_data: Optional[Dict[str, Any]] = None,
    source: str = "unknown",  # R33: Source tracking
    trace_id: Optional[str] = None,  # R33: Trace ID for logging
) -> Dict[str, Any]:
    """
    Submit a prompt workflow to ComfyUI with execution budgets (R33).

    Args:
        prompt_workflow: The full workflow JSON (API format)
        client_id: Optional client ID for WebSocket mapping
        extra_data: Extra metadata to attach (logging, etc)
        source: Source type ("webhook" | "trigger" | "scheduler" | "bridge" | "unknown")
        trace_id: Optional trace ID for logging/correlation

    Returns:
        Dict containing 'prompt_id' and 'number' (queue position) or error info.

    Raises:
        BudgetExceededError: If concurrency or size budgets are exceeded
    """
    from services.execution_budgets import check_render_size, get_limiter

    # R33: Check render size budget
    check_render_size(prompt_workflow, trace_id=trace_id)

    if client_id is None:
        client_id = str(uuid.uuid4())

    payload = {"prompt": prompt_workflow, "client_id": client_id}

    if extra_data:
        payload["extra_data"] = extra_data

    # R33: Acquire concurrency budget
    limiter = get_limiter()
    async with limiter.acquire(source=source, trace_id=trace_id):
        # Use aiohttp to post to local ComfyUI instance
        # We assume we are running INSIDE ComfyUI process, but for HTTP access we use localhost
        # unless we can hook internal server entry point.
        # For MVP, HTTP loopback is safest and standard.

        url = f"{COMFYUI_URL}/prompt"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        logger.info(
                            f"Queued prompt: {data.get('prompt_id')} (source={source}, trace_id={trace_id})"
                        )
                        return data
                    else:
                        text = await resp.text()
                        logger.error(
                            f"Failed to queue prompt: {resp.status} - {text} (source={source}, trace_id={trace_id})"
                        )
                        raise RuntimeError(f"Queue submission failed: {resp.status}")
        except Exception as e:
            logger.error(
                f"Error submitting to queue: {e} (source={source}, trace_id={trace_id})"
            )
            raise
