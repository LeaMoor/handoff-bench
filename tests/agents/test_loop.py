import pytest
from helpers.fake_model import FakeModel

from handoff_bench.agents.call_journal import CallJournal
from handoff_bench.agents.gateway import CallHook, PendingCall, ToolGateway
from handoff_bench.agents.loop import AgentSpec, ModelCallLog, rebuild_requests, run_agent
from handoff_bench.agents.model_client import ModelCallFailed
from handoff_bench.agents.tools import InfraError, ToolResult
from handoff_bench.jsonl import JsonlWriter

pytestmark = pytest.mark.anyio


def spec(max_steps: int = 5) -> AgentSpec:
    return AgentSpec(
        actor="agent",
        system_prompt="sys",
        max_steps=max_steps,
        tools=gateway().openai_tools(["echo", "finish"]),
        finish_tools=frozenset({"finish"}),
    )


def gateway() -> ToolGateway:
    gw = ToolGateway(episode_id="ep", journal=CallJournal())

    async def echo(actor: str, args: dict) -> dict:
        return {"echo": args}

    async def finish(actor: str, args: dict) -> dict:
        return {"accepted": True}

    gw.register("echo", echo, description="Echo back.")
    gw.register("finish", finish, description="Finish.")
    return gw


async def test_runs_until_finish_and_logs_everything() -> None:
    model = FakeModel([[("echo", {"x": 1})], [("finish", {"status": "done"})]])
    log = JsonlWriter[ModelCallLog]()
    gw = gateway()
    trace = await run_agent(spec(), "goal", model=model, gateway=gw, log=log)

    assert (trace.stop_reason, trace.steps, trace.finish_args) == ("finish", 2, {"status": "done"})
    assert trace.usage.cost == pytest.approx(0.002)
    assert [r.step for r in log.rows] == [1, 2]
    assert [r.tool for r in gw.journal.rows] == ["echo", "finish"]
    assert model.requests[1][-1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": '{"echo": {"x": 1}}',
    }
    assert model.requests[1][-2]["reasoning_details"] == [
        {"type": "reasoning.encrypted", "data": "opaque"}
    ]


async def test_stops_at_max_steps() -> None:
    model = FakeModel([[("echo", {})]] * 3)
    trace = await run_agent(spec(max_steps=3), "goal", model=model, gateway=gateway())
    assert (trace.stop_reason, trace.steps) == ("max_steps", 3)


async def test_text_only_answer_stops() -> None:
    trace = await run_agent(spec(), "goal", model=FakeModel(["I am done."]), gateway=gateway())
    assert trace.stop_reason == "text_only"
    assert trace.messages[-1] == {"role": "assistant", "content": "I am done."}


async def test_tool_error_goes_back_to_model_and_loop_continues() -> None:
    model = FakeModel([[("nope", {})], [("finish", {})]])
    trace = await run_agent(spec(), "goal", model=model, gateway=gateway())

    assert trace.stop_reason == "finish"
    assert model.requests[1][-1]["content"] == "Error (unknown_tool): No tool named nope."


async def test_tool_outside_agent_spec_is_rejected_even_if_gateway_has_it() -> None:
    gw = gateway()
    called = []

    async def secret(actor: str, args: dict) -> dict:
        called.append(args)
        return {}

    gw.register("secret", secret)
    model = FakeModel([[("secret", {})], [("finish", {})]])
    trace = await run_agent(spec(), "goal", model=model, gateway=gw)

    assert trace.stop_reason == "finish"
    assert called == []
    assert model.requests[1][-1]["content"] == "Error (unknown_tool): No tool named secret."
    assert (gw.journal.rows[0].tool, gw.journal.rows[0].error_code) == ("secret", "unknown_tool")


async def test_invalid_json_arguments_are_journaled_not_executed() -> None:
    gw = gateway()
    model = FakeModel([[("echo", "{not json")], [("finish", {})]])
    await run_agent(spec(), "goal", model=model, gateway=gw)

    row = gw.journal.rows[0]
    assert (row.tool, row.outcome, row.error_code) == ("echo", "error", "invalid_arguments")
    assert row.args == {"_raw": "{not json"}


async def test_dropped_call_stops_agent_immediately() -> None:
    class DropEcho(CallHook):
        async def before_call(self, call: PendingCall) -> ToolResult | None:
            return ToolResult(outcome="dropped") if call.tool == "echo" else None

    gw = gateway()
    gw.hooks.append(DropEcho())
    model = FakeModel([[("echo", {}), ("finish", {})]])
    trace = await run_agent(spec(), "goal", model=model, gateway=gw)

    assert trace.stop_reason == "dropped"
    assert [r.tool for r in gw.journal.rows] == ["echo"]


async def test_api_failure_after_retries_is_marked() -> None:
    class Broken:
        async def complete(self, messages, tools):
            raise ModelCallFailed(3, RuntimeError("503"))

    log = JsonlWriter[ModelCallLog]()
    trace = await run_agent(spec(), "goal", model=Broken(), gateway=gateway(), log=log)

    assert (trace.stop_reason, trace.retries) == ("api_error", 3)
    assert log.rows[0].error == "RuntimeError('503')"


async def test_non_retryable_api_error_also_ends_cleanly() -> None:
    class BadRequest:
        async def complete(self, messages, tools):
            raise ModelCallFailed(0, ValueError("400 bad tool schema"), retryable=False)

    trace = await run_agent(spec(), "goal", model=BadRequest(), gateway=gateway())
    assert (trace.stop_reason, trace.error) == ("api_error", "ValueError('400 bad tool schema')")


async def test_infra_error_stops_agent_and_is_not_shown_to_model() -> None:
    gw = gateway()

    async def bank_down(actor: str, args: dict) -> None:
        raise InfraError("transport_error", "connection refused")

    gw.register("pay", bank_down)
    model = FakeModel([[("pay", {})], [("finish", {})]])
    pay_spec = spec().model_copy(update={"tools": gw.openai_tools(["pay", "finish"])})
    trace = await run_agent(pay_spec, "goal", model=model, gateway=gw)

    assert (trace.stop_reason, trace.steps) == ("infra_error", 1)
    assert len(model.requests) == 1
    assert gw.journal.rows[0].outcome == "infra_error"


async def test_log_stores_only_new_messages_and_rebuilds_requests() -> None:
    model = FakeModel([[("echo", {"x": 1})], [("echo", {"x": 2})], [("finish", {})]])
    log = JsonlWriter[ModelCallLog]()
    await run_agent(spec(), "goal", model=model, gateway=gateway(), log=log)

    assert [len(r.new_messages) for r in log.rows] == [2, 2, 2]
    rebuilt = rebuild_requests(log.rows)
    assert [rebuilt[("agent", s)] for s in (1, 2, 3)] == model.requests


async def test_trace_is_a_snapshot_not_the_live_history() -> None:
    class Capturing(FakeModel):
        async def complete(self, messages, tools):
            self.live = messages
            return await super().complete(messages, tools)

    model = Capturing([[("finish", {})]])
    trace = await run_agent(spec(), "goal", model=model, gateway=gateway())
    trace.messages[0]["content"] = "changed"

    assert model.live[0]["content"] == "sys"
