import json
from itertools import count
from typing import Any

from handoff_bench.agents.model_client import ModelResponse, ToolCall, Usage

Step = list[tuple[str, dict[str, Any] | str]] | str


class FakeModel:
    def __init__(self, script: list[Step], cost_per_call: float = 0.001) -> None:
        self.script = list(script)
        self.cost = cost_per_call
        self.requests: list[list[dict[str, Any]]] = []
        self.tools: list[list[dict[str, Any]]] = []
        self._ids = count(1)

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelResponse:
        self.requests.append([dict(m) for m in messages])
        self.tools.append(tools)
        step = self.script.pop(0)
        if isinstance(step, str):
            calls: list[ToolCall] = []
            message: dict[str, Any] = {"role": "assistant", "content": step}
        else:
            calls = [
                ToolCall(
                    id=f"call_{next(self._ids)}",
                    name=name,
                    arguments=args if isinstance(args, str) else json.dumps(args),
                )
                for name, args in step
            ]
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": c.arguments},
                    }
                    for c in calls
                ],
                "reasoning_details": [{"type": "reasoning.encrypted", "data": "opaque"}],
            }
        usage = Usage(prompt_tokens=100, completion_tokens=20, cost=self.cost)
        return ModelResponse(
            message=message,
            tool_calls=calls,
            text=message.get("content"),
            finish_reason="tool_calls" if calls else "stop",
            usage=usage,
            model="fake/model",
            provider="fake",
            retries=0,
            latency_s=0.0,
            raw={"fake": True},
        )
