"""Claude Code's hook in native cells: `python -m handoff_bench.harness.hook <policy.json>`.

Claude Code runs it before every tool call and when a subagent starts or stops, with the event as
JSON on stdin. The hook appends the event to the episode's `hooks.jsonl` and, before a bank call:
- refuses the orchestrator the bank tools its cell does not give it (calls without `agent_id`
  come from the orchestrator, calls with it from one of its subagents). The orchestrator's bank
  server has only those tools anyway; this is the second lock;
- sets the idempotency key of a subagent's charge as the cell says, the way a harness would.
"""

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from handoff_bench.cells import KeySource
from handoff_bench.keys import KEY_FIELD, intent_key

# The orchestrator's bank server (only the tools its cell gives it) and the subagent's own one.
BANK_PREFIX = "mcp__bank__"
SUBAGENT_BANK = "billing"
BANK_PREFIXES = (BANK_PREFIX, f"mcp__{SUBAGENT_BANK}__")
POLICY_FILE = "hook_policy.json"
HOOKS_FILE = "hooks.jsonl"


class HookPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    log: Path
    parent_bank_tools: tuple[str, ...]
    key: KeySource


def key_args(key: KeySource, args: dict[str, Any], agent_id: str) -> dict[str, Any]:
    """The charge as the bank should see it."""
    out = {k: v for k, v in args.items() if k != KEY_FIELD}
    if key == "subagent":
        return dict(args)
    if key == "task":
        out[KEY_FIELD] = f"task:{agent_id}"
    elif key == "intent":
        out[KEY_FIELD] = intent_key(args)
    elif key != "none":
        raise ValueError(f"key {key!r} is not supported in native cells")
    return out


def decide(event: dict[str, Any], policy: HookPolicy) -> dict[str, Any]:
    """Claude Code's hook output for this event: empty means no change."""
    tool = str(event.get("tool_name", ""))
    prefix = next((p for p in BANK_PREFIXES if tool.startswith(p)), None)
    if event.get("hook_event_name") != "PreToolUse" or prefix is None:
        return {}
    name = tool.removeprefix(prefix)
    agent = event.get("agent_id")
    args = event.get("tool_input") or {}
    if agent is None and name not in policy.parent_bank_tools:
        return _pre_tool(
            permissionDecision="deny", permissionDecisionReason=f"No tool named {name}."
        )
    if agent is not None and name == "create_charge":
        sent = key_args(policy.key, args, agent)
        if sent != args:
            return _pre_tool(permissionDecision="allow", updatedInput=sent)
    return {}


def _pre_tool(**fields: Any) -> dict[str, Any]:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", **fields}}


def main() -> None:
    policy = HookPolicy.model_validate_json(Path(sys.argv[1]).read_text(encoding="utf-8"))
    event = json.load(sys.stdin)
    out = decide(event, policy)
    with policy.log.open("a", encoding="utf-8") as f:
        f.write(json.dumps({**event, "decision": out}, ensure_ascii=False) + "\n")
    print(json.dumps(out))


if __name__ == "__main__":
    main()
