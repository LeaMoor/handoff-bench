"""An episode with Claude Code's own subagents (native cells, 86-93): the boundary is the harness's.

Claude Code starts its subagents with its Task tool; they reach our bank directly over MCP. Only
the bank fails (a timeout, the charge invisible to checks), and nothing repeats the work but the
orchestrator itself. Our hook (`harness/hook.py`) logs every call, keeps the bank's write tools
from the orchestrator and sets the key. `calls.jsonl` is rebuilt from Claude Code's stream, in
the same format as our loop's, so the grader, the coder and `show.py` read it unchanged.
"""

import json
import shlex
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from handoff_bench.agents.call_journal import CallJournal, CallRow
from handoff_bench.agents.delegation import NATIVE_SUBAGENT_TYPE, register_finish
from handoff_bench.agents.gateway import ToolGateway
from handoff_bench.agents.loop import AgentSpec, AgentTrace, ModelCallLog
from handoff_bench.agents.model_client import Usage
from handoff_bench.agents.tools import Outcome
from handoff_bench.billing import BillingService, EffectJournal
from handoff_bench.billing.protocol import parse_error
from handoff_bench.billing.server import running_server
from handoff_bench.faults.setup import FAULTS, with_delay
from handoff_bench.harness.claude_code import (
    STREAM_FILE,
    SUBAGENT_TOOLS,
    ClaudeCodeOrchestrator,
    NativeSetup,
    _text,
    to_messages,
    tool_call,
)
from handoff_bench.harness.hook import (
    BANK_PREFIX,
    HOOKS_FILE,
    POLICY_FILE,
    SUBAGENT_BANK,
    HookPolicy,
)
from handoff_bench.jsonl import JsonlWriter
from handoff_bench.runner.episode import ORCHESTRATOR, EpisodeConfig, EpisodeResult
from handoff_bench.worlds import World

HOOK_EVENTS = ("PreToolUse", "SubagentStart", "SubagentStop")
SUBAGENT_DESCRIPTION = "Has access to the billing system: give it a goal, it returns its report."


