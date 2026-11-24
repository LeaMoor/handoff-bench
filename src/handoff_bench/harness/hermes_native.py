"""An episode with Hermes' own subagents (cells 94-101): the boundary is the harness's.

The analogue of the Claude Code native cells (86-93, `harness/native.py`). Hermes runs in its own
interpreter (`harness/hermes_runner.py`), in-process (`AIAgent`), with a fresh HERMES_HOME per
episode. Its parent starts children with its own `delegate_task`; the child's report returns to
the parent through Hermes, not through us. Our plugin (`harness/hermes_plugin`) gives both the
bank tools and `finish` and sends every call here with its actor (`orchestrator` or `sub-N`).
Here the calls run through the same `ToolGateway` as everywhere: the call journal, the key
policy (`KeyHook`) and the bank. Only the bank fails (a timeout, the charge invisible to checks);
nothing repeats the work but the parent itself.

Tools as in Claude Code B: the parent has `delegate_task`, `get_invoice`, `list_charges` and
`finish`; its children `get_invoice`, `create_charge` and `list_charges`. Hermes children inherit
the parent's toolsets, so `create_charge` is taken out of the parent's list after the parent is
built, and `finish` is put in only there. The stand refuses an actor any tool its side does not
have (the second lock). Continuing a finished child is refused by the plugin.

The stand's journal is `stand_calls.jsonl`; `calls.jsonl` is rebuilt from it with one `delegate`
row per child Hermes started (in its seq order, with the child's report as the result), so the
grader, the coder and `show.py` read it unchanged.
"""

import asyncio
import copy
import glob
import json
import os
import shutil
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from handoff_bench.agents.call_journal import CallJournal, CallRow
from handoff_bench.agents.delegation import register_finish
from handoff_bench.agents.gateway import ToolGateway
from handoff_bench.agents.loop import AgentTrace, ModelCallLog
from handoff_bench.agents.mcp_backend import McpBackend
from handoff_bench.agents.model_client import Usage
from handoff_bench.agents.tools import InfraError, StopReason, ToolResult
from handoff_bench.billing import BillingService, EffectJournal
from handoff_bench.billing.protocol import format_error
from handoff_bench.billing.server import running_server
from handoff_bench.faults.setup import FAULTS, with_delay
from handoff_bench.harness.hermes import HermesSettings
from handoff_bench.jsonl import JsonlWriter
from handoff_bench.keys import KeyHook
from handoff_bench.runner.episode import ORCHESTRATOR, EpisodeConfig, EpisodeResult
from handoff_bench.worlds import World

HERE = Path(__file__).parent
PLUGIN_DIR = HERE / "hermes_plugin"
RUNNER = HERE / "hermes_runner.py"
PLUGIN_NAME = "hb_stand"
BANK_TOOLSET = "hb_bank"  # every bank tool: the parent and its children
PARENT_TOOLSET = "hb_parent"  # `finish`: put into the parent's list only
DELEGATE = "delegate"  # our name for a child Hermes started
HOOKS_FILE = "hermes_hooks.jsonl"
STAND_CALLS = "stand_calls.jsonl"
RUNNER_FILE = "hermes_runner.json"
STAND_CONFIG_FILE = "hermes_stand.json"
OUT_FILE = "hermes_out.json"
STDERR_FILE = "harness_stderr.txt"
LIVE_DIR = "hermes_live"  # Hermes' live transcripts of its children


