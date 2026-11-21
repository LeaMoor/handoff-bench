"""Everything is computed from `results.csv` only."""

import math
from pathlib import Path
from typing import get_args

import pandas as pd

from handoff_bench.cells import CELLS, Channel
from handoff_bench.grading.grader import Outcome

OUTCOMES = [o for o in get_args(Outcome) if o != "invalid"]
CHANNELS: list[Channel] = [c for c in get_args(Channel) if c != "none"]
MAIN_CELLS = [c.number for c in CELLS.values() if c.block == "main"]
CALIBRATION_PAIRS = {
    (ch, delay): pair
    for ch in ("text", "code", "code_key")
    for delay in ("short", "long")
    if len(
        pair := tuple(
            c.number
            for c in CELLS.values()
            if c.block == "calibration" and c.channel == ch and c.delay == delay
        )
    )
    == 2
}
# Where the channel should decide the outcome: a check cannot see the charge and the subagent
# has no rules block. Predictions and "something to measure" are read here (to be frozen).
PRIMARY = {"delay": "long", "rules": False}
MEASURE_CELLS = [
    c.number
    for c in CELLS.values()
    if c.block == "main"
    and c.agent == "delegated"
    and c.channel == "text"
    and (c.delay, c.rules) == (PRIMARY["delay"], PRIMARY["rules"])
]
DEFAULTS = {
    "delay": "short",
    "rules": True,
    "agent": "delegated",
    "key": "subagent",
    "sub_claim": "none",
    "orchestrator": "stand",
}  # runs before these columns
VARIANT = ["world", "template", "seed", "repeat"]

# The plan's thresholds; the preregistration (task 14) freezes them.
MIN_TEXT_CODE_GAP = 0.25
MIN_CALIBRATION_ERROR_TEXT = 0.8
MAX_CALIBRATION_ERROR_CODE = 0.2
MIN_CONTROL_EOS = 0.9
EQUIVALENCE_TOLERANCE = 0.1


def load_results(*paths: Path) -> pd.DataFrame:
    files = [p / "results.csv" if p.is_dir() else p for p in paths]
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    for column, value in DEFAULTS.items():
        if column not in df:
            df[column] = value
    return df.astype({"correct": "boolean", "rules": bool})


def valid(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["outcome"] != "invalid"]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return math.nan, math.nan
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return centre - half, centre + half


def cell_table(df: pd.DataFrame) -> pd.DataFrame:
    cells = sorted(df["cell"].unique())
    v = valid(df)
    g = v.groupby("cell")
    out = pd.crosstab(v["cell"], v["outcome"], normalize="index")
    out = out.reindex(index=cells, columns=OUTCOMES, fill_value=0.0)
    out.insert(0, "n", g.size().reindex(cells, fill_value=0))
    out["invalid"] = (df["outcome"] == "invalid").groupby(df["cell"]).sum()
    out["fault_not_fired"] = (
        ((df["fault"] != "none") & ~df["fault_fired"]).groupby(df["cell"]).sum()
    )
    out["false_done"] = g["false_done"].mean()
    out["self_checked"] = g["self_checked"].mean()
    # Where a false "done" was born: the subagent the fault hit already said "done". Only where
    # the orchestrator got that report (bank faults); elsewhere the fault lost it, so NaN.
    seen = v["block"].isin(["main", "processing"])
    out["sub_done"] = (v["sub_claim"] == "done").astype(float).where(seen).groupby(v["cell"]).mean()
    out["retried"] = (v["retries"] > 0).groupby(v["cell"]).mean()
    out["tokens_mean"] = (v["prompt_tokens"] + v["completion_tokens"]).groupby(v["cell"]).mean()
    out["cost_mean"] = g["cost"].mean()
    out["cost_total"] = df.groupby("cell")["cost"].sum()
    return out


def first_actions(df: pd.DataFrame) -> pd.DataFrame:
    v = valid(df)
    return pd.crosstab(v["cell"], v["first_action"], normalize="index")


