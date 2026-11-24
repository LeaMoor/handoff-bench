"""Runs one Hermes orchestrator in this process: `python hermes_runner.py <runner.json>`.

Started by `harness/hermes_native.py` with Hermes' own interpreter, so standard library and
Hermes only (handoff_bench is not importable here). Hermes runs in-process (`AIAgent`), not
through `hermes -z`: `-z` hands the turn to the host-wide gateway, which a running Hermes owns.

`runner.json`:
    home            HERMES_HOME of this episode (config.yaml and plugins/hb_stand inside)
    agent_root      the Hermes checkout (`run_agent.py`)
    stand_config    the plugin's config (HB_STAND_CONFIG)
    model, provider, base_url (optional: an OpenAI-compatible endpoint, the tests' fake model),
    max_iterations, system_prompt, goal
    toolsets        the parent's toolsets; its children inherit them
    hide_from_parent  tools taken out of the parent's list once it is built (children keep them)
    parent_only       tools of the `parent_toolset` added to the parent's list only
    parent_toolset
    out             where the result goes (JSON)

Delegation joins inside the turn (`gateway_delegation_inline_scope(True)`): the child's report
returns as the `delegate_task` result, as with Claude Code's Task tool. By default a library
run sends top-level delegations to the background and the turn ends before the child answers.
"""

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any


def _tool_name(tool: Any) -> str | None:
    if isinstance(tool, dict):
        return (tool.get("function") or {}).get("name")
    return None


def restrict_parent(agent: Any, hide: list[str], add: list[dict[str, Any]]) -> list[str]:
    """Remove `hide` from the parent's tools and add `add`; returns the parent's tool names."""
    agent.tools = [t for t in agent.tools or [] if _tool_name(t) not in set(hide)] + list(add)
    names = {n for n in (_tool_name(t) for t in agent.tools) if n}
    agent.valid_tool_names = type(agent.valid_tool_names)(names)
    return sorted(names)


def main() -> None:
    cfg = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out_path = Path(cfg["out"])
    os.environ["HERMES_HOME"] = cfg["home"]
    os.environ["HB_STAND_CONFIG"] = cfg["stand_config"]
    sys.path.insert(0, cfg["agent_root"])
    out: dict[str, Any] = {"failed": True}
    try:
        # First, as Hermes' launcher does: it puts Hermes' dependencies on the path. Without it
        # tool modules (delegate_tool among them) may fail to import and drop out silently.
        import hermes_bootstrap  # noqa: F401, I001 - must precede every Hermes import

        import model_tools
        from run_agent import AIAgent
        from tools.delegate_tool_config import gateway_delegation_inline_scope

        with gateway_delegation_inline_scope(True):
            agent = AIAgent(
                model=cfg["model"],
                provider=cfg["provider"],
                **(
                    {"base_url": cfg["base_url"], "api_key": cfg.get("api_key") or "unused"}
                    if cfg.get("base_url")
                    else {}
                ),
                enabled_toolsets=list(cfg["toolsets"]),
                ephemeral_system_prompt=cfg["system_prompt"],
                max_iterations=int(cfg["max_iterations"]),
                quiet_mode=True,
                skip_memory=True,
                skip_context_files=True,
                platform="cli",
            )
        add = model_tools.get_tool_definitions(
            enabled_toolsets=[cfg["parent_toolset"]], quiet_mode=True
        )
        add = [t for t in add if _tool_name(t) in set(cfg["parent_only"])]
        out["parent_tools"] = restrict_parent(agent, cfg["hide_from_parent"], add)
        missing = {"delegate_task", *cfg["parent_only"]} - set(out["parent_tools"])
        if missing:  # a tool module that failed to import: the episode would be meaningless
            raise RuntimeError(f"the parent lacks {sorted(missing)}")
        out["session_id"] = agent.session_id
        result = agent.run_conversation(user_message=cfg["goal"])
        out.update(
            failed=bool(result.get("failed")),
            error=result.get("error"),
            final_response=result.get("final_response"),
            messages=result.get("messages") or [],
            api_calls=getattr(agent, "session_api_calls", None),
            cost=float(getattr(agent, "session_estimated_cost_usd", 0.0) or 0.0),
        )
    except BaseException as e:  # noqa: BLE001 - the stand reads the reason from the file
        out.update(failed=True, error=f"{type(e).__name__}: {e}", traceback=traceback.format_exc())
    out_path.write_text(json.dumps(out, ensure_ascii=False, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
