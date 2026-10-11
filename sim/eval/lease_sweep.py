"""Benchmark the lease parameters: first push due (F), renewal per push (R) and hard cap (C), across six worlds.

python make_lease_sim.py && python lease_sweep.py coarse && python lease_sweep.py refine && python lease_sweep.py report
Results: lease_sweep_results.json; report: lease_sweep.md tables (printed).
"""
import itertools
import json
import math
import statistics
import sys
import time
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "lease_sweep_results.json"

WORLDS = {
    "honest": ({}, dict(ghost=0.0, zombie=0.0)),
    "silent 20 % + zombie 5 %": ({}, dict(ghost=0.2, zombie=0.05)),
    "silent 50 %": ({}, dict(ghost=0.5)),
    "weekend-only half": (dict(weekend=0.5, wk_on=0.12, wk_off=0.2, wd_on=0.003), dict(ghost=0.05)),
    "big tasks 10 % (40-120 h)": ({}, dict(ghost=0.05, big=0.1)),
    "fake pushers 5 %": ({}, dict(ghost=0.05, fake=0.05)),
}
F_GRID = (6, 12, 24, 48, 72)
R_GRID = (24, 48, 72, 120)
C_GRID = (72, 120, 168, 240, 336, None)
CURRENT = (24, 72, 168, False)
N, DAYS = 1000, 90


def job(args):
    import lease_sim as M
    world, (F, R, C, ext), seed = args[:3]
    sens = args[3] if len(args) > 3 else {}
    sim_extra, dag_extra = WORLDS[world]
    cfg = dict(M.BASE, **M.SCENARIOS["synth"], days=DAYS, seed=seed, patience_h=72, agents=N)
    cfg["syn"] = dict(cfg["syn"], flat_reviews=True)
    cfg.update(sim_extra)
    cfg["dag"] = dict(dag_extra, first_push=F, renew_h=R, cap=C, extension=ext, hub_first=True, **sens)
    r = M.simulate(cfg)
    x = r.pop("_x")
    d = x["dag"]
    hc = max(1, d["honest_claims"])
    return dict(world=world, F=F, R=R, C=C, ext=ext, seed=seed, sens=json.dumps(sens, sort_keys=True),
                merged=r["merged/day (last 7 d)"],
                epic_med=d["epic_med"], epic_p95=d["epic_p95"], epic_open=d["epic_open"],
                lost_k=1000 * d["lost"] / hc, lost_cap_k=1000 * d["lost_cap"] / hc,
                lost_lapse_k=1000 * d["lost_lapse"] / hc, handover=d["handover"],
                big_n=d["big_n"], big_open=d["big_open"], big_p95=d["big_p95"])


def run(jobs, procs=28):
    old = json.loads(OUT.read_text()) if OUT.exists() else []
    t = time.time()
    with Pool(procs, maxtasksperchild=40) as pool:
        new = pool.map(job, jobs, chunksize=4)
    OUT.write_text(json.dumps(old + new), encoding="utf-8")
    print(f"{len(new)} runs in {time.time() - t:.0f} s")


def extra():
    rules = [(F, R, C, True) for F, R, C in itertools.product((24, 36, 48), (72, 96), (96, 120, 144))]
    jobs = [(w, rule, s) for rule in rules for w in WORLDS for s in range(9, 33)]
    finalists = [(24, 72, 120, True), (12, 72, 120, True), (36, 96, 120, True), (24, 72, 168, True), CURRENT]
    for sens in ({"handover_frac": 0.05, "handover_fixed": 1.0}, {"handover_frac": 0.4, "handover_fixed": 4.0},
                 {"ext_detect": 0.5}):
        jobs += [(w, rule, s, sens) for rule in finalists for w in WORLDS for s in range(9, 25)]
    run(jobs)


def key(r):
    return (r["F"], r["R"], r["C"], r["ext"])


