import csv
import io
import json
import sys

import pytest

from handoff_bench.cells import CELLS
from handoff_bench.harness import codex_native
from handoff_bench.harness.codex_native import hook, stamp
from handoff_bench.runner.batch import BatchConfig, run_batch


@pytest.mark.parametrize(
    ("actor", "tool", "decision", "denial"),
    [
        (None, "finish", "allow", None),
        (None, "create_charge", "deny", "wrong_role"),
        ("known", "create_charge", "allow", None),
        ("known", "finish", "deny", "wrong_role"),
        ("unknown", "finish", "deny", "unattributed"),
    ],
)
def test_bank_hook_uses_native_child_identity(
    tmp_path, monkeypatch, capsys, actor, tool, decision, denial
):
    secret = b"test"
    log = tmp_path / "hooks.jsonl"
    log.write_text(json.dumps({"hook_event_name": "SubagentStart", "agent_id": "known"}) + "\n")
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"log": str(log), "secret": secret.hex()}))
    event = {
        "hook_event_name": "PreToolUse",
        "session_id": "shared-parent-id",
        "agent_id": actor,
        "tool_name": f"mcp__stand__{tool}",
        "tool_input": {"_hb_actor": "supplied-by-model"},
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))

    hook(policy)

    out = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert out["permissionDecision"] == decision
    assert json.loads(log.read_text().splitlines()[-1])["stand_denial"] == denial
    if decision == "allow":
        assert out["updatedInput"]["_hb_actor"] == stamp(
            secret, f"sub-{actor}" if actor else "orchestrator"
        )


@pytest.mark.parametrize("cell", range(102, 110))
def test_codex_cells_match_other_native_harnesses(cell):
    expected = CELLS[cell]
    assert expected.block == "codex_native"
    for offset in (8, 16):
        assert expected.model_dump(exclude={"number", "block"}) == CELLS[cell - offset].model_dump(
            exclude={"number", "block"}
        )


@pytest.mark.anyio
async def test_codex_batch_saves_results_and_resumes_after_limit(tmp_path, monkeypatch):
    attempts = 0
    limited = True

    async def fake_process(settings, prompt, goal, cwd, hook_cmd, url, out):
        nonlocal attempts
        attempts += 1
        hit_limit = limited and attempts == 2
        (out / "codex_stderr.txt").write_text("usage limit reached" if hit_limit else "")
        (out / "codex_stream.jsonl").write_text("")
        if hit_limit:
            return 1
        (out / "hooks.jsonl").write_text(
            json.dumps(
                {
                    "hook_event_name": "PostModelRequest",
                    "status": "completed",
                    "model": settings.model,
                    "token_usage": {"input_tokens": 123},
                }
            )
            + "\n"
        )
        return 0

    monkeypatch.setattr(codex_native, "run_process", fake_process)
    config = BatchConfig(
        cells=[102, 103, 104],
        templates=[1],
        seeds=[1],
        budget_usd=1,
        parallel=1,
        model={"model": "child", "provider": "codex-subscription"},
        orchestrator={"harness": "codex", "model": "parent", "subagent_model": "child"},
    )
    args = {"run_dir": tmp_path, "run_id": "test-codex", "commit": "test"}

    first = await run_batch(config, lambda _: None, **args)
    assert (first.finished, first.skipped) == (1, 2)
    assert not first.cost_missing
    assert first.limit_reached and attempts == 2

    limited = False
    resumed = await run_batch(config, lambda _: None, resume=True, **args)
    assert (resumed.done_before, resumed.finished, resumed.skipped) == (1, 2, 0)
    assert attempts == 4
    with (tmp_path / "results.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert {row["cell"] for row in rows} == {"102", "103", "104"}
    assert all(row["outcome"] == "no_finish" and row["prompt_tokens"] == "123" for row in rows)
