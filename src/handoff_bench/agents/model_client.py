"""Провайдер закреплён: иначе OpenRouter раскидает запросы по хостерам с разной квантизацией.
`reasoning_details` надо возвращать в историю без изменений."""

import os
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import anyio
import openai
from pydantic import BaseModel, ConfigDict

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MAX_RETRIES = 3


class ModelSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    provider: str
    temperature: float | None = None  # Haiku 5.5 не принимает temperature
    seed: int | None = None  # сид модели, не сид мира
    reasoning_effort: str | None = None
    max_tokens: int | None = None


class ToolCall(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    arguments: str


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    cost: float = 0.0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(**{k: getattr(self, k) + getattr(other, k) for k in Usage.model_fields})


class ModelResponse(BaseModel):
    message: dict[str, Any]
    tool_calls: list[ToolCall]
    text: str | None
    finish_reason: str | None
    usage: Usage
    model: str | None
    provider: str | None
    retries: int
    latency_s: float
    raw: dict[str, Any]


class ModelCallFailed(Exception):
    def __init__(self, retries: int, error: Exception, *, retryable: bool = True) -> None:
        super().__init__(f"model call failed after {retries} retries: {error!r}")
        self.retries = retries
        self.error = error
        self.retryable = retryable


class ModelClient(Protocol):
    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelResponse: ...


RETRYABLE = (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError)
FATAL = (openai.APIError,)


async def with_retries[T](
    fn: Callable[[], Awaitable[T]],
    *,
    retryable: tuple[type[Exception], ...] = RETRYABLE,
    fatal: tuple[type[Exception], ...] = FATAL,
    max_retries: int = MAX_RETRIES,
    base_delay: float = 1.0,
) -> tuple[T, int]:
    retries = 0
    while True:
        try:
            return await fn(), retries
        except retryable as e:
            if retries >= max_retries:
                raise ModelCallFailed(retries, e) from e
            await anyio.sleep(base_delay * 2**retries)
            retries += 1
        except fatal as e:
            raise ModelCallFailed(retries, e, retryable=False) from e


class EmptyResponse(Exception):
    pass


class OpenRouterClient:
    def __init__(
        self,
        settings: ModelSettings,
        *,
        api_key: str | None = None,
        base_url: str = OPENROUTER_BASE_URL,
        retry_delay: float = 1.0,
    ) -> None:
        key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY is not set (put it into .env)")
        self.settings = settings
        self.retry_delay = retry_delay
        self._client = openai.AsyncOpenAI(api_key=key, base_url=base_url, max_retries=0)

    def request_params(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        s = self.settings
        extra_body: dict[str, Any] = {
            "provider": {"order": [s.provider], "allow_fallbacks": False},
            "usage": {"include": True},
        }
        if s.reasoning_effort is not None:
            extra_body["reasoning"] = {"effort": s.reasoning_effort}
        params: dict[str, Any] = {
            "model": s.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "extra_body": extra_body,
        }
        for name in ("temperature", "seed", "max_tokens"):
            value = getattr(s, name)
            if value is not None:
                params[name] = value
        return params

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelResponse:
        params = self.request_params(messages, tools)

        async def once() -> Any:
            resp = await self._client.chat.completions.create(**params)
            if not resp.choices:
                raise EmptyResponse(str(resp.model_dump()))
            return resp

        started = time.monotonic()
        resp, retries = await with_retries(
            once, retryable=(*RETRYABLE, EmptyResponse), base_delay=self.retry_delay
        )
        return parse_completion(resp.model_dump(), retries, time.monotonic() - started)


def parse_completion(raw: dict[str, Any], retries: int, latency_s: float) -> ModelResponse:
    choice = raw["choices"][0]
    msg = choice["message"]
    tool_calls = [
        ToolCall(id=tc["id"], name=tc["function"]["name"], arguments=tc["function"]["arguments"])
        for tc in msg.get("tool_calls") or []
    ]
    message = {k: v for k, v in msg.items() if v is not None}
    message["role"] = "assistant"

    u = raw.get("usage") or {}
    usage = Usage(
        prompt_tokens=u.get("prompt_tokens") or 0,
        completion_tokens=u.get("completion_tokens") or 0,
        reasoning_tokens=(u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0,
        cached_tokens=(u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0,
        cost=u.get("cost") or 0.0,
    )
    return ModelResponse(
        message=message,
        tool_calls=tool_calls,
        text=msg.get("content"),
        finish_reason=choice.get("finish_reason"),
        usage=usage,
        model=raw.get("model"),
        provider=raw.get("provider"),
        retries=retries,
        latency_s=latency_s,
        raw=raw,
    )
