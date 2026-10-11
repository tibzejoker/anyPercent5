"""Experiments for away.md and capabilities.md (capsim.py, built by make_capsim.py).

python make_capsim.py && python caps_away.py run && python caps_away.py report
"""
import json
import math
import statistics
import sys
import time
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "caps_away_results.json"
REC = dict(first_push=24, renew_h=72, cap=120, extension=True, hub_first=True)
WEEKEND = dict(weekend=0.5, wk_on=0.12, wk_off=0.2, wd_on=0.003)
CAPS = dict(caps=(0.15, 0.40))
MIX = dict(req_mix=(0.6, 0.15, 0.25))
LIARS = dict(liars=0.10, attackers_lie=True)

AWAY_WORLDS = {
    "honest": ({}, dict(ghost=0.0)),
    "silent 20 % + zombie 5 %": ({}, dict(ghost=0.2, zombie=0.05)),
    "weekend-only half": (WEEKEND, dict(ghost=0.05)),
    "fake pushers 5 %": ({}, dict(ghost=0.05, fake=0.05)),
}
AWAY_RULES = {"no away (recommended lease)": {}}
for block in ("none", "hubs", "takeover"):
    for mx in (3, 5, 7):
        for month in (7, 14):
            AWAY_RULES[f"away {mx} d, {month} d/month, block={block}"] = dict(away=True, away_max=mx, away_month=month,
                                                                              away_block=block)

AWAY_EXTRA = {
    "away 5 d, 7 d/month, takeover >= 1 dependent, needs a push": dict(away=True, away_max=5, away_month=7,
                                                                       away_block="takeover", takeover_deps=1,
                                                                       away_need_push=True),
    "away 7 d, 14 d/month, takeover >= 1 dependent, needs a push": dict(away=True, away_max=7, away_month=14,
                                                                        away_block="takeover", takeover_deps=1,
                                                                        away_need_push=True),
    "away 5 d, 21 d/month, takeover >= 1 dependent, needs a push": dict(away=True, away_max=5, away_month=21,
                                                                        away_block="takeover", takeover_deps=1,
                                                                        away_need_push=True),
    "away 5 d, 7 d/month, takeover >= 2 dependents, needs a push": dict(away=True, away_max=5, away_month=7,
                                                                        away_block="takeover", away_need_push=True),
    "away 5 d, 7 d/month, no away on hubs, needs a push": dict(away=True, away_max=5, away_month=7,
                                                               away_block="hubs", away_need_push=True),
}
AWAY_EXTRA2 = {
    "away 5 d, 7 d/month, takeover >= 1 dependent, needs a push, cap keeps running": dict(
        away=True, away_max=5, away_month=7, away_block="takeover", takeover_deps=1, away_need_push=True,
        away_pause_cap=False),
    "away 5 d, 21 d/month, takeover >= 1 dependent, needs a push, cap keeps running, away not counted in the progress check": dict(
        away=True, away_max=5, away_month=21, away_block="takeover", takeover_deps=1, away_need_push=True,
        away_pause_cap=False, away_shift_progress=True),
    "away 5 d, 21 d/month, takeover >= 1 dependent, needs a push, cap keeps running": dict(
        away=True, away_max=5, away_month=21, away_block="takeover", takeover_deps=1, away_need_push=True,
        away_pause_cap=False),
}
AWAY_RULES.update(AWAY_EXTRA)
AWAY_RULES.update(AWAY_EXTRA2)
AWAY_WORLDS["big tasks 10 % (40-120 h)"] = ({}, dict(ghost=0.05, big=0.1))