class ActorTools:
    """Runs the plugin's calls through the gateway in the episode's task (the bank's MCP clients
    are opened lazily and must be entered and closed in one task, as in `StandTools`)."""

    def __init__(
        self,
        gateway: ToolGateway,
        *,
        parent_tools: tuple[str, ...],
        child_tools: tuple[str, ...],
        finish_tools: frozenset[str],
    ) -> None:
        self.gateway = gateway
        self.parent_tools = (*parent_tools, DELEGATE)
        self.child_tools = child_tools
        self.finish_tools = finish_tools
        self.finish_args: dict[str, Any] | None = None
        self.finish_tool: str | None = None
        self.stop: StopReason | None = None
        self.error: str | None = None
        self.stopped = asyncio.Event()
        self._queue: asyncio.Queue[tuple[str, str, dict[str, Any], asyncio.Future[ToolResult]]] = (
            asyncio.Queue()
        )

    def allowed(self, actor: str) -> tuple[str, ...]:
        return self.parent_tools if actor == ORCHESTRATOR else self.child_tools

    async def call(self, actor: str, tool: str, args: Any) -> ToolResult:
        if self.stop is not None:
            return self._reject(
                actor, tool, args, "episode_over", "The task is finished. Stop now."
            )
        if tool not in self.allowed(actor):
            return self._reject(actor, tool, args, "unknown_tool", f"No tool named {tool}.")
        if not isinstance(args, dict):
            return self._reject(
                actor, tool, args, "invalid_arguments", "Arguments must be an object."
            )
        done: asyncio.Future[ToolResult] = asyncio.get_running_loop().create_future()
        self._queue.put_nowait((actor, tool, args, done))
        return await done

    async def serve(self, until: asyncio.Future[Any]) -> None:
        while not until.done():
            get = asyncio.ensure_future(self._queue.get())
            await asyncio.wait({get, until}, return_when=asyncio.FIRST_COMPLETED)
            if not get.done():
                get.cancel()
                break
            actor, tool, args, done = get.result()
            result = await self._execute(actor, tool, args)
            if not done.done():
                done.set_result(result)
        while not self._queue.empty():
            *_, done = self._queue.get_nowait()
            done.cancel()

    async def _execute(self, actor: str, tool: str, args: dict[str, Any]) -> ToolResult:
        if self.stop is not None:
            return self._reject(
                actor, tool, args, "episode_over", "The task is finished. Stop now."
            )
        try:
            result = await self.gateway.call(actor, tool, args)
        except InfraError as e:
            self._end("infra_error", str(e))
            return ToolResult(outcome="infra_error", error_code=e.code, error_message=e.message)
        if actor == ORCHESTRATOR and tool in self.finish_tools and result.outcome == "ok":
            self.finish_tool, self.finish_args = tool, args
            self._end("finish")
        return result

    def _reject(self, actor: str, tool: str, args: object, code: str, message: str) -> ToolResult:
        return self.gateway.reject(actor, tool, repr(args), code, message)

    def _end(self, reason: StopReason, error: str | None = None) -> None:
        self.stop, self.error = reason, error
        self.stopped.set()


def answer(result: ToolResult) -> dict[str, Any]:
    """What the plugin hands Hermes: the data, or the error as the bank's MCP server words it."""
    if result.outcome == "ok":
        return {"is_error": False, "data": result.data}
    return {
        "is_error": True,
        "text": format_error(result.error_code or result.outcome, result.error_message or ""),
    }


@asynccontextmanager
async def serving(tools: ActorTools, *, host: str = "127.0.0.1") -> AsyncIterator[str]:
    """The stand's endpoint for the plugin on this event loop; yields its URL."""

    async def call(request: Request) -> JSONResponse:
        body = await request.json()
        result = await tools.call(str(body.get("actor")), str(body.get("tool")), body.get("args"))
        return JSONResponse(answer(result))

    app = Starlette(routes=[Route("/call", call, methods=["POST"])])
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        while not server.started:
            if task.done():
                task.result()
                raise RuntimeError(f"stand endpoint did not start on port {port}")
            await asyncio.sleep(0.02)
        yield f"http://{host}:{port}/call"
    finally:
        server.should_exit = True
        await task
        sock.close()


def hermes_python(settings: HermesSettings) -> str:
    if settings.python:
        return settings.python
    found = sorted(glob.glob(str(Path.home() / ".hermes/tools/python-3*/bin/python3")))
    if not found:
        raise FileNotFoundError("Hermes' interpreter not found; set orchestrator.python")
    return found[-1]


def hermes_config(settings: HermesSettings, subagent_max_steps: int) -> dict[str, Any]:
    delegation: dict[str, Any] = {"max_iterations": subagent_max_steps}
    if settings.subagent_model:
        delegation["model"] = settings.subagent_model
        if settings.base_url:  # the tests' endpoint serves the children too
            delegation["base_url"] = settings.base_url
            delegation["api_key"] = "unused"
        else:
            delegation["provider"] = settings.subagent_provider or settings.provider
    return {
        "plugins": {"enabled": [PLUGIN_NAME]},
        # Every tool in the model's list, as Claude Code's subagents see their bank tools.
        "tools": {"tool_search": {"enabled": "off"}},
        "delegation": delegation,
    }


