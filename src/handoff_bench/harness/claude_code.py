"""Claude Code as the orchestrator: `claude -p` with the stand's MCP server as its only tools.

The boundary stays ours (`delegate` runs our subagent), so faults, keys, channels and the call
journal are the same as with our loop; the orchestrator's context, prompt and tool handling are
Claude Code's. Its raw stream is kept in `harness_stream.jsonl` next to the episode's logs.
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from handoff_bench.agents.gateway import ToolGateway
from handoff_bench.agents.loop import AgentSpec, AgentTrace
from handoff_bench.agents.model_client import Usage
from handoff_bench.agents.tools import StopReason
from handoff_bench.harness.stand_mcp import SERVER_NAME, StandTools, serving

STREAM_FILE = "harness_stream.jsonl"
TOOL_PREFIX = f"mcp__{SERVER_NAME}__"
# Variables that would switch Claude Code from the subscription to an API key.
_API_KEY_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


class HarnessSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    harness: Literal["claude_code"] = "claude_code"
    model: str  # an alias ("opus", "sonnet") or a full model name
    binary: str = "claude"
    auth: Literal["subscription", "api_key"] = "subscription"
    timeout_s: float = 900
    finish_grace_s: float = 60  # after `finish`, time to end the turn before we stop it

    @property
    def label(self) -> str:
        return f"{self.harness}:{self.model}"


class ClaudeCodeOrchestrator:
    def __init__(self, settings: HarnessSettings) -> None:
        self.settings = settings

    def command(self, spec: AgentSpec, mcp_url: str) -> list[str]:
        mcp = {"mcpServers": {SERVER_NAME: {"type": "http", "url": mcp_url}}}
        tools = [t["function"]["name"] for t in spec.tools]
        return [
            self.settings.binary,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            self.settings.model,
            "--append-system-prompt",
            spec.system_prompt,
            "--no-session-persistence",
            "--disable-slash-commands",
            "--strict-mcp-config",
            "--mcp-config",
            json.dumps(mcp),
            "--allowedTools",
            ",".join(TOOL_PREFIX + t for t in tools),
            # No built-in tools: no shell or files, so the episode's logs stay out of reach.
            "--tools",
            "",
        ]

    def env(self) -> dict[str, str]:
        if self.settings.auth == "api_key":
            return dict(os.environ)
        return {k: v for k, v in os.environ.items() if k not in _API_KEY_VARS}

    async def run(
        self, spec: AgentSpec, goal: str, *, gateway: ToolGateway, episode_dir: Path
    ) -> AgentTrace:
        tools = StandTools(
            gateway,
            actor=spec.actor,
            tools=[t["function"]["name"] for t in spec.tools],
            finish_tools=spec.finish_tools,
        )
        with tempfile.TemporaryDirectory(prefix="handoff-cc-") as cwd:
            async with serving(tools) as url:
                harness = asyncio.ensure_future(
                    self._run_process(spec, goal, url, cwd, tools, episode_dir / STREAM_FILE)
                )
                await tools.serve(until=harness)
                events, timed_out, too_long = await harness
        return to_trace(spec, goal, events, tools, timed_out=timed_out, too_long=too_long)

    async def _run_process(
        self,
        spec: AgentSpec,
        goal: str,
        url: str,
        cwd: str,
        tools: StandTools,
        stream_path: Path,
    ) -> tuple[list[dict[str, Any]], bool, bool]:
        proc = await asyncio.create_subprocess_exec(
            *self.command(spec, url),
            cwd=cwd,
            env=self.env(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,
        )
        assert proc.stdin and proc.stdout and proc.stderr
        # The goal goes through stdin: as an argument it would be read as one more allowed tool.
        proc.stdin.write(goal.encode())
        await proc.stdin.drain()
        proc.stdin.close()

        state = {"timed_out": False, "too_long": False}

        def stop() -> None:
            if proc.returncode is None:
                proc.terminate()

        async def watchdog() -> None:
            try:
                await asyncio.wait_for(tools.stopped.wait(), self.settings.timeout_s)
            except TimeoutError:
                state["timed_out"] = True
                stop()
                return
            if tools.stop == "finish":
                await asyncio.sleep(self.settings.finish_grace_s)
            stop()

        events: list[dict[str, Any]] = []
        turns: set[str] = set()
        guard = asyncio.create_task(watchdog())
        stderr = asyncio.create_task(proc.stderr.read())
        try:
            with stream_path.open("w", encoding="utf-8") as out:
                async for raw in proc.stdout:
                    line = raw.decode(errors="replace").strip()
                    if not line:
                        continue
                    out.write(line + "\n")
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    events.append(event)
                    if event.get("type") == "assistant":
                        turns.add(event.get("message", {}).get("id", ""))
                        if len(turns) > spec.max_steps and tools.stop is None:
                            state["too_long"] = True
                            stop()
            await proc.wait()
        finally:
            guard.cancel()
            stop()
        err = (await stderr).decode(errors="replace").strip()
        if err:
            stream_path.with_suffix(".stderr.txt").write_text(err, encoding="utf-8")
        return events, state["timed_out"], state["too_long"]


def to_trace(
    spec: AgentSpec,
    goal: str,
    events: list[dict[str, Any]],
    tools: StandTools,
    *,
    timed_out: bool = False,
    too_long: bool = False,
) -> AgentTrace:
    result = next((e for e in reversed(events) if e.get("type") == "result"), None)
    messages = to_messages(spec.system_prompt, goal, events)
    steps = sum(1 for m in messages if m["role"] == "assistant")
    error = tools.error
    reason: StopReason
    if tools.stop is not None:
        reason = tools.stop
    elif too_long:
        reason = "max_steps"
    elif timed_out:
        reason, error = "infra_error", "harness timed out"
    elif result is None:
        reason, error = "infra_error", "harness exited without a result"
    elif result.get("is_error"):
        reason, error = "api_error", str(result.get("result") or result.get("terminal_reason"))
    else:
        reason = "text_only"
    return AgentTrace(
        actor=spec.actor,
        stop_reason=reason,
        steps=steps,
        finish_tool=tools.finish_tool,
        finish_args=tools.finish_args,
        messages=messages,
        usage=_usage(result),
        retries=0,
        error=error,
    )


def _usage(result: dict[str, Any] | None) -> Usage:
    if result is None:
        return Usage()
    u = result.get("usage") or {}
    cached = u.get("cache_read_input_tokens", 0)
    return Usage(
        prompt_tokens=u.get("input_tokens", 0) + cached + u.get("cache_creation_input_tokens", 0),
        completion_tokens=u.get("output_tokens", 0),
        reasoning_tokens=(u.get("output_tokens_details") or {}).get("thinking_tokens", 0),
        cached_tokens=cached,
        # On a subscription this is what the run would cost on the API, not a charge.
        cost=float(result.get("total_cost_usd") or 0.0),
    )


def to_messages(
    system_prompt: str, goal: str, events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The stream as chat messages in our loop's format, so `show.py` and the coder read it.
    The system message is only our part of the prompt: Claude Code appends it to its own.

    One model response may arrive as several `assistant` events with the same message id.
    """
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": goal},
    ]
    by_id: dict[str, dict[str, Any]] = {}
    for event in events:
        kind, msg = event.get("type"), event.get("message") or {}
        if kind == "assistant":
            mid = msg.get("id", "")
            if mid not in by_id:
                by_id[mid] = {"role": "assistant", "content": None, "tool_calls": []}
                messages.append(by_id[mid])
            target = by_id[mid]
            for block in msg.get("content") or []:
                if block.get("type") == "text":
                    target["content"] = (target["content"] or "") + block.get("text", "")
                elif block.get("type") == "tool_use":
                    name = block.get("name", "")
                    target["tool_calls"].append(
                        {
                            "id": block.get("id"),
                            "type": "function",
                            "function": {
                                "name": name.removeprefix(TOOL_PREFIX),
                                "arguments": json.dumps(block.get("input") or {}),
                            },
                        }
                    )
        elif kind == "user":
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": block.get("tool_use_id"),
                            "content": _text(block.get("content")),
                        }
                    )
    for m in by_id.values():
        if not m["tool_calls"]:
            del m["tool_calls"]
    return messages


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""
