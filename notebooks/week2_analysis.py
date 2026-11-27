# ---
# jupyter:
#   jupytext:
#     formats: py:percent
#     text_representation:
#       extension: .py
#       format_name: percent
# ---

# %% [markdown]
# # Week 2: first runs
#
# Everything here comes from `results.csv` (one row per episode). Definitions of outcomes,
# pair error and thresholds: `DECISIONS.md` and `docs/delegation-and-exactly-once.md`.
#
# Set `RESULTS` to the `results.csv` files (or run directories) to analyse.
# Left empty, the notebook runs on synthetic debug data with known effects.

# %%
from pathlib import Path

import pandas as pd

from handoff_bench.analysis import report
from handoff_bench.analysis.synthetic import synthetic_results

RESULTS: list[Path] = []

df = report.load_results(*RESULTS) if RESULTS else synthetic_results(repeats=2)
pd.set_option("display.precision", 2)
print(f"{len(df)} episodes, {(df['outcome'] == 'invalid').sum()} invalid")
print("commits:", sorted(df["commit"].unique()))

# %% [markdown]
# ## Per cell
# Outcome shares among valid episodes; `invalid` is a count (API or stand failures).

# %%
report.cell_table(df)

# %%
report.first_actions(df)

# %% [markdown]
# ## Main number 1: main block where a check cannot help (long delay, no rules)
# Share of correct outcomes (EOS or honest "unknown") per channel, 95% Wilson interval,
# and the share of episodes where the orchestrator checked the bank itself.

# %%
report.main_by_channel(df, **report.PRIMARY)

# %% [markdown]
# Every condition: who pays (B0 single agent or delegated), delay, rules block, channel.

# %%
report.main_grid(df)

# %% [markdown]
# ## Main number 2: calibration (cells 2-5)
# Per variant: error in pre (skip) + error in post (duplicate) = 0, 1 or 2.
# The mean is over pairs where neither half said "unknown"; the unknown share is apart.

# %%
report.calibration_summary(df)

# %%
pairs = report.calibration_pairs(df)
pd.crosstab([pairs["channel"], pairs["first_action_pre"]], pairs["error"])

# %% [markdown]
# ## Predictions (preregistration)

# %%
report.predictions(df)

# %% [markdown]
# ## Pilot criteria
# Grader and coder against manual labels are checked by hand, not here.

# %%
report.pilot_checks(df)
