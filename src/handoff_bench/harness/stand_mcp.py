"""The orchestrator's tools as an MCP server, so a harness can call them.

Every call goes through the same `ToolGateway` as with our own loop: key hooks, the fault
injector, `delegate` and the call journal do not change. The server only queues the calls; the
episode's own task runs them (`StandTools.serve`), because the bank's MCP clients are opened
lazily and must be entered and closed in one task.
"""

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from mcp.server.lowlevel import Server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from handoff_bench.agents.gateway import ToolGateway
from handoff_bench.agents.loop import render_tool_result
from handoff_bench.agents.tools import InfraError, StopReason, ToolResult

SERVER_NAME = "stand"


class StandTools:
    """What the harness may call, and how its calls ended: `finish`, a dropped call or a stand
    failure stop the episode."""

    def __init__(
        self, gateway: ToolGateway, *, actor: str, tools: list[str], finish_tools: frozenset[str]
    ) -> None:
        self.gateway = gateway
        self.actor = actor
        self.specs = {s["function"]["name"]: s["function"] for s in gateway.openai_tools(tools)}
        self.finish_tools = finish_tools
        self.finish_tool: str | None = None
        self.finish_args: dict[str, Any] | None = None
        self.stop: StopReason | None = None
        self.error: str | None = None
        self.stopped = asyncio.Event()
        self._in_flight = 0
        self._queue: asyncio.Queue[tuple[str, dict[str, Any], asyncio.Future[ToolResult]]] = (
            asyncio.Queue()
        )

    def list_tools(self) -> list[Tool]:
        return [
            Tool(name=name, description=f["description"], input_schema=f["parameters"])
            for name, f in self.specs.items()
        ]

    async def call(self, name: str, args: dict[str, Any] | None) -> ToolResult:
        """Called by the server for each request; waits until `serve` has run it."""
        if self.stop is not None:
            return self._reject(name, args, "episode_over", "The task is finished. Stop now.")
        if name not in self.specs:
            return self._reject(name, args, "unknown_tool", f"No tool named {name}.")
        if not isinstance(args, dict):
            return self._reject(name, args, "invalid_arguments", "Arguments must be an object.")
        if name in self.finish_tools and self._in_flight:
            # Our loop rejects finish next to other calls in one response; here calls of one
            # response arrive concurrently, so finish must not overtake a pending call.
            return self._reject(
                name, args, "finish_not_alone", f"{name} must not run alongside other calls."
            )
        done: asyncio.Future[ToolResult] = asyncio.get_running_loop().create_future()
        self._in_flight += 1
        self._queue.put_nowait((name, args, done))
        try:
            return await done
        finally:
            self._in_flight -= 1

    async def serve(self, until: asyncio.Future[Any]) -> None:
        """Runs queued calls in the calling task until `until` is done."""
        while not until.done():
            get = asyncio.ensure_future(self._queue.get())
            await asyncio.wait({get, until}, return_when=asyncio.FIRST_COMPLETED)
            if not get.done():
                get.cancel()
                break
            name, args, done = get.result()
            result = await self._execute(name, args)
            if not done.done():
                done.set_result(result)
        while not self._queue.empty():  # the harness is gone: nobody reads these
            _, _, done = self._queue.get_nowait()
            done.cancel()

    async def _execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        if self.stop is not None:
            return self._reject(name, args, "episode_over", "The task is finished. Stop now.")
        try:
            result = await self.gateway.call(self.actor, name, args)
        except InfraError as e:
            self._end("infra_error", str(e))
            return ToolResult(outcome="infra_error", error_code=e.code, error_message=e.message)
        if result.outcome == "dropped":
            self._end("dropped")
        elif result.outcome == "ok" and name in self.finish_tools:
            self.finish_tool, self.finish_args = name, args
            self._end("finish")
        return result

    def _reject(self, name: str, args: object, code: str, message: str) -> ToolResult:
        return self.gateway.reject(self.actor, name, repr(args), code, message)

    def _end(self, reason: StopReason, error: str | None = None) -> None:
        self.stop, self.error = reason, error
        self.stopped.set()


def make_server(tools: StandTools) -> Server[Any]:
    async def on_list_tools(ctx: Any, params: Any) -> ListToolsResult:
        return ListToolsResult(tools=tools.list_tools())

    async def on_call_tool(ctx: Any, params: Any) -> CallToolResult:
        result = await tools.call(params.name, params.arguments)
        return CallToolResult(
            content=[TextContent(type="text", text=render_tool_result(result))],
            is_error=result.outcome != "ok",
        )

    return Server(SERVER_NAME, on_list_tools=on_list_tools, on_call_tool=on_call_tool)


@asynccontextmanager
async def serving(tools: StandTools, *, host: str = "127.0.0.1") -> AsyncIterator[str]:
    """Runs the server on this event loop on a free port; yields its MCP URL."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, 0))
    port = sock.getsockname()[1]
    app = make_server(tools).streamable_http_app(streamable_http_path="/mcp", host=host)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        while not server.started:
            if task.done():
                task.result()  # raises the startup error
                raise RuntimeError(f"stand MCP server did not start on port {port}")
            await asyncio.sleep(0.02)
        yield f"http://{host}:{port}/mcp"
    finally:
        server.should_exit = True
        await task
        sock.close()