CAP_VARIANTS = {
    "no capabilities (everyone can do everything)": ({}, {}),
    "(a) claim matching, reviews open": (CAPS, MIX),
    "(b) claim matching, reviews restricted": (dict(CAPS, review_caps=True), MIX),
    "(c) (a) + artifact capture 70 %": (CAPS, dict(MIX, conv=0.7)),
    "(c) + restricted reviews": (dict(CAPS, review_caps=True), dict(MIX, conv=0.7)),
    "(d) (a) + liars": (dict(CAPS, **LIARS), MIX),
    "(d) (b) + liars": (dict(CAPS, review_caps=True, **LIARS), MIX),
    "(d) (c) + liars": (dict(CAPS, **LIARS), dict(MIX, conv=0.7)),
    "supply: maintainers only": (dict(n_maint=10), dict(supply_mode="maintainers")),
    "supply: anyone proposes (1-4 h review)": ({}, dict(supply_mode="propose")),
    "(c) + supply: maintainers only": (dict(CAPS, n_maint=10), dict(MIX, conv=0.7, supply_mode="maintainers")),
    "(c) + supply: anyone proposes": (CAPS, dict(MIX, conv=0.7, supply_mode="propose")),
}


def pct(xs, q):
    xs = sorted(xs)
    return xs[int(q * (len(xs) - 1))] if xs else None


def job(args):
    import capsim as M
    study, name, seed = args
    if study == "away":
        world, rule = name
        sim_extra, dag_extra = AWAY_WORLDS[world]
        dag_extra = dict(dag_extra, **AWAY_RULES[rule])
    else:
        sim_extra, dag_extra = CAP_VARIANTS[name]
        dag_extra = dict(dag_extra, ghost=0.05)
    cfg = dict(M.BASE, **M.SCENARIOS["synth"], days=90, seed=seed, patience_h=72, agents=1000)
    cfg["syn"] = dict(cfg["syn"], flat_reviews=True)
    cfg.update(sim_extra)
    cfg["dag"] = dict(REC, **dag_extra)
    r = M.simulate(cfg)
    x = r.pop("_x")
    d = x["dag"]
    hours = 90 * 24
    merged_before = sum(1 for h in x["merged_hour"] if h <= hours - 336)
    return dict(study=study, name=name if study == "caps" else list(name), seed=seed,
                merged=r["merged/day (last 7 d)"], med_h=statistics.median(x["lat"]) if x["lat"] else None,
                epic_med=d["epic_med"], epic_p95=d["epic_p95"], epic_open=d["epic_open"],
                lost_k=1000 * d["lost"] / max(1, d["honest_claims"]), nothing=d["nothing"], aways=d["aways"],
                away_h=d["away_h"], away_blocked=d["away_blocked"], liar_fail=d["liar_fail"],
                wait_full_med=d["wait_full_med"], wait_full_p95=d["wait_full_p95"], full_ready=d["full_ready"],
                zombie_hold=d["zombie_hold_med"],
                ship14=x["ship14"], merged_before=merged_before, careless=x["rlazy"] / max(1, x["rn"]),
                quality=x["rq"] / max(1, x["rn"]), bad_reviews=x["ring_reviews"], attempts=x["attempts"])


def run_extra():
    old = json.loads(OUT.read_text())
    jobs = [("away", (w, r), s) for w in AWAY_WORLDS for r in AWAY_EXTRA for s in range(1, 21)]
    with Pool(28, maxtasksperchild=40) as pool:
        res = pool.map(job, jobs, chunksize=2)
    OUT.write_text(json.dumps(old + res), encoding="utf-8")
    print(f"{len(res)} extra runs")


def run_extra2():
    old = json.loads(OUT.read_text())
    big = "big tasks 10 % (40-120 h)"
    done = {(tuple(r["name"]), r["seed"]) for r in old if r["study"] == "away"}
    jobs = [("away", (w, r), s) for w in AWAY_WORLDS for r in AWAY_EXTRA2 for s in range(1, 21)]
    jobs += [("away", (big, r), s) for r in ["no away (recommended lease)"] + list(AWAY_EXTRA)[:1]
             for s in range(1, 21)]
    jobs = [j for j in jobs if (j[1], j[2]) not in done]
    with Pool(28, maxtasksperchild=40) as pool:
        res = pool.map(job, jobs, chunksize=2)
    OUT.write_text(json.dumps(old + res), encoding="utf-8")
    print(f"{len(res)} extra runs")


