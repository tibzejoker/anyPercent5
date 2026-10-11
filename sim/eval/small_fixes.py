"""Rule changes for small projects (10-300 people), where few reviewers are idle and ranking has no choice."""
import json
import statistics
import sys
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

FIXES = {
    "as published (flat)": ({}, {}),
    "green needs 2 reviewers": ({"tier_reviewers": {"green": 2, "amber": 3, "red": 2}}, {}),
    "post-merge review on 100 % of green": ({}, {"post_review": {"green": 1.0, "amber": 1.0, "red": 0.0}}),
    "both": ({"tier_reviewers": {"green": 2, "amber": 3, "red": 2}},
             {"post_review": {"green": 1.0, "amber": 1.0, "red": 0.0}}),
    "real reviews need score >= 0.5": ({}, {"juror_min": 0.5}),
    "real reviews need score >= 0.6": ({}, {"juror_min": 0.6}),
    "both + 1 canary per 5 changes": ({"tier_reviewers": {"green": 2, "amber": 3, "red": 2}, "canary_every": 5.0},
                                      {"post_review": {"green": 1.0, "amber": 1.0, "red": 0.0}}),
}
SIZES = {10: 60, 30: 40, 100: 20, 300: 10, 1000: 4}


def job(args):
    import anyp5_sim as M
    name, n, seed = args
    top, syn = FIXES[name]
    cfg = dict(M.BASE, **M.SCENARIOS["synth"], days=90, seed=seed, patience_h=72)
    cfg["agents"] = n
    cfg["syn"] = dict(cfg["syn"], flat_reviews=True, **syn)
    cfg.update(top)
    r = M.simulate(cfg)
    x = r.pop("_x")
    hours = 90 * 24
    return dict(name=name, n=n, seed=seed, merged_day=r["merged/day (last 7 d)"],
                med=statistics.median(x["lat"]) if x["lat"] else None,
                ship14=x["ship14"], merged_before=sum(1 for h in x["merged_hour"] if h <= hours - 336),
                careless=x["rlazy"] / max(1, x["rn"]))


if __name__ == "__main__":
    jobs = [(f, n, s) for f in FIXES for n, k in SIZES.items() for s in range(1, k + 1)]
    with Pool(10, maxtasksperchild=8) as pool:
        rows = pool.map(job, jobs, chunksize=2)
    (ROOT / "small_fixes_results.json").write_text(json.dumps(rows, indent=1))
    print("| Rule | N | Merged/day | Median h | Shipped /10k (14 d soak) |\n|---|---|---|---|---|")
    for f in FIXES:
        for n in SIZES:
            g = [r for r in rows if r["name"] == f and r["n"] == n]
            print(f"| {f} | {n:,} | {statistics.mean(r['merged_day'] for r in g):.1f} | "
                  f"{statistics.mean(r['med'] for r in g if r['med'] is not None):.1f} | "
                  f"{1e4 * sum(r['ship14'] for r in g) / max(1, sum(r['merged_before'] for r in g)):.1f} |")