def _main(df: pd.DataFrame) -> pd.DataFrame:
    """Valid main-block episodes whose fault fired; a timeout that did not fire is a control run
    in disguise (counted in `fault_not_fired`)."""
    m = valid(df)
    return m[m["cell"].isin(MAIN_CELLS) & m["fault_fired"]]


def main_by_channel(
    df: pd.DataFrame, *, delay: str | None = None, rules: bool | None = None
) -> pd.DataFrame:
    """Main number 1: correct share per channel, delegated episodes, optionally one condition."""
    m = _main(df)
    m = m[m["agent"] == "delegated"]
    if delay is not None:
        m = m[m["delay"] == delay]
    if rules is not None:
        m = m[m["rules"] == rules]
    rows = []
    for channel in CHANNELS:
        s = m[m["channel"] == channel]
        n, k = len(s), int(s["correct"].sum())
        low, high = wilson(k, n)
        rows.append(
            {
                "channel": channel,
                "n": n,
                "correct": k / n if n else math.nan,
                "ci_low": low,
                "ci_high": high,
                "self_checked": s["self_checked"].mean() if n else math.nan,
            }
        )
    return pd.DataFrame(rows).set_index("channel")


def main_grid(df: pd.DataFrame) -> pd.DataFrame:
    """Main block by condition: who pays (B0 or delegated), delay, rules and channel."""
    m = _main(df)
    keys = ["agent", "delay", "rules", "channel"]
    g = m.groupby(keys)
    out = pd.DataFrame(
        {
            "n": g.size(),
            "correct": g["correct"].mean(),
            "duplicate": g["outcome"].apply(lambda s: (s == "duplicate").mean()),
            "honest_unknown": g["outcome"].apply(lambda s: (s == "honest_unknown").mean()),
            "self_checked": g["self_checked"].mean(),
        }
    )
    return out


def calibration_pairs(df: pd.DataFrame) -> pd.DataFrame:
    """Pair error = wrong number of charges in pre + in post. A pair is dropped if a half is
    invalid or its fault did not fire (the orchestrator then got real words, not the empty
    report)."""
    frames = []
    for (channel, delay), (pre, post) in CALIBRATION_PAIRS.items():
        cols = ["outcome", "effect", "first_action", "fault_fired"]
        a = df[df["cell"] == pre].set_index(VARIANT)[cols]
        b = df[df["cell"] == post].set_index(VARIANT)[cols]
        j = a.join(b, suffixes=("_pre", "_post"), how="inner")
        j = j[
            (j["outcome_pre"] != "invalid")
            & (j["outcome_post"] != "invalid")
            & j["fault_fired_pre"]
            & j["fault_fired_post"]
        ]
        j = j.assign(
            channel=channel,
            delay=delay,
            error_pre=(j["effect_pre"] != "once").astype(int),
            error_post=(j["effect_post"] != "once").astype(int),
            unknown=(j["outcome_pre"] == "honest_unknown")
            | (j["outcome_post"] == "honest_unknown"),
        )
        frames.append(j.assign(error=j["error_pre"] + j["error_post"]).reset_index())
    return pd.concat(frames, ignore_index=True)