async def run_native_episode(
    world: World, config: EpisodeConfig, *, episode_dir: Path
) -> EpisodeResult:
    settings = config.orchestrator
    if settings is None:
        raise ValueError(f"cell {config.cell} needs a harness as the orchestrator")
    agents = config.agents
    fault = with_delay(FAULTS[config.fault](world), config.delay, world)
    episode_dir.mkdir(parents=True, exist_ok=True)
    (episode_dir / "config.json").write_text(
        json.dumps(
            {"episode": config.model_dump(mode="json"), "bank": fault.bank.model_dump(mode="json")},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    service = BillingService(
        episode_id=config.episode_id,
        config=fault.bank,
        journal=EffectJournal(episode_dir / "effects.jsonl"),
        db_path=episode_dir / "bank.sqlite",
        id_seed=config.seed,
    )
    service.add_invoices(world.invoices)

    bank_tools = [t for t in agents.orchestrator_tools if t != "finish"]
    policy = HookPolicy(
        log=(episode_dir / HOOKS_FILE).resolve(),
        parent_bank_tools=tuple(bank_tools),
        key=config.key,
    )
    policy_path = episode_dir / POLICY_FILE
    policy_path.write_text(policy.model_dump_json(indent=2), encoding="utf-8")
    hook = f"{shlex.quote(sys.executable)} -m handoff_bench.harness.hook "
    hook += shlex.quote(str(policy_path.resolve()))
    hooks = {
        "hooks": {
            ev: [{"matcher": "", "hooks": [{"type": "command", "command": hook}]}]
            for ev in HOOK_EVENTS
        }
    }
    sub_tools = [f"mcp__{SUBAGENT_BANK}__{t}" for t in agents.subagent_tools]
    # Only `finish` goes through our gateway; its journal stays apart from the rebuilt one.
    gateway = ToolGateway(
        episode_id=config.episode_id, journal=CallJournal(episode_dir / "stand_calls.jsonl")
    )
    register_finish(gateway)
    spec = AgentSpec(
        actor=ORCHESTRATOR,
        system_prompt=agents.orchestrator_prompt,
        max_steps=agents.orchestrator_max_steps,
        tools=gateway.openai_tools(["finish"]),
        finish_tools=frozenset({"finish"}),
    )
    with ExitStack() as stack:
        stack.enter_context(service)
        sub_url = stack.enter_context(running_server(service, tools=agents.subagent_tools))
        bank_url = (
            stack.enter_context(running_server(service, tools=bank_tools)) if bank_tools else None
        )
        subagent = {
            NATIVE_SUBAGENT_TYPE: {
                "description": SUBAGENT_DESCRIPTION,
                "prompt": agents.subagent_prompt,
                "tools": sub_tools,
                "model": settings.subagent_model,
                "mcpServers": [{SUBAGENT_BANK: {"type": "http", "url": sub_url}}],
            }
        }
        native = NativeSetup(
            bank_url=bank_url,
            agents=subagent,
            settings=hooks,
            allowed=sorted({*sub_tools, *(BANK_PREFIX + t for t in bank_tools)}),
        )
        trace = await ClaudeCodeOrchestrator(settings, native).run(
            spec, world.task, gateway=gateway, episode_dir=episode_dir
        )

    events = _read_jsonl(episode_dir / STREAM_FILE)
    denied = {
        e.get("tool_use_id")
        for e in _read_jsonl(episode_dir / HOOKS_FILE)
        if (e.get("decision") or {}).get("hookSpecificOutput", {}).get("permissionDecision")
        == "deny"
    }
    rows = native_calls(config.episode_id, events, denied)
    journal = CallJournal(episode_dir / "calls.jsonl")
    for row in rows:
        journal.write(row)
    subagents = native_subagents(events, agents.subagent_prompt)
    JsonlWriter[ModelCallLog](episode_dir / "model_calls.jsonl")  # none: Claude Code ran them
    (episode_dir / "trace.json").write_text(
        json.dumps(
            {
                "orchestrator": trace.model_dump(mode="json"),
                "subagents": [s.model_dump(mode="json") for s in subagents],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    u = trace.usage
    return EpisodeResult(
        episode_dir=episode_dir,
        stop_reason=trace.stop_reason,
        steps=trace.steps,
        delegations=len(subagents),
        orchestrator_responses=[
            len(m["tool_calls"]) for m in trace.messages if m.get("tool_calls")
        ],
        fault_fired=any(r.outcome == "timeout" for r in rows),
        finish_args=trace.finish_args,
        final_text=trace.messages[-1].get("content") if trace.stop_reason == "text_only" else None,
        cost=u.cost,
        prompt_tokens=u.prompt_tokens,
        completion_tokens=u.completion_tokens,
        reasoning_tokens=u.reasoning_tokens,
        cached_tokens=u.cached_tokens,
        retries=0,
        error=trace.error,
    )


def native_calls(episode_id: str, events: list[dict[str, Any]], denied: set[Any]) -> list[CallRow]:
    """Every tool call of the orchestrator and its subagents, in the order the stream shows them.

    A subagent is `sub-N` by the order of the subagent calls that started it. The arguments are
    what the model sent; the bank saw the hook's version (its effects journal keeps that).
    """
    subs: dict[str, str] = {}
    pending: dict[str, tuple[int, str, str, dict[str, Any]]] = {}
    rows: list[CallRow] = []
    seq = 0
    for event in events:
        msg = event.get("message") or {}
        parent = event.get("parent_tool_use_id") or None
        actor = ORCHESTRATOR if parent is None else subs.get(parent, "sub-?")
        for block in msg.get("content") or []:
            if not isinstance(block, dict):
                continue
            if event.get("type") == "assistant" and block.get("type") == "tool_use":
                seq += 1
                if parent is None and block.get("name") in SUBAGENT_TOOLS:
                    subs[block["id"]] = f"sub-{len(subs) + 1}"
                name, args = tool_call(block.get("name", ""), block.get("input") or {})
                pending[block["id"]] = (seq, actor, name, args)
            elif event.get("type") == "user" and block.get("type") == "tool_result":
                call = pending.pop(block.get("tool_use_id"), None)
                if call is None:
                    continue
                rows.append(
                    _row(episode_id, *call, block, denied=block.get("tool_use_id") in denied)
                )
    return sorted(rows, key=lambda r: r.seq)


def _row(
    episode_id: str,
    seq: int,
    actor: str,
    tool: str,
    args: dict[str, Any],
    block: dict[str, Any],
    *,
    denied: bool,
) -> CallRow:
    text = _text(block.get("content"))
    outcome: Outcome = "ok"
    result: Any = None
    code = message = None
    if denied:
        outcome, code, message = "rejected", "unknown_tool", text
    elif block.get("is_error"):
        code, message = parse_error(tool, text) or ("bank_error", text)
        outcome = "timeout" if code == "timeout" else "error"
    else:
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            result = text
        if isinstance(result, dict) and result.keys() == {"result"}:
            result = result["result"]
    return CallRow(
        episode_id=episode_id,
        seq=seq,
        actor=actor,
        tool=tool,
        args=args,
        outcome=outcome,
        result=result,
        error_code=code,
        error_message=message,
    )


def native_subagents(events: list[dict[str, Any]], system_prompt: str) -> list[AgentTrace]:
    """One trace per subagent Claude Code started, in our loop's message format."""
    out = []
    for event in events:
        if event.get("type") != "assistant" or event.get("parent_tool_use_id"):
            continue
        for block in (event.get("message") or {}).get("content") or []:
            if block.get("type") == "tool_use" and block.get("name") in SUBAGENT_TOOLS:
                goal = (block.get("input") or {}).get("prompt", "")
                messages = to_messages(system_prompt, goal, events, parent=block["id"])
                out.append(
                    AgentTrace(
                        actor=f"sub-{len(out) + 1}",
                        stop_reason="text_only",
                        steps=sum(1 for m in messages if m["role"] == "assistant"),
                        messages=messages,
                        usage=Usage(),  # counted in the orchestrator's total
                        retries=0,
                    )
                )
    return out


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
