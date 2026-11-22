"""A stand-in for `claude -p --output-format stream-json`: no model, a scripted plan.

`FAKE_CLAUDE_PLAN` is a JSON list of turns, the same for every run; a turn is a list of
`[tool, args]` calls made in one response (concurrently, as Claude Code does), or a string for a
final text answer. The tools are called on the MCP server from `--mcp-config`, and the stream is
printed in Claude Code's format.
"""

import asyncio
import json
import os
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


async def main() -> None:
    args = sys.argv[1:]
    config = json.loads(args[args.index("--mcp-config") + 1])
    name, server = next(iter(config["mcpServers"].items()))
    goal = sys.stdin.read()
    plan = json.loads(os.environ["FAKE_CLAUDE_PLAN"])
    emit({"type": "system", "subtype": "init", "model": "fake", "tools": [], "cwd": os.getcwd()})
    if limit_reached():
        info = {"status": "rejected", "resetsAt": 1795572000, "rateLimitType": "five_hour"}
        emit({"type": "rate_limit_event", "rate_limit_info": info})
        emit({"type": "result", "is_error": True, "result": "You've hit your limit"})
        return
    async with httpx2.AsyncClient() as http:
        async with Client(streamable_http_client(server["url"], http_client=http)) as client:
            for i, turn in enumerate(plan, 1):
                if isinstance(turn, str):
                    content = [{"type": "text", "text": turn}]
                    emit({"type": "assistant", "message": {"id": f"m{i}", "content": content}})
                    break
                uses = [
                    {
                        "type": "tool_use",
                        "id": f"t{i}_{j}",
                        "name": f"mcp__{name}__{tool}",
                        "input": a,
                    }
                    for j, (tool, a) in enumerate(turn)
                ]
                emit({"type": "assistant", "message": {"id": f"m{i}", "content": uses}})
                results = await asyncio.gather(*(client.call_tool(tool, a) for tool, a in turn))
                blocks = [
                    {
                        "type": "tool_result",
                        "tool_use_id": u["id"],
                        "is_error": r.is_error,
                        "content": [
                            {"type": "text", "text": c.text}
                            for c in r.content
                            if isinstance(c, TextContent)
                        ],
                    }
                    for u, r in zip(uses, results, strict=True)
                ]
                emit({"type": "user", "message": {"content": blocks}})
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
