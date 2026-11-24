"""The stand's plugin for Hermes in the hermes_native cells (94-101). Runs inside Hermes.

Standard library only: Hermes loads it with its own interpreter, where handoff_bench is not
installed. The stand writes `HB_STAND_CONFIG` (see `harness/hermes_native.py`):

    {"url": ..., "log": ..., "orchestrator": "orchestrator", "call_timeout_s": ...,
     "tools": [{"name", "description", "parameters", "toolset"}, ...]}

- Every bank tool and `finish` is a plugin tool. Its handler sends the call to the stand with
  the actor: `sub-N` for the N-th child Hermes started (`subagent_start`), the orchestrator
  for any other session. The stand runs it through its gateway: the call journal, the key
  policy and the bank are the same as in the Claude Code native cells.
- Each child Hermes starts is posted to the stand as a `delegate` call of the orchestrator
  before the child runs, so the journal keeps the order the parent started them in.
- `delegate_task` may only spawn: continuing a finished child is refused (cells 94-101 count
  only new children).
- Every hook event and every model call (with its usage) goes to the episode's log.
"""

import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_CONFIG: dict[str, Any] = {}
_LOCK = threading.Lock()
_CHILDREN: dict[str, str] = {}  # child session id -> sub-N
SPAWN_ACTIONS = (None, "", "spawn")
CONTINUE_REFUSED = "Only new subagents can be started here: action must be spawn."


def configure(config: dict[str, Any]) -> None:
    _CONFIG.clear()
    _CONFIG.update(config)
    with _LOCK:
        _CHILDREN.clear()


def _orchestrator() -> str:
    return str(_CONFIG.get("orchestrator", "orchestrator"))


def actor_of(session_id: str | None) -> str:
    with _LOCK:
        return _CHILDREN.get(session_id or "", _orchestrator())


def _log(kind: str, **fields: Any) -> None:
    path = _CONFIG.get("log")
    if not path:
        return
    row = {"t": time.time(), "kind": kind, **fields}
    with _LOCK, open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def on_subagent_start(
    child_session_id: str | None = None,
    child_subagent_id: str | None = None,
    parent_session_id: str | None = None,
    child_goal: str | None = None,
    **_: Any,
) -> None:
    with _LOCK:
        actor = _CHILDREN.setdefault(child_session_id or "", f"sub-{len(_CHILDREN) + 1}")
    # The stand's marker of this child, in the journal's order: `sub-N` is the N-th marker.
    marker = call_stand(
        _orchestrator(), str(_CONFIG.get("delegate_tool", "delegate")), {"goal": child_goal or ""}
    )
    _log(
        "subagent_start",
        actor=actor,
        child_session_id=child_session_id,
        child_subagent_id=child_subagent_id,
        parent_session_id=parent_session_id,
        goal=child_goal,
        marker=marker,
    )


def on_subagent_stop(
    child_session_id: str | None = None,
    child_status: str | None = None,
    child_summary: str | None = None,
    **_: Any,
) -> None:
    _log(
        "subagent_stop",
        actor=actor_of(child_session_id),
        child_session_id=child_session_id,
        status=child_status,
        summary=child_summary,
    )


def on_pre_tool_call(
    tool_name: str | None = None,
    args: dict[str, Any] | None = None,
    session_id: str | None = None,
    tool_call_id: str | None = None,
    **_: Any,
) -> dict[str, Any] | None:
    decision = None
    if tool_name == "delegate_task" and (args or {}).get("action") not in SPAWN_ACTIONS:
        decision = {"action": "block", "message": CONTINUE_REFUSED}
    _log(
        "pre_tool_call",
        actor=actor_of(session_id),
        session_id=session_id,
        tool=tool_name,
        args=args,
        tool_call_id=tool_call_id,
        decision=decision,
    )
    return decision


def on_post_tool_call(
    tool_name: str | None = None,
    session_id: str | None = None,
    result: Any = None,
    **_: Any,
) -> None:
    _log("post_tool_call", actor=actor_of(session_id), tool=tool_name, result=result)


def on_post_api_request(
    session_id: str | None = None,
    model: str | None = None,
    response_model: str | None = None,
    provider: str | None = None,
    usage: dict[str, Any] | None = None,
    finish_reason: str | None = None,
    **_: Any,
) -> None:
    _log(
        "api_request",
        actor=actor_of(session_id),
        session_id=session_id,
        model=model,
        response_model=response_model,
        provider=provider,
        usage=usage,
        finish_reason=finish_reason,
    )


def call_stand(actor: str, tool: str, args: dict[str, Any]) -> str:
    """The stand's answer, shaped as Hermes shapes an MCP result: `{"result"}` or `{"error"}`."""
    body = json.dumps({"actor": actor, "tool": tool, "args": args}).encode("utf-8")
    request = urllib.request.Request(
        _CONFIG["url"], data=body, headers={"content-type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(
            request, timeout=float(_CONFIG.get("call_timeout_s", 300))
        ) as r:
            answer = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        return json.dumps({"error": f"The tool is unavailable: {e}"})
    if answer.get("is_error"):
        return json.dumps({"error": answer.get("text", "")}, ensure_ascii=False)
    return json.dumps({"result": answer.get("data")}, ensure_ascii=False)


def _handler(tool: str):
    def handler(args: dict[str, Any], session_id: str | None = None, **_: Any) -> str:
        return call_stand(actor_of(session_id), tool, dict(args or {}))

    return handler


def register(ctx: Any) -> None:
    configure(json.loads(Path(os.environ["HB_STAND_CONFIG"]).read_text(encoding="utf-8")))
    for spec in _CONFIG["tools"]:
        ctx.register_tool(
            name=spec["name"],
            toolset=spec["toolset"],
            schema={
                "name": spec["name"],
                "description": spec["description"],
                "parameters": spec["parameters"],
            },
            handler=_handler(spec["name"]),
            description=spec["description"],
        )
    ctx.register_hook("subagent_start", on_subagent_start)
    ctx.register_hook("subagent_stop", on_subagent_stop)
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("post_api_request", on_post_api_request)
