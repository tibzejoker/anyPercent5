"""More seeds for the small-project rule variants (backdoor counts are small, so the noise is large)."""
import json, statistics, sys
from multiprocessing import Pool
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from small_fixes import job

NAMES = ["as published (flat)", "post-merge review on 100 % of green", "both", "real reviews need score >= 0.5",
         "real reviews need score >= 0.6"]
SIZES = {10: 400, 30: 200, 100: 80, 1000: 4}

if __name__ == "__main__":
    jobs = [(f, n, s) for f in NAMES for n, k in SIZES.items() for s in range(1000, 1000 + k)]
    with Pool(10, maxtasksperchild=50) as pool:
        rows = pool.map(job, jobs, chunksize=8)
    print("| Rule | N | Seeds | Merged/day | Median h | Shipped backdoors (count) | Shipped /10k (14 d soak) | Careless share |\n|---|---|---|---|---|---|---|---|")
    for f in NAMES:
        for n in SIZES:
            g = [r for r in rows if r["name"] == f and r["n"] == n]
            ship = sum(r["ship14"] for r in g)
            print(f"| {f} | {n} | {len(g)} | {statistics.mean(r['merged_day'] for r in g):.1f} | "
                  f"{statistics.mean(r['med'] for r in g if r['med'] is not None):.1f} | {ship} | "
                  f"{1e4 * ship / max(1, sum(r['merged_before'] for r in g)):.1f} | "
                  f"{100 * statistics.mean(r['careless'] for r in g):.0f} % |")
