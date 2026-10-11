"""Markdown table of results_90d.json (python report.py > ../../docs/RESULTS_TABLE.md)."""
import collections
import json
import statistics
import sys
from pathlib import Path

NAMES = {"proposal:status-quo-gate": "Status quo: only insiders submit and review",
         "proposal:proposal": "Baseline: trust levels + credits",
         "synth:synth": "**any%5**",
         "agent1:mine": "Designer 1", "agent2:mine": "Designer 2", "agent3:mine": "Designer 3",
         "agent3:mine-robust": "Designer 3 (robust)", "agent4:mine": "Designer 4", "agent4:mine-hedged": "Designer 4 (hedged)",
         "agent5:mine": "Designer 5", "agent5:mine-balanced": "Designer 5 (balanced)", "agent6:mine": "Designer 6"}
path = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).with_name("results_90d.json"))
rows = json.loads(path.read_text())
days = 90
groups = collections.defaultdict(list)
for name, scenario, profile, seed, r, _ in rows:
    groups[(f"{name}:{scenario}", profile)].append(r)
mean = lambda rs, k: statistics.mean(r[k] for r in rs)


def shipped(rs, soak):
    per_day = mean(rs, "merged/day (last 7 d)")
    return 1e4 * mean(rs, f"shipped soak {soak}d") / (per_day * (days - soak)) if per_day else 0.0


print("| System | Merged/day | Median h to merge | Shipped backdoors per 10k merges, 14-day soak: eager / standard / patient attacker | Same, shipping on merge (standard) |")
print("|---|---:|---:|---|---:|")
for key in NAMES:
    if (key, "standard") not in groups:
        continue
    std = groups[(key, "standard")]
    cells = " / ".join(f"{shipped(groups[(key, p)], 14):.1f}" for p in ("eager", "standard", "patient"))
    print(f"| {NAMES[key]} | {mean(std, 'merged/day (last 7 d)'):.0f} | {mean(std, 'median hours to merge'):.0f} | {cells} | {shipped(std, 0):.1f} |")
