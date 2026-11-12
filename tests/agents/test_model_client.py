import pytest

from handoff_bench.agents.model_client import (
    ModelCallFailed,
    ModelSettings,
    OpenRouterClient,
    parse_completion,
    with_retries,
)

RAW = {
    "id": "gen-1",
    "model": "qwen/qwen3.7-flash",
    "provider": "Alibaba",
    "choices": [
        {
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": None,
                "reasoning": "need invoice first",
                "reasoning_details": [{"type": "reasoning.text", "text": "need invoice first"}],
                "tool_calls": [
                    {
                        "id": "call_a",
                        "type": "function",
                        "function": {"name": "get_invoice", "arguments": '{"invoice_id":"INV-12"}'},
                    }
                ],
            },
        }
    ],
    "usage": {
        "prompt_tokens": 900,
        "completion_tokens": 60,
        "completion_tokens_details": {"reasoning_tokens": 40},
        "prompt_tokens_details": {"cached_tokens": 512},
        "cost": 0.0000348,
    },
}


def test_parse_completion() -> None:
    resp = parse_completion(RAW, retries=1, latency_s=0.5)

    assert [(c.id, c.name) for c in resp.tool_calls] == [("call_a", "get_invoice")]
    assert resp.message["reasoning_details"] == RAW["choices"][0]["message"]["reasoning_details"]
    assert "content" not in resp.message
    assert (resp.usage.reasoning_tokens, resp.usage.cached_tokens) == (40, 512)
    assert resp.usage.cost == pytest.approx(0.0000348)
    assert (resp.provider, resp.retries) == ("Alibaba", 1)


def test_request_pins_provider_and_skips_unset_params() -> None:
    client = OpenRouterClient(ModelSettings(model="m", provider="alibaba", seed=7), api_key="test")
    params = client.request_params([{"role": "user", "content": "hi"}], [])

    assert params["extra_body"]["provider"] == {"order": ["alibaba"], "allow_fallbacks": False}
    assert params["extra_body"]["usage"] == {"include": True}
    assert params["seed"] == 7
    assert "temperature" not in params and "reasoning" not in params["extra_body"]


def test_missing_api_key_is_clear_error(monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        OpenRouterClient(ModelSettings(model="m", provider="p"))


class Flaky(Exception):
    pass


@pytest.mark.anyio
async def test_with_retries_counts_retries() -> None:
    calls = iter([Flaky(), Flaky(), "ok"])

    async def fn() -> str:
        item = next(calls)
        if isinstance(item, Exception):
            raise item
        return item

    assert await with_retries(fn, retryable=(Flaky,), base_delay=0) == ("ok", 2)


@pytest.mark.anyio
async def test_with_retries_gives_up_after_three() -> None:
    async def fn() -> None:
        raise Flaky()

    with pytest.raises(ModelCallFailed) as exc:
        await with_retries(fn, retryable=(Flaky,), base_delay=0)
    assert exc.value.retries == 3


@pytest.mark.anyio
async def test_non_retryable_error_is_not_retried() -> None:
    attempts = 0

    async def fn() -> None:
        nonlocal attempts
        attempts += 1
        raise ValueError("400")

    with pytest.raises(ValueError):
        await with_retries(fn, retryable=(Flaky,), base_delay=0)
    assert attempts == 1


@pytest.mark.anyio
async def test_fatal_api_error_becomes_model_call_failed() -> None:
    import httpx2
    import openai

    request = httpx2.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx2.Response(400, request=request, json={"error": {"message": "bad schema"}})

    async def fn() -> None:
        raise openai.BadRequestError("bad schema", response=response, body=None)

    with pytest.raises(ModelCallFailed) as exc:
        await with_retries(fn, base_delay=0)
    assert (exc.value.retries, exc.value.retryable) == (0, False)


def test_model_seed_is_not_sent_by_default() -> None:
    client = OpenRouterClient(ModelSettings(model="m", provider="p"), api_key="test")
    assert "seed" not in client.request_params([], [])