def run():
    jobs = [("away", (w, r), s) for w in AWAY_WORLDS for r in AWAY_RULES for s in range(1, 21)]
    jobs += [("caps", v, s) for v in CAP_VARIANTS for s in range(1, 41)]
    t = time.time()
    with Pool(28, maxtasksperchild=40) as pool:
        res = pool.map(job, jobs, chunksize=2)
    OUT.write_text(json.dumps(res), encoding="utf-8")
    print(f"{len(res)} runs in {time.time() - t:.0f} s")


def ci(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return float("nan"), float("nan")
    mu = statistics.mean(xs)
    return mu, (1.96 * statistics.stdev(xs) / math.sqrt(len(xs)) if len(xs) > 1 else float("nan"))


def ship(rows):
    """Shipped backdoors per 10k merges at 14-day soak: pooled ratio, CI by bootstrap over seeds."""
    import random
    s, mb = sum(r["ship14"] for r in rows), sum(r["merged_before"] for r in rows)
    rng = random.Random(1)
    boot = []
    for _ in range(400):
        pick = [rng.choice(rows) for _ in rows]
        boot.append(1e4 * sum(r["ship14"] for r in pick) / max(1, sum(r["merged_before"] for r in pick)))
    boot.sort()
    return 1e4 * s / max(1, mb), boot[10], boot[389]


def report():
    res = json.loads(OUT.read_text())
    print("## away")
    print("| World | Rule | Merged/day | Feature p95 (d) | Unfinished 30 d | Honest leases lost /1k | Aways /run | Abuser hold med (h) |")
    print("|---|---|---|---|---|---|---|---|")
    for w in AWAY_WORLDS:
        for rule in AWAY_RULES:
            rows = [r for r in res if r["study"] == "away" and r["name"] == [w, rule]]
            if not rows:
                continue
            m, p, o, l = (ci([r[k] for r in rows]) for k in ("merged", "epic_p95", "epic_open", "lost_k"))
            print(f"| {w} | {rule} | {m[0]:.0f} ± {m[1]:.0f} | {p[0] / 24:.1f} ± {p[1] / 24:.1f} | {o[0]:.1f} % | "
                  f"{l[0]:.0f} | {statistics.mean(r['aways'] for r in rows):.0f} | "
                  f"{statistics.mean((r['zombie_hold'] or 0) for r in rows):.0f} |")
    print("\n## capabilities")
    print("| Variant | Merged/day | Median h to merge | Feature p95 (d) | Idle claim attempts | Full-set tasks waiting | "
          "Wait for full-set task med / p95 (h) | Liar failed claims | Careless review share | Backdoor reviews by bad actors | Backdoor attempts /run | Shipped per attempt | Shipped /10k (90 % CI) |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for v in CAP_VARIANTS:
        rows = [r for r in res if r["study"] == "caps" and r["name"] == v]
        m, h, p, n, fr, cl, br = (ci([r[k] for r in rows]) for k in
                                  ("merged", "med_h", "epic_p95", "nothing", "full_ready", "careless", "bad_reviews"))
        wf = ci([r["wait_full_med"] for r in rows])[0], ci([r["wait_full_p95"] for r in rows])[0]
        lf = statistics.mean(r["liar_fail"] for r in rows)
        sh = ship(rows)
        print(f"| {v} | {m[0]:.0f} ± {m[1]:.0f} | {h[0]:.1f} | {p[0] / 24:.1f} ± {p[1] / 24:.1f} | {n[0]:.0f} % | {fr[0]:.0f} | "
              f"{wf[0]:.0f} / {wf[1]:.0f} | {lf:.0f} | {100 * cl[0]:.1f} % | {br[0]:.1f} | {statistics.mean(r["attempts"] for r in rows):.1f} | {100 * sum(r["ship14"] for r in rows) / max(1, sum(r["attempts"] for r in rows)):.1f} % | {sh[0]:.1f} ({sh[1]:.1f}-{sh[2]:.1f}) |")


if __name__ == "__main__":
    {"run": run, "extra": run_extra, "extra2": run_extra2, "report": report}[sys.argv[1]]()
