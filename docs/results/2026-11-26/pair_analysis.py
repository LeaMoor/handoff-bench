"""Recompute descriptive intervals and exact paired tests from results.csv."""

import csv
from collections import defaultdict
from math import comb, sqrt
from pathlib import Path
from statistics import NormalDist

HERE = Path(__file__).resolve().parent
RUNS = HERE.parents[2] / "runs"
Z = NormalDist().inv_cdf(0.975)


def interval(k, n):
    p = k / n
    d = 1 + Z**2 / n
    mid = (p + Z**2 / (2 * n)) / d
    radius = Z * sqrt(p * (1 - p) / n + Z**2 / (4 * n * n)) / d
    return round(max(0, mid - radius) * 100, 2), round(min(1, mid + radius) * 100, 2)


def load(paths, cells):
    data = defaultdict(dict)
    for path in sorted(paths):
        with path.open(newline="") as stream:
            for row in csv.DictReader(stream):
                cell = int(row["cell"])
                if cell not in cells or row["outcome"] == "invalid":
                    continue
                world = tuple(row[k] for k in ("world", "template", "seed", "repeat"))
                # Same order and first-valid-copy rule as scripts/grid.py.
                data[row["model"], cell].setdefault(world, row)
    return data


def compare(label, model, data, a, b, metric="duplicate", event=None):
    left, right = data[model, a], data[model, b]
    if set(left) != set(right) or len(left) != 21:
        raise ValueError(f"Incomplete pairs: {label}, {model}, {a}, {b}")
    if event is None:

        def event(row):
            return row["outcome"] == metric

    pairs = [(event(left[w]), event(right[w])) for w in left]
    n = len(pairs)
    x, y = map(sum, zip(*pairs, strict=True))
    only_a = pairs.count((True, False))
    only_b = pairs.count((False, True))
    discord = only_a + only_b
    p = min(1, 2 * sum(comb(discord, i) for i in range(min(only_a, only_b) + 1)) / 2**discord)
    return dict(
        contrast=label,
        model=model,
        metric=metric,
        cell_A=a,
        cell_B=b,
        n_pairs=n,
        events_A=x,
        wilson_A_low=interval(x, n)[0],
        wilson_A_high=interval(x, n)[1],
        events_B=y,
        wilson_B_low=interval(y, n)[0],
        wilson_B_high=interval(y, n)[1],
        delta_B_minus_A_pp=round(100 * (y - x) / n, 2),
        only_A=only_a,
        only_B=only_b,
        fired_A=sum(r["fault_fired"] == "True" for r in left.values()),
        fired_B=sum(r["fault_fired"] == "True" for r in right.values()),
        mcnemar_two_sided=p,
    )


def main():
    out = []
    grid = [
        (31, 25, "M1 single vs delegated"),
        (25, 29, "M2 words vs ledger"),
        (29, 73, "M2 ledger vs warning"),
        (25, 19, "M7 words rules off vs on"),
    ]
    families = [
        (grid, RUNS.glob("prefreeze-*/results.csv")),
        (
            [(48, 52, "M4 respawn none vs intent"), (54, 58, "M4 redelivery none vs intent")],
            RUNS.glob("prefreeze-*-keys-hidden-v3/results.csv"),
        ),
    ]
    for comparisons, paths in families:
        data = load(paths, {c for a, b, _ in comparisons for c in (a, b)})
        models = sorted({model for model, _ in data})
        assert len(models) == 6
        for a, b, label in comparisons:
            for model in models:
                out.append(compare(label, model, data, a, b))
    for harness, a in (("cc", 90), ("hermes", 98), ("codex", 106)):
        data = load([RUNS / f"harness-{harness}-native" / "results.csv"], {a, a + 3})
        (model,) = {m for m, _ in data}
        out.append(compare(f"E3 {harness} none vs intent", model, data, a, a + 3))
    # A single conservative descriptive family for this exploratory export.
    running = 0.0
    for i, row in enumerate(sorted(out, key=lambda r: r["mcnemar_two_sided"])):
        running = max(running, (len(out) - i) * row["mcnemar_two_sided"])
        row["holm_all_exploratory"] = min(1.0, running)
    with (HERE / "pair_results.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=out[0])
        writer.writeheader()
        writer.writerows(out)


if __name__ == "__main__":
    main()
