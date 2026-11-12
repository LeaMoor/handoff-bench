from contextlib import AsyncExitStack
from typing import Any, Self

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent

from handoff_bench.agents.tools import InfraError, ToolResult, ToolSpec, error_result
from handoff_bench.billing.protocol import ACTOR_HEADER, parse_error

_DISCOVERY_ACTOR = "gateway"


class McpBackend:
    def __init__(self, url: str) -> None:
        self.url = url
        self._stack = AsyncExitStack()
        self._clients: dict[str, Client] = {}
        self._specs: list[ToolSpec] = []

    async def __aenter__(self) -> Self:
        await self._stack.__aenter__()
        try:
            client = await self._client(_DISCOVERY_ACTOR)
            tools = (await client.list_tools()).tools
        except InfraError:
            await self._stack.aclose()
            raise
        except Exception as e:
            await self._stack.aclose()
            raise InfraError("transport_error", f"cannot list tools at {self.url}: {e!r}") from e
        self._specs = [
            ToolSpec(name=t.name, description=t.description or "", parameters=t.input_schema)
            for t in tools
        ]
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._clients.clear()
        await self._stack.__aexit__(*exc)

    def specs(self) -> list[ToolSpec]:
        return list(self._specs)

    async def call(self, actor: str, tool: str, args: dict[str, Any]) -> ToolResult:
        try:
            client = await self._client(actor)
            raw = await client.call_tool(tool, args)
        except InfraError:
            raise
        except Exception as e:
            raise InfraError("transport_error", f"{tool} via {self.url}: {e!r}") from e
        return parse_result(tool, raw)

    async def _client(self, actor: str) -> Client:
        if actor not in self._clients:
            try:
                http = await self._stack.enter_async_context(
                    httpx2.AsyncClient(headers={ACTOR_HEADER: actor})
                )
                transport = streamable_http_client(self.url, http_client=http)
                self._clients[actor] = await self._stack.enter_async_context(Client(transport))
            except Exception as e:
                raise InfraError("transport_error", f"cannot connect to {self.url}: {e!r}") from e
        return self._clients[actor]


def parse_result(tool: str, raw: CallToolResult) -> ToolResult:
    if raw.is_error:
        text = "".join(c.text for c in raw.content if isinstance(c, TextContent))
        parsed = parse_error(tool, text)
        if parsed is None:
            return error_result("bank_error", text)
        return error_result(*parsed)
    data = raw.structured_content
    if isinstance(data, dict) and data.keys() == {"result"}:
        data = data["result"]
    return ToolResult(outcome="ok", data=data)
