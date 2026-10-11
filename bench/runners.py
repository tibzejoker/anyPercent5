"""Runner-hours per day needed to check contributed changes, by strategy (bench/RUNNERS.md).

A deterministic model with stated assumptions, plus a Monte Carlo of merge-queue batching with bisection.
Assumptions (AnyPS5-like C++ project):
  cold build 10 min + tests 3 min = 13 min per run; warm cache + affected targets only: 2 + 3 = 5 min
  submissions = merges x 1.15 (13 % rejected in review); 3 % of approved changes break the build or tests
"""
import math
import random

COLD, WARM = 13 / 60, 5 / 60          # hours per CI run
REJECTED = 1.15
BREAKS = 0.03
SPOT = 0.10                            # share of evidence bundles CI re-runs


def queue_runs(n_changes, batch, breaks=BREAKS, seed=1):
    """CI runs to land n_changes through a merge queue that tests `batch` changes together and bisects a red batch."""
    rng = random.Random(seed)
    runs = 0

    def test(group):
        nonlocal runs
        runs += 1
        if not any(group):
            return
        if len(group) == 1:
            return
        mid = len(group) // 2
        test(group[:mid])
        test(group[mid:])

    left = n_changes
    while left > 0:
        size = min(batch, left)
        test([rng.random() < breaks for _ in range(size)])
        left -= size
    return runs


def strategies(merges):
    subs = merges * REJECTED
    per_change = {b: queue_runs(20000, b) / 20000 for b in (1, 4, 8, 16, 32)}
    # one queue lane runs batches back to back: it must land a day of merges in 24 h
    feasible = [b for b in per_change if merges / b * WARM <= 24] or [max(per_change)]
    best_b = min(feasible, key=per_change.get)
    rows = [
        ("CI on every submission, cold build", subs * COLD),
        ("CI on every submission, warm cache + affected targets", subs * WARM),
        (f"merge queue only (batches of {best_b}, bisect on red)", merges * per_change[best_b] * WARM),
        (f"evidence bundles + {int(SPOT * 100)} % spot checks + merge queue ({best_b})",
         subs * SPOT * WARM + merges * per_change[best_b] * WARM),
        ("volunteer runners, every job on 2 machines (free hardware)", 2 * subs * WARM),
    ]
    return rows, per_change, best_b


def deterrence(gain, loss):
    """Spot-check rate above which a false evidence bundle has negative expected value: q > G / (G + L)."""
    return gain / (gain + loss)


def main():
    capacity = {"Free/Pro public, 20-40 concurrent": 20 * 24, "Team public, 60 concurrent": 60 * 24,
                "Enterprise, 500 concurrent": 500 * 24, "private Free, 2,000 min/month Linux": 2000 / 60 / 30,
                "private Free, Windows (2x minutes)": 1000 / 60 / 30}
    print("Batch bisection, CI runs per landed change:",
          {b: round(r, 3) for b, r in strategies(100)[1].items()})
    print()
    print("| Merges/day | Strategy | Runner-hours/day | Share of a Free plan's 480 h/day (20 concurrent) |")
    print("|---|---|---:|---:|")
    for m in (10, 100, 2400, 24000):
        rows, _, _ = strategies(m)
        for name, hours in rows:
            print(f"| {m:,} | {name} | {hours:,.1f} | {100 * hours / 480:.1f} % |")
    print()
    for name, h in capacity.items():
        print(f"capacity {name}: {h:,.1f} runner-hours/day")
    print()
    print("Deterrence spot-check rate q = G / (G + L):")
    for gain, loss, what in ((0.25, 20, "lazy member skipping 15 min of local tests, loses 20 h of merged work"),
                             (0.25, 1, "newcomer, loses 1 h of work"),
                             (10, 1, "attacker landing a change CI would reject, throwaway account")):
        print(f"  {what}: q > {deterrence(gain, loss):.1%}")


if __name__ == "__main__":
    main()
