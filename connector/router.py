"""
Connector Router (F29 Remediation).
Dispatches parsed commands to handlers with AST argument parsing.
"""

import logging
import shlex
from typing import Any, Dict, List

from .config import ConnectorConfig
from .contract import CommandRequest, CommandResponse
from .openclaw_client import OpenClawClient
from .state import ConnectorState

if False:  # Type hinting only
    from .results_poller import ResultsPoller

from .llm_client import LLMClient
from .prompts import CHAT_SYSTEM_PROMPT, CHAT_STATUS_PROMPT

logger = logging.getLogger(__name__)


class CommandRouter:
    def __init__(self, config: ConnectorConfig, client: OpenClawClient, poller: "ResultsPoller" = None):
        self.config = config
        self.client = client
        self.poller = poller
        self.state = ConnectorState(path=self.config.state_path)

    async def handle(self, req: CommandRequest) -> CommandResponse:
        """Main dispatch loop."""
        text = req.text.strip()

        try:
            parts = shlex.split(text)
        except ValueError:
            return CommandResponse(
                text="[Error] Parsing command arguments failed (unbalanced quotes?)."
            )

        if not parts:
            return CommandResponse(text="Empty command.")

        cmd = parts[0].lower()
        args = parts[1:]

        # Dispatch Table
        handlers = {
            ("/status", "status"): (self._handle_status, False),
            ("/help", "help", "/start"): (self._handle_help, False),
            ("/run", "run"): (self._handle_run, False),
            ("/interrupt", "interrupt", "/cancel", "cancel", "/stop"): (
                self._handle_interrupt,
                True,
            ),  # Global interrupt => admin-only.
            ("/approvals", "approvals"): (self._handle_approvals_list, True),
            ("/approve", "approve"): (self._handle_approve, True),
            ("/reject", "reject"): (self._handle_reject, True),
            ("/schedules", "schedules"): (self._handle_schedules_list, True),
            ("/schedule", "schedule"): (self._handle_schedule_subcommand, True),
            # Phase 3 Introspection
            ("/history", "history"): (self._handle_history, False),
            ("/trace", "trace"): (self._handle_trace, True),  # Admin only
            ("/jobs", "jobs", "queue"): (self._handle_jobs, False),
            # F30: Chat Assistant
            ("/chat", "chat"): (self._handle_chat, False),
        }

        # Find Handler
        handler = None
        requires_admin = False

        for aliases, (func, admin_req) in handlers.items():
            if cmd in aliases:
                handler = func
                requires_admin = admin_req
                break

        if not handler:
            return CommandResponse(
                text=f"Unknown command: {cmd}. Type /help for options."
            )

        # Admin Check
        if requires_admin:
            if not self._is_admin(req.sender_id):
                return CommandResponse(
                    text="[Access Denied] This command requires Admin privileges."
                )

        # Execute
        try:
            return await handler(req, args)
        except Exception as e:
            logger.exception(f"Command execution error {cmd}: {e}")
            return CommandResponse(text=f"[Internal Error] {str(e)}")

    def _is_admin(self, user_id: str) -> bool:
        return str(user_id) in self.config.admin_users

    def _is_trusted(self, req: CommandRequest) -> bool:
        """
        Trusted users can execute /run immediately.
        Untrusted users are routed to approval flow.
        """
        if self._is_admin(req.sender_id):
            return True

        platform = (req.platform or "").lower()
        sender_id = str(req.sender_id)
        channel_id = str(req.channel_id)

        if platform == "telegram":
            try:
                uid = int(sender_id)
            except Exception:
                uid = None
            try:
                cid = int(channel_id)
            except Exception:
                cid = None
            if uid is not None and uid in self.config.telegram_allowed_users:
                return True
            if cid is not None and cid in self.config.telegram_allowed_chats:
                return True
            return False

        if platform == "discord":
            if sender_id in self.config.discord_allowed_users:
                return True
            if channel_id in self.config.discord_allowed_channels:
                return True
            return False

        if platform == "line":
            if sender_id in self.config.line_allowed_users:
                return True
            if channel_id in self.config.line_allowed_groups:
                return True
            return False

        # Unknown platform: trust only admins
        return False

    # --- Handlers ---

    async def _handle_status(
        self, req: CommandRequest, args: List[str]
    ) -> CommandResponse:
        health = await self.client.get_health()
        queue = await self.client.get_prompt_queue()

        # New standardized response handling
        health_ok = health.get("ok")

        status_icon = "Online" if health_ok else "Offline"
        details = []

        if health_ok:
            data = health.get("data", {})
            stats = data.get("stats", {})
            details.append(f"Logs: {stats.get('logs_processed', 0)}")
            details.append(f"Errors: {stats.get('errors_captured', 0)}")

            q_res = queue.get("data", {})
            q_rem = q_res.get("exec_info", {}).get("queue_remaining", 0)
            details.append(f"Queue: {q_rem}")
        else:
            details.append(f"Error: {health.get('error')}")

        return CommandResponse(
            text=f"[{status_icon}] System Status\n"
            + "\n".join(f"- {d}" for d in details)
        )

    async def _handle_run(
        self, req: CommandRequest, args: List[str]
    ) -> CommandResponse:
        if not args:
            return CommandResponse(
                text="Usage: /run <template_id> [key=value ...] [--approval]"
            )

        # Parse flags
        explicit_approval = False
        clean_args = []
        for arg in args:
            if arg in ("--require-approval", "--approval", "-a"):
                explicit_approval = True
            else:
                clean_args.append(arg)

        if not clean_args:
            return CommandResponse(text="Usage: /run <template_id> ...")

        template_id = clean_args[0]
        inputs = {}
        for arg in clean_args[1:]:
            if "=" in arg:
                k, v = arg.split("=", 1)
                inputs[k.strip()] = v.strip()

        trusted = self._is_trusted(req)
        require_approval = explicit_approval or (not trusted)

        res = await self.client.submit_job(
            template_id, inputs, require_approval=require_approval
        )
        if res.get("ok"):
            data = res.get("data", {})
            trace_id = data.get("trace_id", "unknown")

            if data.get("pending"):
                approval_id = data.get("approval_id", "unknown")
                msg = f"[Approval Requested]\nID: {approval_id}\nTrace: {trace_id}"
                if "expires_at" in data:
                    msg += f"\nExpires: {data['expires_at']}"
                return CommandResponse(text=msg)
            else:
                prompt_id = data.get("prompt_id", "unknown")
                if self.poller:
                    self.poller.track_job(prompt_id, req.platform, req.channel_id, req.sender_id)

                return CommandResponse(
                    text=f"[Job Submitted]\nID: {prompt_id}\nTemplate: {template_id}\nTrace: {trace_id}"
                )
        else:
            err = res.get("error", "Unknown error")
            return CommandResponse(text=f"[Submission Failed] Reason: {err}")

    async def _handle_interrupt(
        self, req: CommandRequest, args: List[str]
    ) -> CommandResponse:
        # Remediation: Global Interrupt
        res = await self.client.interrupt_output()
        if res.get("ok"):
            return CommandResponse(text="[Stop] Global Interrupt sent to ComfyUI.")
        else:
            return CommandResponse(text=f"[Stop Failed] {res.get('error')}")

    async def _handle_approvals_list(
        self, req: CommandRequest, args: List[str]
    ) -> CommandResponse:
        res = await self.client.get_approvals()
        if not res.get("ok"):
            return CommandResponse(
                text=f"[Error] Failed to list approvals: {res.get('error')}"
            )

        items = res.get("items", [])
        if not items:
            return CommandResponse(text="No pending approvals.")

        pending_count = res.get("pending_count")
        lines = []
        for i in items:
            # IMPORTANT (stability): the backend approval schema uses:
            # `approval_id`, `template_id`, `status`, `requested_by`, `source`.
            # Do not “simplify” these keys to `id/description/requester` unless you also
            # update the backend API + all tests. This mismatch previously caused silent
            # bad output and brittle regressions.
            approval_id = i.get("approval_id") or i.get("id") or "unknown"
            template_id = i.get("template_id") or "unknown"
            status = i.get("status") or "unknown"
            requested_by = i.get("requested_by") or "unknown"
            source = i.get("source") or "unknown"

            lines.append(
                f"- {approval_id} [{status}] template={template_id} by={requested_by} source={source}"
            )

        header = "Pending Approvals"
        if isinstance(pending_count, int):
            header += f" ({pending_count})"
        return CommandResponse(text=header + ":\n" + "\n".join(lines))

    async def _handle_approve(
        self, req: CommandRequest, args: List[str]
    ) -> CommandResponse:
        if not args:
            return CommandResponse(text="Usage: /approve <id>")

        # Assuming auto_execute=True by default for chat logic
        res = await self.client.approve_request(args[0], auto_execute=True)
        if not res.get("ok"):
            return CommandResponse(text=f"[Failed] {res.get('error')}")

        data = res.get("data", {})
        msg = f"[Approved] {args[0]}"

        # Phase 4: Show execution result
        if "prompt_id" in data:
            pid = data['prompt_id']
            msg += f"\nExecuted: {pid}"
            if self.poller:
                # Approval request might have come from different flow, but usually user invoking /approve 
                # wants the result. Using current req context is safest assumption for "ChatOps".
                self.poller.track_job(pid, req.platform, req.channel_id, req.sender_id)
        elif data.get("executed") is False:
            msg += "\n(Not Executed)"
            if err := data.get("execution_error"):
                msg += f"\nError: {err}"

        return CommandResponse(text=msg)

    async def _handle_reject(
        self, req: CommandRequest, args: List[str]
    ) -> CommandResponse:
        if not args:
            return CommandResponse(text="Usage: /reject <id> [reason]")

        reason = " ".join(args[1:]) if len(args) > 1 else "Rejected via chat"
        res = await self.client.reject_request(args[0], reason)
        if not res.get("ok"):
            return CommandResponse(text=f"[Failed] {res.get('error')}")

        return CommandResponse(text=f"[Rejected] {args[0]}")

    async def _handle_schedules_list(
        self, req: CommandRequest, args: List[str]
    ) -> CommandResponse:
        res = await self.client.get_schedules()
        if not res.get("ok"):
            return CommandResponse(text=f"[Error] {res.get('error')}")

        scheds = res.get("schedules", [])
        if not scheds:
            return CommandResponse(text="No schedules found.")

        lines = []
        for s in scheds:
            status = "+" if s.get("enabled") else "-"
            lines.append(
                f"[{status}] {s.get('id')}: {s.get('cron')} - {s.get('template_id')}"
            )

        return CommandResponse(text="Schedules:\n" + "\n".join(lines))

    async def _handle_schedule_subcommand(
        self, req: CommandRequest, args: List[str]
    ) -> CommandResponse:
        if len(args) < 2:
            return CommandResponse(text="Usage: /schedule <run|toggle> <id>")

        sub = args[0].lower()
        sid = args[1]

        if sub == "run":
            res = await self.client.run_schedule(sid)
            if not res.get("ok"):
                return CommandResponse(text=f"[Error] {res.get('error')}")
            return CommandResponse(text=f"[Success] Schedule {sid} triggered manually.")
        else:
            return CommandResponse(text="Not implemented yet.")

    async def _handle_help(
        self, req: CommandRequest, args: List[str]
    ) -> CommandResponse:
        return CommandResponse(
            text=(
                "OpenClaw Connector\n"
                "/status - Check system health and queue\n"
                "/run <template> [k=v] - Run a generation (trusted users auto-exec; others require approval)\n"
                "/stop - Global Interrupt (Admin)\n"
                "/history <id> - Job details\n"
                "/jobs - Queue summary\n"
                "Admin Only:\n"
                "/approvals - List pending approvals\n"
                "/approve <id>, /reject <id>\n"
                "/schedules, /schedule run <id>\n"
                "/trace <id> - Execution trace"
            )
        )

    async def _handle_history(
        self, req: CommandRequest, args: List[str]
    ) -> CommandResponse:
        if not args:
            return CommandResponse(text="Usage: /history <prompt_id>")
        res = await self.client.get_history(args[0])
        if not res.get("ok"):
            return CommandResponse(text=f"[Error] {res.get('error')}")

        # Simple format
        data = res.get("data", {})
        status = data.get("status", {}).get("status_str", "unknown")
        # Assuming backend returns a structure we can summarise
        return CommandResponse(
            text=f"Job {args[0]}: {status}\nFull details: not implemented in connector view yet."
        )

    async def _handle_trace(
        self, req: CommandRequest, args: List[str]
    ) -> CommandResponse:
        if not args:
            return CommandResponse(text="Usage: /trace <prompt_id>")
        res = await self.client.get_trace(args[0])
        if not res.get("ok"):
            return CommandResponse(text=f"[Error] {res.get('error')}")

        # Dump trace
        return CommandResponse(
            text=f"Trace {args[0]}: {str(res.get('data'))[:1000]}..."
        )

    async def _handle_jobs(
        self, req: CommandRequest, args: List[str]
    ) -> CommandResponse:
        # Try native /openclaw/jobs first
        res = await self.client.get_jobs()
        if res.get("ok"):
            # Format nice summary
            return CommandResponse(text=f"Default Jobs View: {res.get('data')}")

        # Fallback: Queue
        q = await self.client.get_prompt_queue()
        if q.get("ok"):
            rem = q.get("data", {}).get("exec_info", {}).get("queue_remaining", "?")
            return CommandResponse(text=f"[Fallback] Queue Remaining: {rem}")

        return CommandResponse(text="[Error] Could not fetch jobs or queue.")

    # -------------------------------------------------------------------------
    # F30: Chat LLM Assistant
    # -------------------------------------------------------------------------

    async def _handle_chat(
        self, req: CommandRequest, args: List[str]
    ) -> CommandResponse:
        """
        /chat [subcommand] <message>
        Subcommands: run, template, status
        Default: general chat
        
        Security: Never auto-executes commands. Only suggests command text.
        """
        llm = LLMClient(self.client)

        if not await llm.is_configured():
            return CommandResponse(
                text="[Chat Error] LLM not configured. Configure in OpenClaw Settings."
            )

        # Parse subcommand
        if not args:
            return CommandResponse(
                text="Usage: /chat <message> or /chat run|template|status <request>"
            )

        subcommand = args[0].lower()
        message = " ".join(args[1:]) if len(args) > 1 else ""

        trust_level = "TRUSTED" if self._is_trusted(req) else "UNTRUSTED"

        if subcommand == "run":
            return await self._chat_run(llm, message, trust_level)
        elif subcommand == "template":
            return await self._chat_template(llm, message)
        elif subcommand == "status":
            return await self._chat_status(llm)
        else:
            # General chat: first word is part of message
            full_message = " ".join(args)
            return await self._chat_general(llm, full_message, trust_level)

    async def _chat_general(
        self, llm: LLMClient, message: str, trust_level: str
    ) -> CommandResponse:
        """General chat with assistant."""
        system_prompt = CHAT_SYSTEM_PROMPT.format(trust_level=trust_level)
        response = await llm.chat(system_prompt, message)
        return CommandResponse(text=response)

    async def _chat_run(
        self, llm: LLMClient, request: str, trust_level: str
    ) -> CommandResponse:
        """Suggest a /run command based on user request."""
        if not request:
            return CommandResponse(text="Usage: /chat run <description of what you want>")

        # Get available templates (simplified - could fetch from API)
        templates = "txt2img, img2img, upscale (examples)"

        system_prompt = CHAT_SYSTEM_PROMPT.format(trust_level=trust_level)
        user_prompt = f"""User wants to run a generation. Suggest a `/run` command.

Request: {request}
Available templates: {templates}
Trust level: {trust_level}

Remember: {"add --approval flag" if trust_level == "UNTRUSTED" else "no --approval needed"}.
Output only the command in a code block."""

        response = await llm.chat(system_prompt, user_prompt)
        return CommandResponse(text=response)

    async def _chat_template(
        self, llm: LLMClient, request: str
    ) -> CommandResponse:
        """Generate a template JSON suggestion."""
        if not request:
            return CommandResponse(text="Usage: /chat template <description>")

        system_prompt = CHAT_SYSTEM_PROMPT.format(trust_level="N/A")
        user_prompt = f"""Generate a workflow template JSON for this request:

Request: {request}

Output:
1. Suggested filename
2. Template JSON in a code block

Keep it minimal."""

        response = await llm.chat(system_prompt, user_prompt)
        return CommandResponse(text=response)

    async def _chat_status(self, llm: LLMClient) -> CommandResponse:
        """Summarize system status using LLM."""
        # Fetch status data
        health = await self.client.get_health()
        jobs = await self.client.get_jobs()
        queue = await self.client.get_prompt_queue()

        status_data = {
            "health": health.get("data", {}) if health.get("ok") else "unavailable",
            "jobs": jobs.get("data", {}) if jobs.get("ok") else "unavailable",
            "queue": queue.get("data", {}) if queue.get("ok") else "unavailable",
        }

        system_prompt = CHAT_SYSTEM_PROMPT.format(trust_level="N/A")
        user_prompt = CHAT_STATUS_PROMPT.format(status_data=status_data)

        response = await llm.chat(system_prompt, user_prompt)
        return CommandResponse(text=response)