def _tool_specs(gateway: ToolGateway, bank: list[str], parent_only: list[str]) -> list[dict]:
    specs = []
    for names, toolset in ((bank, BANK_TOOLSET), (parent_only, PARENT_TOOLSET)):
        for spec in gateway.openai_tools(names):
            f = spec["function"]
            specs.append(
                {
                    "name": f["name"],
                    "description": f["description"],
                    "parameters": f["parameters"],
                    "toolset": toolset,
                }
            )
    return specs


async def run_hermes_native_episode(
    world: World, config: EpisodeConfig, *, episode_dir: Path
) -> EpisodeResult:
    settings = config.orchestrator
    if not isinstance(settings, HermesSettings):
        raise ValueError(f"cell {config.cell} needs Hermes as the orchestrator")
    agents = config.agents
    fault = with_delay(FAULTS[config.fault](world), config.delay, world)
    episode_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        episode_dir / "config.json",
        {"episode": config.model_dump(mode="json"), "bank": fault.bank.model_dump(mode="json")},
    )
    service = BillingService(
        episode_id=config.episode_id,
        config=fault.bank,
        journal=EffectJournal(episode_dir / "effects.jsonl"),
        db_path=episode_dir / "bank.sqlite",
        id_seed=config.seed,
    )
    service.add_invoices(world.invoices)

    parent_bank = [t for t in agents.orchestrator_tools if t != "finish"]
    bank = sorted({*parent_bank, *agents.subagent_tools})
    hidden = sorted(set(bank) - set(parent_bank))
    parent_only = [t for t in agents.orchestrator_tools if t == "finish"]
    home = episode_dir / "hermes_home"
    with service, running_server(service) as url:
        async with McpBackend(url) as backend:
            gateway = ToolGateway(
                episode_id=config.episode_id,
                journal=CallJournal(episode_dir / STAND_CALLS),
                backends=[backend],
                hooks=[KeyHook(config.key, {})],
            )
            register_finish(gateway)

            async def delegate(actor: str, args: dict[str, Any]) -> dict[str, Any]:
                return {"started": True}  # a marker: Hermes runs the child, not us

            gateway.register(DELEGATE, delegate, description="A child Hermes started.")
            tools = ActorTools(
                gateway,
                parent_tools=tuple(agents.orchestrator_tools),
                child_tools=tuple(agents.subagent_tools),
                finish_tools=frozenset({"finish"}),
            )
            async with serving(tools) as stand_url:
                _prepare_home(home, settings, agents.subagent_max_steps)
                stand_config = {
                    "url": stand_url,
                    "log": str((episode_dir / HOOKS_FILE).resolve()),
                    "orchestrator": ORCHESTRATOR,
                    "delegate_tool": DELEGATE,
                    "call_timeout_s": settings.timeout_s,
                    "tools": _tool_specs(gateway, bank, parent_only),
                }
                _write_json(episode_dir / STAND_CONFIG_FILE, stand_config)
                runner = {
                    "home": str(home.resolve()),
                    "agent_root": settings.agent_root,
                    "stand_config": str((episode_dir / STAND_CONFIG_FILE).resolve()),
                    "model": settings.model,
                    "provider": settings.provider,
                    "base_url": settings.base_url,
                    "max_iterations": agents.orchestrator_max_steps,
                    "system_prompt": agents.orchestrator_prompt,
                    "goal": world.task,
                    "toolsets": ["delegation", BANK_TOOLSET],
                    "hide_from_parent": hidden,
                    "parent_only": parent_only,
                    "parent_toolset": PARENT_TOOLSET,
                    "out": str((episode_dir / OUT_FILE).resolve()),
                }
                _write_json(episode_dir / RUNNER_FILE, runner)
                process = asyncio.create_task(_run_process(settings, episode_dir, tools))
                await tools.serve(until=process)
                stderr, timed_out = await process
    # Hermes' own live logs of its children are raw logs: kept; the rest of the home is not.
    # Hermes building its dependencies under the episode's home: minutes and ~1 GB per episode.
    built_deps = (home / "installs").exists()
    live = home / "cache" / "delegation" / "live"
    if live.is_dir():
        shutil.copytree(live, episode_dir / LIVE_DIR, dirs_exist_ok=True)
    shutil.rmtree(home, ignore_errors=True)
    if stderr:
        (episode_dir / STDERR_FILE).write_text(stderr, encoding="utf-8")

    out = _read_json(episode_dir / OUT_FILE) or {"failed": True, "error": "no runner output"}
    hooks = _read_jsonl(episode_dir / HOOKS_FILE)
    stand_rows = CallJournal.read(episode_dir / STAND_CALLS)
    rows = with_reports(stand_rows, hooks)
    journal = CallJournal(episode_dir / "calls.jsonl")
    for row in rows:
        journal.write(row)
    JsonlWriter[ModelCallLog](episode_dir / "model_calls.jsonl")  # none: Hermes ran them

    stop_reason: StopReason
    error = None
    if tools.stop is not None:
        stop_reason, error = tools.stop, tools.error
    elif timed_out:
        stop_reason, error = "infra_error", "Hermes timed out"
    elif out.get("failed"):
        stop_reason, error = "api_error", str(out.get("error") or "Hermes reported a failed run")
    else:
        stop_reason = "text_only"
    usage = hermes_usage(hooks, float(out.get("cost") or 0.0))
    messages = parent_messages(agents.orchestrator_prompt, world.task, out.get("messages") or [])
    trace = AgentTrace(
        actor=ORCHESTRATOR,
        stop_reason=stop_reason,
        steps=sum(
            1 for h in hooks if h.get("kind") == "api_request" and h.get("actor") == ORCHESTRATOR
        ),
        finish_tool=tools.finish_tool,
        finish_args=tools.finish_args,
        messages=messages,
        usage=usage,
        retries=0,
        error=error,
    )
    subagents = child_traces(hooks, rows)
    _write_json(
        episode_dir / "trace.json",
        {
            "orchestrator": trace.model_dump(mode="json"),
            "subagents": [s.model_dump(mode="json") for s in subagents],
            "parent_tools": out.get("parent_tools"),
            "hermes_built_deps": built_deps,
        },
    )
    return EpisodeResult(
        episode_dir=episode_dir,
        stop_reason=stop_reason,
        steps=trace.steps,
        delegations=len(subagents),
        orchestrator_responses=[
            len(m["tool_calls"])
            for m in messages
            if m.get("role") == "assistant" and m.get("tool_calls")
        ],
        fault_fired=any(r.outcome == "timeout" for r in rows),
        finish_args=tools.finish_args,
        final_text=out.get("final_response") if stop_reason == "text_only" else None,
        cost=usage.cost,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        cached_tokens=usage.cached_tokens,
        retries=0,
        error=error,
    )


