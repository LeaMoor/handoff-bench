"""Run Codex native subagents against the simulated bank."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import secrets
import shlex
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from mcp.server.lowlevel import Server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool
from pydantic import BaseModel, ConfigDict, Field

from handoff_bench.agents.call_journal import CallJournal
from handoff_bench.agents.delegation import register_finish
from handoff_bench.agents.gateway import ToolGateway
from handoff_bench.agents.loop import AgentTrace, ModelCallLog, render_tool_result
from handoff_bench.agents.mcp_backend import McpBackend
from handoff_bench.agents.tools import StopReason
from handoff_bench.billing import BillingService, EffectJournal
from handoff_bench.billing.server import running_server
from handoff_bench.faults.setup import FAULTS, with_delay
from handoff_bench.harness.stand_mcp import serving
from handoff_bench.jsonl import JsonlWriter
from handoff_bench.keys import KeyHook

if TYPE_CHECKING:
    from handoff_bench.runner.episode import EpisodeConfig, EpisodeResult
    from handoff_bench.worlds import World


class CodexSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    harness: Literal["codex"]
    model: str = "gpt-6.2-sol"
    subagent_model: str = "gpt-6-luna"
    reasoning_effort: str = "medium"
    binary: str = "codex"
    timeout_s: float = Field(default=420, gt=0)

    @property
    def label(self) -> str:
        return f"codex:{self.model}"


LIMIT = re.compile(
    r"usage limit|rate[_ -]limit(?:ed| exceeded| reached)?|hit your .* limit|too many requests",
    re.IGNORECASE,
)


def read_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def stamp(secret: bytes, actor: str) -> str:
    return f"{actor}:{hmac.digest(secret, actor.encode(), 'sha256').hex()}"


def hook(policy: Path) -> None:
    cfg = json.loads(policy.read_text())
    log = Path(cfg["log"])
    event = json.load(sys.stdin)
    kind = event["hook_event_name"]
    out = {}
    decision = None
    if kind == "PreToolUse":
        children = {
            e["agent_id"]
            for e in read_events(log)
            if e["hook_event_name"] == "SubagentStart" and e.get("agent_id")
        }
        identity = event.get("agent_id")
        name = event["tool_name"].split("__")[-1]
        if identity and identity not in children:
            decision = "unattributed"
        elif (name == "create_charge" and not identity) or (name == "finish" and identity):
            decision = "wrong_role"
        if decision:
            out = {"permissionDecision": "deny", "permissionDecisionReason": "Tool unavailable."}
        else:
            actor = f"sub-{identity}" if identity else "orchestrator"
            args = dict(event["tool_input"])
            args["_hb_actor"] = stamp(bytes.fromhex(cfg["secret"]), actor)
            out = {"permissionDecision": "allow", "updatedInput": args}
        out = {"hookSpecificOutput": {"hookEventName": kind, **out}}
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({**event, "stand_denial": decision}) + "\n")
    print(json.dumps(out))


class BankTools:
    def __init__(self, gateway: ToolGateway, secret: bytes, config: EpisodeConfig) -> None:
        from handoff_bench.harness.hermes_native import ActorTools

        agents = config.agents
        self.actors = ActorTools(
            gateway,
            parent_tools=tuple(agents.orchestrator_tools),
            child_tools=tuple(agents.subagent_tools),
            finish_tools=frozenset({"finish"}),
        )
        self.specs = [
            spec["function"]
            for spec in gateway.openai_tools(
                sorted(set(agents.orchestrator_tools) | set(agents.subagent_tools))
            )
        ]
        self.secret = secret
        self.unattributed = False

    def server(self) -> Server:
        async def list_tools(_ctx, _params):
            return ListToolsResult(
                tools=[
                    Tool(name=s["name"], description=s["description"], input_schema=s["parameters"])
                    for s in self.specs
                ]
            )

        async def call(_ctx, params):
            args = dict(params.arguments or {})
            ticket = str(args.pop("_hb_actor", ""))
            actor = ticket.partition(":")[0]
            if not actor or not hmac.compare_digest(ticket, stamp(self.secret, actor)):
                self.unattributed = True
                return CallToolResult(
                    content=[TextContent(type="text", text="Missing stand actor attribution.")],
                    is_error=True,
                )
            result = await self.actors.call(actor, params.name, args)
            return CallToolResult(
                content=[TextContent(type="text", text=render_tool_result(result))],
                is_error=result.outcome != "ok",
            )

        return Server("stand", on_list_tools=list_tools, on_call_tool=call)


def config_args(command: str, url: str, settings: CodexSettings) -> list[str]:
    handler = '{type="command", command=' + json.dumps(command) + "}"
    return [
        "-c",
        f"agents.default_subagent_model={json.dumps(settings.subagent_model)}",
        "-c",
        "agents.max_depth=1",
        "-c",
        f"model_reasoning_effort={json.dumps(settings.reasoning_effort)}",
        "-c",
        'model_provider="openai"',
        "-c",
        'forced_login_method="chatgpt"',
        "-c",
        f"mcp_servers.stand.url={json.dumps(url)}",
        "-c",
        'mcp_servers.stand.default_tools_approval_mode="approve"',
        "-c",
        "mcp_servers.stand.required=true",
        "-c",
        'web_search="disabled"',
        "-c",
        f"hooks.SubagentStart=[{{hooks=[{handler}]}}]",
        "-c",
        f"hooks.PostModelRequest=[{{hooks=[{handler}]}}]",
        "-c",
        f'hooks.PreToolUse=[{{matcher="^mcp__stand__",hooks=[{handler}]}}]',
    ]


async def run_codex_native_episode(
    world: World, config: EpisodeConfig, *, episode_dir: Path
) -> EpisodeResult:
    from handoff_bench.harness.claude_code import UsageLimitReached
    from handoff_bench.runner.episode import EpisodeResult

    settings = config.orchestrator
    if not isinstance(settings, CodexSettings):
        raise ValueError(f"cell {config.cell} needs Codex as the orchestrator")
    episode_dir.mkdir(parents=True, exist_ok=True)
    out = episode_dir.resolve()
    fault = with_delay(FAULTS[config.fault](world), config.delay, world)
    (out / "config.json").write_text(
        json.dumps(
            {
                "episode": config.model_dump(mode="json"),
                "bank": fault.bank.model_dump(mode="json"),
            },
            indent=2,
        )
    )
    service = BillingService(
        episode_id=config.episode_id,
        config=fault.bank,
        journal=EffectJournal(out / "effects.jsonl"),
        db_path=out / "bank.sqlite",
        id_seed=config.seed,
    )
    service.add_invoices(world.invoices)
    secret = secrets.token_bytes(32)
    hook_log = out / "hooks.jsonl"
    with tempfile.TemporaryDirectory(prefix="handoff-codex-") as tmp, service:
        policy = Path(tmp) / "policy.json"
        policy.write_text(json.dumps({"secret": secret.hex(), "log": str(hook_log)}))
        cmd = shlex.join([sys.executable, str(Path(__file__).resolve()), str(policy)])
        with running_server(service) as bank:
            async with McpBackend(bank) as backend:
                gateway = ToolGateway(
                    episode_id=config.episode_id,
                    journal=CallJournal(out / "calls.jsonl"),
                    backends=[backend],
                    hooks=[KeyHook(config.key, {})],
                )
                register_finish(gateway)
                tools = BankTools(gateway, secret, config)
                async with serving(server=tools.server()) as url:
                    process = asyncio.create_task(
                        run_process(
                            settings,
                            config.agents.orchestrator_prompt,
                            world.task,
                            tmp,
                            cmd,
                            url,
                            out,
                        )
                    )
                    await tools.actors.serve(until=process)
                    code = await process

    events = read_events(hook_log)
    stream = read_events(out / "codex_stream.jsonl")
    usage_events = [e for e in events if e["hook_event_name"] == "PostModelRequest"]
    errors = [str(e.get("error")) for e in usage_events if e.get("error")] + [
        str(e.get("message") or e.get("error") or e.get("item", {}).get("message"))
        for e in stream
        if e["type"] in {"error", "turn.failed"} or e.get("item", {}).get("type") == "error"
    ]
    stderr = (out / "codex_stderr.txt").read_text()
    limit = next((e for e in [*errors, *stderr.splitlines()] if LIMIT.search(e)), None)
    if limit and (code != 0 or tools.actors.stop != "finish"):
        raise UsageLimitReached(None, limit[:300])

    models = {
        (bool(e.get("agent_id")), e.get("model"))
        for e in usage_events
        if e.get("status") == "completed"
    }
    stop: StopReason = tools.actors.stop or "text_only"
    error = tools.actors.error
    if tools.unattributed or any(e.get("stand_denial") == "unattributed" for e in events):
        stop, error = "infra_error", "A bank call could not be attributed to an agent."
    elif code == 124:
        stop, error = "infra_error", "Codex exceeded the episode time limit."
    elif code != 0:
        stop, error = "api_error", f"Codex exited with {code}. See the stream and stderr."
    elif (False, settings.model) not in models or models - {
        (False, settings.model),
        (True, settings.subagent_model),
    }:
        stop, error = "infra_error", f"Unexpected Codex models: {sorted(models, key=str)}"
    children = [e for e in events if e["hook_event_name"] == "SubagentStart"]
    if code == 0 and children and (True, settings.subagent_model) not in models:
        stop, error = "infra_error", "Codex started a child with no completed model request."

    # Hook totals include children. The exec stream's turn totals do not.
    usage = [e["token_usage"] for e in usage_events if e.get("token_usage")]
    tokens = {
        "prompt_tokens": sum(u.get("input_tokens", 0) for u in usage),
        "completion_tokens": sum(u.get("output_tokens", 0) for u in usage),
        "reasoning_tokens": sum(u.get("reasoning_output_tokens", 0) for u in usage),
        "cached_tokens": sum(u.get("cached_input_tokens", 0) for u in usage),
    }
    messages = [
        {"role": "system", "content": config.agents.orchestrator_prompt},
        {"role": "user", "content": world.task},
        *(
            {"role": "assistant", "content": e["item"]["text"]}
            for e in stream
            if e["type"] == "item.completed" and e["item"]["type"] == "agent_message"
        ),
    ]
    trace = AgentTrace(
        actor="orchestrator",
        stop_reason=stop,
        finish_tool=tools.actors.finish_tool,
        finish_args=tools.actors.finish_args,
        messages=messages,
        error=error,
        steps=sum(not e.get("agent_id") for e in usage_events),
        retries=0,
        usage=tokens,
    )
    (out / "trace.json").write_text(
        json.dumps(
            {
                "orchestrator": trace.model_dump(mode="json"),
                "subagents": [],
                "codex_children": children,
            },
            indent=2,
        )
    )
    JsonlWriter[ModelCallLog](out / "model_calls.jsonl")
    return EpisodeResult(
        episode_dir=episode_dir,
        stop_reason=stop,
        steps=trace.steps,
        delegations=len(children),
        fault_fired=any(r.outcome == "timeout" for r in gateway.journal.rows),
        finish_args=trace.finish_args,
        error=error,
        cost=0,
        retries=0,
        orchestrator_responses=[],
        final_text=next(
            (m["content"] for m in reversed(messages) if m["role"] == "assistant"), None
        )
        if stop == "text_only"
        else None,
        **tokens,
    )


async def run_process(
    settings: CodexSettings, prompt: str, goal: str, cwd: str, hook_cmd: str, url: str, out: Path
) -> int:
    env = dict(os.environ)
    for name in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT_ID",
        "OPENROUTER_API_KEY",
        "CODEX_API_KEY",
    ):
        env.pop(name, None)
    with (
        (out / "codex_stream.jsonl").open("wb") as stdout,
        (out / "codex_stderr.txt").open("wb") as stderr,
    ):
        proc = await asyncio.create_subprocess_exec(
            settings.binary,
            "exec",
            "--ignore-user-config",
            "--ignore-rules",
            "--dangerously-bypass-hook-trust",
            "--skip-git-repo-check",
            "-C",
            cwd,
            "-s",
            "read-only",
            "--disable",
            "shell_tool",
            "--disable",
            "unified_exec",
            "--disable",
            "apps",
            "--json",
            "-m",
            settings.model,
            *config_args(hook_cmd, url, settings),
            prompt + "\n\n" + goal,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
        )
        try:
            return await asyncio.wait_for(proc.wait(), settings.timeout_s)
        except TimeoutError:
            return 124
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()


if __name__ == "__main__":
    hook(Path(sys.argv[1]))