def calibration_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Main number 2: mean over pairs where neither half said "unknown". Delay long: the
    focal charge never shows, so a check delegated to a subagent cannot tell the worlds apart."""
    pairs = calibration_pairs(df)
    rows = []
    for channel, delay in CALIBRATION_PAIRS:
        p = pairs[(pairs["channel"] == channel) & (pairs["delay"] == delay)]
        known = p[~p["unknown"]]
        rows.append(
            {
                "channel": channel,
                "delay": delay,
                "pairs": len(p),
                "pairs_without_unknown": len(known),
                "mean_error": known["error"].mean() if len(known) else math.nan,
                "unknown_share": p["unknown"].mean() if len(p) else math.nan,
            }
        )
    return pd.DataFrame(rows).set_index(["channel", "delay"])


def _check(name: str, value: float, rule: str, passed: bool | None) -> dict[str, object]:
    if isinstance(value, float) and math.isnan(value):
        passed = None
    return {"check": name, "value": value, "rule": rule, "passed": passed}


def control_eos(df: pd.DataFrame) -> float:
    c = valid(df)
    c = c[c["cell"] == 1]
    return (c["outcome"] == "eos").mean() if len(c) else math.nan


def predictions(df: pd.DataFrame) -> pd.DataFrame:
    """The plan's predictions; `passed` is None where there is no data yet."""
    main = main_by_channel(df, **PRIMARY)["correct"]
    cal = calibration_summary(df)["mean_error"].xs("short", level="delay")
    text, struct, code = (main[c] for c in ("text", "struct", "code"))
    eos = control_eos(df)
    spread = code - text if not math.isnan(struct) else math.nan
    return pd.DataFrame(
        [
            _check("main: text < struct < code", spread, "ordered", text < struct < code),
            _check(
                "main: code - text",
                code - text,
                f">= {MIN_TEXT_CODE_GAP}",
                code - text >= MIN_TEXT_CODE_GAP,
            ),
            _check(
                "main: struct no worse than code",
                struct - code,
                "informative: weakens 'code is needed'",
                struct >= code,
            ),
            _check(
                "calibration: text mean error",
                cal["text"],
                f">= {MIN_CALIBRATION_ERROR_TEXT}",
                cal["text"] >= MIN_CALIBRATION_ERROR_TEXT,
            ),
            _check(
                "calibration: code mean error",
                cal["code"],
                f"<= {MAX_CALIBRATION_ERROR_CODE}",
                cal["code"] <= MAX_CALIBRATION_ERROR_CODE,
            ),
            _check("control: EOS share", eos, f">= {MIN_CONTROL_EOS}", eos >= MIN_CONTROL_EOS),
        ]
    ).set_index("check")


def first_action_agreement(df: pd.DataFrame) -> tuple[float, float]:
    """(within pre/post pairs, between repeats). Words channel only: in "statement" they differ."""
    pairs = calibration_pairs(df)
    pairs = pairs[(pairs["channel"] == "text") & (pairs["delay"] == "short")]
    in_pair = (pairs["first_action_pre"] == pairs["first_action_post"]).mean()
    v = valid(df)
    v = v[v["cell"].isin(CALIBRATION_PAIRS[("text", "short")])]
    keys = ["cell", "world", "template", "seed"]
    r0 = v[v["repeat"] == 0].set_index(keys)["first_action"]
    r1 = v[v["repeat"] == 1].set_index(keys)["first_action"]
    both = pd.concat([r0, r1], axis=1, keys=["r0", "r1"], join="inner")
    in_repeats = (both["r0"] == both["r1"]).mean() if len(both) else math.nan
    return float(in_pair) if len(pairs) else math.nan, float(in_repeats)


def pilot_checks(df: pd.DataFrame) -> pd.DataFrame:
    """Pilot criteria computable from results.csv; grader and coder vs manual labels are not."""
    in_pair, in_repeats = first_action_agreement(df)
    eos = control_eos(df)
    v = valid(df)
    timeout_text = v[v["cell"].isin(MEASURE_CELLS) & v["fault_fired"]]["correct"]
    has_both = timeout_text.nunique() == 2
    return pd.DataFrame(
        [
            _check(
                "equivalence: first action pre/post vs repeats",
                in_pair - in_repeats,
                f">= -{EQUIVALENCE_TOLERANCE}",
                in_pair - in_repeats >= -EQUIVALENCE_TOLERANCE,
            ),
            _check("control: EOS share", eos, f">= {MIN_CONTROL_EOS}", eos >= MIN_CONTROL_EOS),
            _check(
                "primary condition, words: both correct and wrong outcomes",
                float(len(timeout_text)),
                "both present",
                has_both if len(timeout_text) else None,
            ),
        ]
    ).set_index("check")
