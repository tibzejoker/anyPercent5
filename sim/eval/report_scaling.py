"""Markdown tables from scaling_results.json and dependencies_results.json."""
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def mean(rows, key):
    vals = [r[key] for r in rows if r[key] is not None]
    return statistics.mean(vals) if vals else None


def f(v, nd=0):
    if v is None:
        return "-"
    return f"{v:,.{nd}f}"


def scaling():
    rows = json.loads((ROOT / "scaling_results.json").read_text())
    groups = defaultdict(list)
    for r in rows:
        if not r["growth"]:
            groups[(r["variant"], r["n"])].append(r)
    out = ["| Variant | N | Seeds | Online | Merged/day | Merged/day per online | Median h | p95 h | Backlog | "
           "Shipped /10k (14 d soak) | Runs with an attacker | Careless review share | Review quality | "
           "Commands/h (peak) | Sim s/run |", "|" + "---|" * 15]
    for (v, n), g in sorted(groups.items(), key=lambda kv: (kv[0][0] != "flat", kv[0][1])):
        merged = sum(r["merged_before"] for r in g)
        shipped = sum(r["ship14"] for r in g)
        out.append(f"| {v} | {n:,} | {len(g)} | {f(mean(g, 'online'), 1)} | {f(mean(g, 'merged_day'), 1)} | "
                   f"{f(mean(g, 'merged_day') / max(0.01, mean(g, 'online')), 2)} | {f(mean(g, 'med'), 1)} | "
                   f"{f(mean(g, 'p95'))} | {f(mean(g, 'backlog'), 1)} | {f(1e4 * shipped / max(1, merged), 1)} | "
                   f"{sum(r['bad_ever'] > 0 for r in g)}/{len(g)} | {f(100 * mean(g, 'careless'))} % | "
                   f"{f(mean(g, 'quality'), 2)} | {f(mean(g, 'cmd_mean') + mean(g, 'heartbeats'))} "
                   f"({f(max(r['cmd_max'] for r in g))}) | {f(mean(g, 'runtime'), 1)} |")
    print("\n".join(out))
    for v in ("flat", "synth"):
        g = [r for r in rows if r["growth"] and r["variant"] == v]
        if not g:
            continue
        print(f"\nGrowth 10 -> 10,000 people over 120 days, variant {v}, {len(g)} seeds")
        print("| Day | Online | Merged/day | Median h | p95 h | Backlog | Commands/h |\n|---|---|---|---|---|---|---|")
        for i in range(len(g[0]["series"])):
            s = [r["series"][i] for r in g]
            print(f"| {s[0]['day']} | {f(mean(s, 'online'))} | {f(mean(s, 'merged_day'), 1)} | {f(mean(s, 'med'), 1)} | "
                  f"{f(mean(s, 'p95'))} | {f(mean(s, 'backlog'), 1)} | {f(mean(s, 'cmd'))} |")


def deps():
    rows = json.loads((ROOT / "dependencies_results.json").read_text())
    for n in sorted({r["n"] for r in rows}):
        groups = defaultdict(list)
        for r in rows:
            if r["n"] == n:
                groups[(r["ghost"], r["zombie"], r["rule"])].append(r)
        print(f"\nN = {n:,}")
        print("| Silent | Zombie | Rule | Merged/day | Blocked wait med / p95 (d) | Epic med / p95 (d) | Epics open after 30 d | "
              "Hub task ready->merged med / p95 (d) | Wasted work | Honest leases lost /1k claims |\n|" + "---|" * 10)
        for (gh, z, rule), g in sorted(groups.items()):
            print(f"| {int(gh * 100)} % | {int(z * 100)} % | {rule} | {f(mean(g, 'merged_day'))} | "
                  f"{f(mean(g, 'wait_med') / 24, 1)} / {f(mean(g, 'wait_p95') / 24, 1)} | "
                  f"{f(mean(g, 'epic_med') / 24, 1)} / {f(mean(g, 'epic_p95') / 24, 1)} | {f(mean(g, 'epic_open'), 1)} % | "
                  f"{f(mean(g, 'hub_med') / 24, 1)} / {f(mean(g, 'hub_p95') / 24, 1)} | {f(mean(g, 'wasted'), 1)} % | "
                  f"{f(1000 * mean(g, 'lost') / max(1, mean(g, 'claims')), 1)} |")


if __name__ == "__main__":
    {"scaling": scaling, "deps": deps}[sys.argv[1]]()
