"""A stand-in for `claude -p --output-format stream-json`: no model, a scripted plan.

`FAKE_CLAUDE_PLAN` is a JSON list of turns, the same for every run; a turn is a list of
`[tool, args]` calls made in one response (concurrently, as Claude Code does), or a string for a
final text answer. The tools are called on the MCP servers from `--mcp-config`, and the stream is
printed in Claude Code's format.

With `--agents` (native cells) the fake also plays Claude Code's own subagents: a call
`["Agent", {"prompt": ..., "turns": [...], "report": ...}]` starts one, whose turns call the bank
with the `PreToolUse` hook from `--settings` applied (deny, or new arguments), and whose events
carry `parent_tool_use_id`. `finish` goes to the `stand` server, other tools to `bank`.
"""

import asyncio
import json
import os
import subprocess
import sys

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent


def limit_reached() -> bool:
    """`FAKE_CLAUDE_RUNS` runs succeed (counted in `FAKE_CLAUDE_COUNTER`), then the limit."""
    runs = os.environ.get("FAKE_CLAUDE_RUNS")
    if runs is None:
        return False
    counter = os.environ["FAKE_CLAUDE_COUNTER"]
    done = int(open(counter).read()) if os.path.exists(counter) else 0
    with open(counter, "w") as f:
        f.write(str(done + 1))
    return done >= int(runs)


def emit(event: dict) -> None:
    print(json.dumps(event), flush=True)


def flag(args: list[str], name: str) -> str | None:
    return args[args.index(name) + 1] if name in args else None


class Fake:
    def __init__(self, args: list[str], clients: dict[str, Client]) -> None:
        self.clients = clients
        self.native = "--agents" in args
        settings = json.loads(flag(args, "--settings") or "{}")
        pre = settings.get("hooks", {}).get("PreToolUse", [])
        self.hook = pre[0]["hooks"][0]["command"] if pre else None
        self.ids = 0

    def server(self, tool: str) -> str:
        if not self.native:
            return next(iter(self.clients))
        return "stand" if tool == "finish" else "bank"

    def new_id(self) -> str:
        self.ids += 1
        return f"toolu_{self.ids}"

    def run_hook(self, event: dict) -> dict:
        if self.hook is None:
            return {}
        out = subprocess.run(
            self.hook, shell=True, input=json.dumps(event), capture_output=True, text=True
        )
        return (json.loads(out.stdout or "{}") or {}).get("hookSpecificOutput", {})

    async def call(self, tool: str, args: dict, use_id: str, agent: str | None) -> dict:
        """One MCP call; returns its `tool_result` block."""
        name = f"mcp__{self.server(tool)}__{tool}"
        if self.native and self.server(tool) == "bank":
            event = {"hook_event_name": "PreToolUse", "tool_name": name, "tool_input": args}
            event["tool_use_id"] = use_id
            if agent:
                event |= {"agent_id": agent, "agent_type": "payer"}
            decision = self.run_hook(event)
            if decision.get("permissionDecision") == "deny":
                reason = decision.get("permissionDecisionReason", "denied")
                return {
                    "type": "tool_result",
                    "tool_use_id": use_id,
                    "is_error": True,
                    "content": reason,
                }
            args = decision.get("updatedInput", args)
        r = await self.clients[self.server(tool)].call_tool(tool, args)
        text = [{"type": "text", "text": c.text} for c in r.content if isinstance(c, TextContent)]
        return {
            "type": "tool_result",
            "tool_use_id": use_id,
            "is_error": r.is_error,
            "content": text,
        }

    async def subagent(self, use_id: str, spec: dict) -> dict:
        agent = f"agent{use_id}"
        emit(
            {
                "type": "user",
                "parent_tool_use_id": use_id,
                "message": {"content": [{"type": "text", "text": spec["prompt"]}]},
            }
        )
        for k, turn in enumerate(spec.get("turns", []), 1):
            uses = [
                {"type": "tool_use", "id": self.new_id(), "name": f"mcp__bank__{tool}", "input": a}
                for tool, a in turn
            ]
            emit(
                {
                    "type": "assistant",
                    "parent_tool_use_id": use_id,
                    "message": {"id": f"{use_id}_m{k}", "content": uses},
                }
            )
            blocks = [
                await self.call(t, a, u["id"], agent) for (t, a), u in zip(turn, uses, strict=True)
            ]
            emit({"type": "user", "parent_tool_use_id": use_id, "message": {"content": blocks}})
        report = spec.get("report", "Done.")
        emit(
            {
                "type": "assistant",
                "parent_tool_use_id": use_id,
                "message": {"id": f"{use_id}_end", "content": [{"type": "text", "text": report}]},
            }
        )
        return {
            "type": "tool_result",
            "tool_use_id": use_id,
            "content": [{"type": "text", "text": report}],
        }

    async def turn(self, i: int, turn: list) -> None:
        uses = []
        for tool, a in turn:
            if tool == "Agent":
                name, shown = "Agent", {"subagent_type": "payer", "prompt": a["prompt"]}
            else:
                name, shown = f"mcp__{self.server(tool)}__{tool}", a
            uses.append({"type": "tool_use", "id": self.new_id(), "name": name, "input": shown})
        emit({"type": "assistant", "message": {"id": f"m{i}", "content": uses}})
        if self.native:
            blocks = []
            for (tool, a), u in zip(turn, uses, strict=True):
                if tool == "Agent":
                    blocks.append(await self.subagent(u["id"], a))
                else:
                    blocks.append(await self.call(tool, a, u["id"], None))
        else:
            blocks = await asyncio.gather(
                *(
                    self.call(tool, a, u["id"], None)
                    for (tool, a), u in zip(turn, uses, strict=True)
                )
            )
        emit({"type": "user", "message": {"content": list(blocks)}})


async def main() -> None:
    args = sys.argv[1:]
    servers = json.loads(flag(args, "--mcp-config"))["mcpServers"]
    goal = sys.stdin.read()
    plan = json.loads(os.environ["FAKE_CLAUDE_PLAN"])
    emit({"type": "system", "subtype": "init", "model": "fake", "tools": [], "cwd": os.getcwd()})
    if limit_reached():
        info = {"status": "rejected", "resetsAt": 1795572000, "rateLimitType": "five_hour"}
        emit({"type": "rate_limit_event", "rate_limit_info": info})
        emit({"type": "result", "is_error": True, "result": "You've hit your limit"})
        return
    async with httpx2.AsyncClient() as http:
        clients = {}
        stack = []
        for name, server in servers.items():
            cm = Client(streamable_http_client(server["url"], http_client=http))
            clients[name] = await cm.__aenter__()
            stack.append(cm)
        fake = Fake(args, clients)
        for i, turn in enumerate(plan, 1):
            if isinstance(turn, str):
                content = [{"type": "text", "text": turn}]
                emit({"type": "assistant", "message": {"id": f"m{i}", "content": content}})
                break
            await fake.turn(i, turn)
        for cm in reversed(stack):
            await cm.__aexit__(None, None, None)
    usage = {"input_tokens": 10, "cache_read_input_tokens": 1000, "output_tokens": 50}
    emit(
        {
            "type": "result",
            "is_error": False,
            "result": goal[:20],
            "usage": usage,
            "total_cost_usd": 0.05,
            "num_turns": len(plan),
        }
    )


asyncio.run(main())