def _prepare_home(home: Path, settings: HermesSettings, subagent_max_steps: int) -> None:
    shutil.rmtree(home, ignore_errors=True)
    (home / "plugins").mkdir(parents=True)
    shutil.copytree(
        PLUGIN_DIR,
        home / "plugins" / PLUGIN_NAME,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    import yaml

    (home / "config.yaml").write_text(
        yaml.safe_dump(hermes_config(settings, subagent_max_steps), sort_keys=False),
        encoding="utf-8",
    )


async def _run_process(
    settings: HermesSettings, episode_dir: Path, tools: ActorTools
) -> tuple[str, bool]:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    command = [*settings.runner] if settings.runner else [hermes_python(settings), str(RUNNER)]
    proc = await asyncio.create_subprocess_exec(
        *command,
        str((episode_dir / RUNNER_FILE).resolve()),
        cwd=str(episode_dir),
        env=env,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stderr
    timed_out = False

    async def watchdog() -> None:
        nonlocal timed_out
        try:
            await asyncio.wait_for(tools.stopped.wait(), settings.timeout_s)
        except TimeoutError:
            timed_out = True
        else:
            if tools.stop == "finish":
                try:
                    await asyncio.wait_for(proc.wait(), settings.finish_grace_s)
                except TimeoutError:
                    pass
        if proc.returncode is None:
            proc.terminate()

    guard = asyncio.create_task(watchdog())
    try:
        stderr = await proc.stderr.read()
        await proc.wait()
    finally:
        guard.cancel()
    return stderr.decode(errors="replace").strip(), timed_out


def with_reports(rows: list[CallRow], hooks: list[dict[str, Any]]) -> list[CallRow]:
    """The stand's rows, each `delegate` marker given the report of its child (`sub-N` is the
    N-th child: the plugin posts the marker when Hermes starts it)."""
    reports = {h.get("actor"): h.get("summary") for h in hooks if h.get("kind") == "subagent_stop"}
    out, n = [], 0
    for row in sorted(rows, key=lambda r: r.seq):
        if row.tool == DELEGATE and row.actor == ORCHESTRATOR and row.outcome == "ok":
            n += 1
            row = row.model_copy(update={"result": reports.get(f"sub-{n}")})
        out.append(row)
    return out


def hermes_usage(hooks: list[dict[str, Any]], cost: float) -> Usage:
    """Tokens of every model call of the parent and its children (the plugin logs them); the
    cost is Hermes' own estimate for the parent session, which adds its children's."""
    usage = Usage(cost=cost)
    for h in hooks:
        u = h.get("usage") if h.get("kind") == "api_request" else None
        if not isinstance(u, dict):
            continue
        usage = usage + Usage(
            prompt_tokens=int(u.get("prompt_tokens") or 0),
            completion_tokens=int(u.get("output_tokens") or 0),
            reasoning_tokens=int(u.get("reasoning_tokens") or 0),
            cached_tokens=int(u.get("cache_read_tokens") or 0),
        )
    return usage


def parent_messages(
    system_prompt: str, goal: str, messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The parent's history in our loop's format. The system message is only our part of the
    prompt: Hermes puts it after its own."""
    out: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": goal},
    ]
    started = False
    for m in messages:
        role = m.get("role")
        if not started:  # Hermes' history starts with the user's goal
            started = role == "user"
            continue
        if role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": m.get("content")}
            if m.get("tool_calls"):
                entry["tool_calls"] = [
                    {
                        "id": c.get("id"),
                        "type": "function",
                        "function": _our_call(c.get("function") or {}),
                    }
                    for c in m["tool_calls"]
                ]
            out.append(entry)
        elif role == "tool":
            out.append(
                {"role": "tool", "tool_call_id": m.get("tool_call_id"), "content": m.get("content")}
            )
    return out


def _our_call(function: dict[str, Any]) -> dict[str, Any]:
    if function.get("name") != "delegate_task":
        return copy.deepcopy(function)
    return {"name": DELEGATE, "arguments": function.get("arguments", "{}")}


def child_traces(hooks: list[dict[str, Any]], rows: list[CallRow]) -> list[AgentTrace]:
    """One trace per child Hermes started, rebuilt from its goal, its calls and its report."""
    out = []
    stops = {h.get("actor"): h for h in hooks if h.get("kind") == "subagent_stop"}
    for start in (h for h in hooks if h.get("kind") == "subagent_start"):
        actor = str(start.get("actor"))
        messages: list[dict[str, Any]] = [{"role": "user", "content": start.get("goal") or ""}]
        for row in (r for r in rows if r.actor == actor):
            call_id = f"hermes-{row.seq}"
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": row.tool, "arguments": json.dumps(row.args)},
                        }
                    ],
                }
            )
            content = (
                json.dumps(row.result, ensure_ascii=False)
                if row.outcome == "ok"
                else format_error(row.error_code or row.outcome, row.error_message or "")
            )
            messages.append({"role": "tool", "tool_call_id": call_id, "content": content})
        stop = stops.get(actor) or {}
        messages.append({"role": "assistant", "content": stop.get("summary")})
        out.append(
            AgentTrace(
                actor=actor,
                stop_reason="text_only",
                steps=sum(
                    1 for h in hooks if h.get("kind") == "api_request" and h.get("actor") == actor
                ),
                messages=messages,
                usage=Usage(),  # counted in the orchestrator's total
                retries=0,
                error=None
                if stop.get("status") in (None, "completed")
                else str(stop.get("status")),
            )
        )
    return out


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
