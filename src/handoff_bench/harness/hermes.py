"""Hermes Agent as an external orchestrator over the stand's per-episode MCP server."""

import asyncio
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict

from handoff_bench.agents.gateway import ToolGateway
from handoff_bench.agents.loop import AgentSpec, AgentTrace
from handoff_bench.agents.model_client import Usage
from handoff_bench.agents.tools import StopReason
from handoff_bench.harness.stand_mcp import SERVER_NAME, StandTools, serving

USAGE_FILE = "harness_usage.json"
CONFIG_FILE = "hermes_config.yaml"
SOUL_FILE = "hermes_soul.md"


class HermesSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    harness: Literal["hermes"] = "hermes"
    model: str
    provider: str = "openrouter"
    binary: str = "hermes"
    timeout_s: float = 900
    finish_grace_s: float = 30
    # Hermes' own subagents (hermes_native cells, `harness/hermes_native.py`): Hermes runs
    # in-process under its own interpreter. None: the newest ~/.hermes/tools/python-3*.
    python: str | None = None
    agent_root: str = str(Path.home() / ".hermes" / "hermes-agent")
    subagent_model: str | None = None  # None: the children run on `model`
    subagent_provider: str | None = None  # None: `provider`
    base_url: str | None = None  # an OpenAI-compatible endpoint instead of `provider` (tests)
    runner: list[str] | None = None  # the command instead of Hermes' runner (tests)

    @property
    def label(self) -> str:
        return f"{self.harness}:{self.model}"


class HermesOrchestrator:
    def __init__(self, settings: HermesSettings) -> None:
        self.settings = settings

    async def run(
        self, spec: AgentSpec, goal: str, *, gateway: ToolGateway, episode_dir: Path
    ) -> AgentTrace:
        tools = StandTools(
            gateway,
            actor=spec.actor,
            tools=[t["function"]["name"] for t in spec.tools],
            finish_tools=spec.finish_tools,
        )
        async with serving(tools) as url:
            home = _profile_home(episode_dir.parent.name)
            home.mkdir(parents=True, exist_ok=True)
            config = {
                "platform_toolsets": {"cli": [SERVER_NAME]},
                "mcp_servers": {SERVER_NAME: {"url": url}},
            }
            rendered_config = yaml.safe_dump(config, sort_keys=False)
            (home / "config.yaml").write_text(rendered_config, encoding="utf-8")
            (episode_dir / CONFIG_FILE).write_text(rendered_config, encoding="utf-8")
            (home / "SOUL.md").write_text(spec.system_prompt, encoding="utf-8")
            (episode_dir / SOUL_FILE).write_text(spec.system_prompt, encoding="utf-8")
            process = asyncio.create_task(
                self._run_process(
                    spec,
                    goal,
                    str(home),
                    tools,
                    episode_dir / USAGE_FILE,
                    episode_dir,
                )
            )
            await tools.serve(until=process)
            stdout, stderr, timed_out = await process
        if stderr:
            (episode_dir / "harness_stderr.txt").write_text(stderr, encoding="utf-8")
        usage_data = _read_usage(episode_dir / USAGE_FILE)
        usage = _usage(usage_data)
        stop_reason: StopReason
        error = None
        if tools.stop is not None:
            stop_reason = tools.stop
        elif timed_out:
            stop_reason, error = "infra_error", "Hermes timed out"
        elif usage_data.get("failed"):
            stop_reason, error = "api_error", stderr or "Hermes reported a failed run"
        else:
            stop_reason = "text_only"
        messages = _messages(spec, goal, gateway, stdout)
        return AgentTrace(
            actor=spec.actor,
            stop_reason=stop_reason,
            steps=int(usage_data.get("api_calls") or len(gateway.journal.by_actor(spec.actor))),
            finish_tool=tools.finish_tool,
            finish_args=tools.finish_args,
            messages=messages,
            usage=usage,
            retries=0,
            error=error,
        )

    async def _run_process(
        self,
        spec: AgentSpec,
        goal: str,
        hermes_home: str,
        tools: StandTools,
        usage_path: Path,
        episode_dir: Path,
    ) -> tuple[str, str, bool]:
        prompt = goal
        env = dict(os.environ)
        env["HERMES_HOME"] = hermes_home
        proc = await asyncio.create_subprocess_exec(
            self.settings.binary,
            "--provider",
            self.settings.provider,
            "--model",
            self.settings.model,
            "--toolsets",
            SERVER_NAME,
            "--usage-file",
            str(usage_path),
            "-z",
            prompt,
            cwd=hermes_home,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdout and proc.stderr
        timed_out = False

        async def watchdog() -> None:
            nonlocal timed_out
            try:
                await asyncio.wait_for(tools.stopped.wait(), self.settings.timeout_s)
            except TimeoutError:
                timed_out = True
            else:
                if tools.stop == "finish":
                    try:
                        await asyncio.wait_for(proc.wait(), self.settings.finish_grace_s)
                    except TimeoutError:
                        pass
            if proc.returncode is None:
                proc.terminate()

        guard = asyncio.create_task(watchdog())
        try:
            stdout, stderr = await proc.communicate()
            if proc.returncode not in (0, -15) and not usage_path.exists():
                (episode_dir / "harness_exit.txt").write_text(
                    f"Hermes exited with status {proc.returncode}", encoding="utf-8"
                )
        finally:
            guard.cancel()
        return (
            stdout.decode(errors="replace").strip(),
            stderr.decode(errors="replace").strip(),
            timed_out,
        )


def _read_usage(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"failed": True}


def _profile_home(run_id: str) -> Path:
    """Reuse Hermes' isolated runtime dependencies across episodes in one batch."""
    safe_id = re.sub(r"[^A-Za-z0-9._-]", "-", run_id)
    return Path(tempfile.gettempdir()) / "handoff-bench-hermes" / safe_id


def _usage(data: dict[str, Any]) -> Usage:
    return Usage(
        prompt_tokens=int(data.get("input_tokens") or 0),
        completion_tokens=int(data.get("output_tokens") or 0),
        reasoning_tokens=int(data.get("reasoning_tokens") or 0),
        cached_tokens=int(data.get("cache_read_tokens") or 0),
        cost=float(data.get("estimated_cost_usd") or 0),
    )


def _messages(
    spec: AgentSpec, goal: str, gateway: ToolGateway, final_text: str
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": spec.system_prompt},
        {"role": "user", "content": goal},
    ]
    for row in gateway.journal.by_actor(spec.actor):
        call_id = f"hermes-{row.seq}"
        messages.append(
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": row.tool,
                            "arguments": json.dumps(row.args, ensure_ascii=False),
                        },
                    }
                ],
            }
        )
        result = (
            row.result if row.outcome == "ok" else f"Error ({row.error_code}): {row.error_message}"
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": json.dumps(result, ensure_ascii=False),
            }
        )
    messages.append({"role": "assistant", "content": final_text})
    return messages
