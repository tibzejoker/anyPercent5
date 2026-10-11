"""How any%5 behaves from 10 to 100k contributors, and in a project growing from 10 to 10k.

python scaling.py [days=90]  -> scaling_results.json, then report() prints the tables used in scaling.md
"""
import json
import statistics
import sys
import time
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

SIZES = {10: 40, 30: 30, 100: 20, 300: 10, 1000: 5, 3000: 4, 10000: 3, 30000: 2, 100000: 1}
VARIANTS = {
    "synth": {},                                         # as published: amber/red reviewers need trust levels 1/2
    "flat": {"flat": True},                              # engine as coded: any calibrated member reviews any tier
}


def pct(xs, q):
    xs = sorted(xs)
    return xs[int(q * (len(xs) - 1))] if xs else None


def cfg_for(variant, n, seed, days, growth=None, extra=None):
    import anyp5_sim as M
    cfg = dict(M.BASE, **M.SCENARIOS["synth"], days=days, seed=seed, patience_h=72)
    cfg["agents"] = n
    if VARIANTS.get(variant, {}).get("flat"):
        cfg["syn"] = dict(cfg["syn"], flat_reviews=True)
    if growth:
        cfg["growth"] = growth
    cfg.update(extra or {})
    return cfg


def job(args):
    import anyp5_sim as M
    variant, n, seed, days, growth, extra = args
    cfg = cfg_for(variant, n, seed, days, growth, extra)
    t = time.time()
    r = M.simulate(cfg)
    x = r.pop("_x")
    hours = days * 24
    last = slice(hours - 7 * 24, hours)
    merged_before = sum(1 for h in x["merged_hour"] if h <= hours - 336)
    out = dict(variant=variant, n=n, seed=seed, growth=growth, extra=extra, runtime=time.time() - t,
               merged_day=r["merged/day (last 7 d)"], active=x["active"],
               online=statistics.mean(x["online"][last]),
               med=statistics.median(x["lat"]) if x["lat"] else None, p95=pct(x["lat"], 0.95),
               backlog=statistics.mean(x["backlog"][last]), backlog_max=max(x["backlog"]),
               ship14=x["ship14"], merged_before=merged_before, attempts=x["attempts"], bad_ever=x["bad_ever"],
               careless=x["rlazy"] / max(1, x["rn"]), quality=x["rq"] / max(1, x["rn"]),
               calibrated=x["calibrated"] / max(1, x["active"]),
               canaries=x["canaries"], canary_caught=x["canary_caught"],
               cmd_mean=statistics.mean(x["cmd_hour"][last]), cmd_max=max(x["cmd_hour"][last]),
               heartbeats=x["work_hours"] / hours / 4,
               banned_bad=r["bad actors banned"], honest_banned=r["honest banned"])
    if growth:
        series = []
        for d0 in range(0, days, 10):
            lo, hi = d0 * 24, (d0 + 10) * 24
            lats = [l for h, l in zip(x["merged_hour"], x["lat"]) if lo <= h < hi]
            series.append(dict(day=d0, online=statistics.mean(x["online"][lo:hi]), merged_day=len(lats) / 10,
                               med=statistics.median(lats) if lats else None, p95=pct(lats, 0.95),
                               backlog=statistics.mean(x["backlog"][lo:hi]),
                               cmd=statistics.mean(x["cmd_hour"][lo:hi])))
        out["series"] = series
    return out


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    jobs = [(v, n, s, days, None, None) for v in VARIANTS for n, seeds in SIZES.items() for s in range(1, seeds + 1)
            if not (v == "flat" and n > 30000)]
    jobs += [(v, 10, s, 150, (10, 10000, 120), None) for v in VARIANTS for s in range(1, 4)]
    jobs.sort(key=lambda j: -(j[1] if not j[4] else 6000))
    with Pool(10, maxtasksperchild=4) as pool:
        results = pool.map(job, jobs, chunksize=1)
    (ROOT / "scaling_results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    print("done", len(results))


if __name__ == "__main__":
    main()