def table(results, sens="{}"):
    """(rule, world) -> {metric: (mean, half-width of the 95 % CI)}, for one sensitivity setting."""
    groups = {}
    for r in results:
        if r.get("sens", "{}") != sens:
            continue
        groups.setdefault((key(r), r["world"]), []).append(r)
    out = {}
    for k, rows in groups.items():
        stats = {}
        for m in ("merged", "epic_med", "epic_p95", "epic_open", "lost_k", "lost_cap_k", "lost_lapse_k",
                  "handover", "big_open", "big_n"):
            xs = [r[m] for r in rows if r[m] is not None]
            mu = statistics.mean(xs) if xs else float("nan")
            ci = 1.96 * statistics.stdev(xs) / math.sqrt(len(xs)) if len(xs) > 1 else float("nan")
            stats[m] = (mu, ci)
        stats["n"] = len(rows)
        out[k] = stats
    return out


def regrets(tab, rules):
    """Per rule and world: the worse of two regrets against the best rule of that world,
    p95 feature delay (delay / best - 1) and throughput (best merged per day / merged - 1)."""
    rules = [r for r in rules if all((r, w) in tab for w in WORLDS)]
    best_p95 = {w: min(tab[(r, w)]["epic_p95"][0] for r in rules) for w in WORLDS}
    best_m = {w: max(tab[(r, w)]["merged"][0] for r in rules) for w in WORLDS}
    return {r: {w: max(tab[(r, w)]["epic_p95"][0] / best_p95[w] - 1, best_m[w] / tab[(r, w)]["merged"][0] - 1)
                for w in WORLDS} for r in rules}


def coarse():
    rules = [(F, R, C, False) for F, R, C in itertools.product(F_GRID, R_GRID, C_GRID)]
    run([(w, rule, s) for rule in rules for w in WORLDS for s in range(1, 9)])


def refine():
    results = json.loads(OUT.read_text())
    tab = table(results)
    rules = sorted({key(r) for r in results}, key=str)
    reg = regrets(tab, rules)
    top = sorted(reg, key=lambda r: max(reg[r].values()))[:8]
    extra = set(top) | {CURRENT}
    extra |= {(F, R, C, True) for F, R, C, _ in list(extra) if C}
    extra |= {(F, R, C, True) for F, R, C in itertools.product((6, 12, 24), (48, 72, 120), (120, 168, 240, 336))}
    done = {(key(r), r["world"]) for r in results if r["seed"] > 8}
    run([(w, rule, s) for rule in sorted(extra, key=str) for w in WORLDS for s in range(9, 33)
         if (rule, w) not in done])


def label(r):
    F, R, C, ext = r
    cap = "none" if C is None else f"{C // 24} d"
    return f"{F} h / {R} h / {cap}{' + ext' if ext else ''}"


def report():
    results = json.loads(OUT.read_text())
    tab = table(results)
    rules = sorted({key(r) for r in results}, key=str)
    deep = [r for r in rules if all(tab.get((r, w), {}).get("n", 0) >= 20 for w in WORLDS)]
    reg = regrets(tab, rules)
    print("## coarse ranking (all rules, 8 seeds): worst-world regret (p95 feature delay or throughput)")
    ranked = sorted(reg, key=lambda r: max(reg[r].values()))
    for r in ranked[:15] + [CURRENT]:
        print(f"| {label(r)} | {100 * max(reg[r].values()):.0f} % | " +
              " | ".join(f"{100 * reg[r][w]:.0f}" for w in WORLDS) + " |")
    print("\n## deep rules (>= 20 seeds): per world")
    dreg = regrets(tab, deep)
    for r in sorted(deep, key=lambda r: max(dreg[r].values())):
        print(f"\n### {label(r)}  worst regret {100 * max(dreg[r].values()):.0f} %")
        for w in WORLDS:
            s = tab[(r, w)]
            f = lambda m, d=0: f"{s[m][0]:.{d}f} ± {s[m][1]:.{d}f}"
            print(f"| {w} | {f('merged')} | {s['epic_med'][0] / 24:.1f} | {s['epic_p95'][0] / 24:.1f} ± "
                  f"{s['epic_p95'][1] / 24:.1f} | {f('epic_open', 1)} | {f('lost_cap_k')} | {f('lost_lapse_k')} | "
                  f"{f('handover', 1)} | {s['big_open'][0]:.0f}/{s['big_n'][0]:.0f} |")


if __name__ == "__main__":
    {"coarse": coarse, "refine": refine, "extra": extra, "report": report}[sys.argv[1]]()
