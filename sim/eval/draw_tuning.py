"""Draw tuning: can the v0.3 review draw keep the reject-all ring out and recover v0.2's per-attempt safety?

python draw_tuning.py [agents] [days] [seeds] [population] [variant ...]
python draw_tuning.py --report

Builds build/anyp5_dt.py = false_rejects.py's model plus a configurable draw inside the eligible band:
- band: share of the idle pool, ranked by calibration score, that the draw picks from (v0.3: 0.5);
- weight_k: probability inside the band proportional to score^k (v0.3: 0, uniform);
- top_first: (tiers, share) the first reviewer of a change of these tiers comes from the top `share` of the pool.
"""
import importlib.util
import json
import statistics
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import false_rejects as fr

ROOT = Path(__file__).resolve().parent

EXTRA = [
    ('                return (a.cal_hit + 1) / (a.cal_n + 2) * (a.cal_ok + 1) / (a.cal_cn + 2)\n',
     '                clean = (a.cal_ok + 1) / (a.cal_cn + 2)\n'
     '                if syn.get("clean_gate") and clean >= syn["clean_gate"]:\n'
     '                    clean = 1.0\n'
     '                return (a.cal_hit + 1) / (a.cal_n + 2) * clean ** syn.get("clean_pow", 1)\n'),
    ('    def fr(agent):\n',
     '''    def pick_index(pool, pr, kind):
        n = len(pool)
        band = syn.get("band", 0.5)
        top = syn.get("top_first")
        if top and kind == "review" and pr.tier in top[0] and not pr.reviewers:
            band = top[1]
        size = max(1, min(n, int(n * band + 0.999)))
        k = syn.get("weight_k", 0)
        if not k:
            return rng.randrange(size)
        best = max(score(pool[0]), 1e-9)
        for _ in range(64):
            i = rng.randrange(size)
            if rng.random() < (score(pool[i]) / best) ** k:
                return i
        return 0

    def fr(agent):
'''),
    ('                    cand = pool.pop(rng.randrange((len(pool) + 1) // 2))\n',
     '                    cand = pool.pop(pick_index(pool, pr, kind))\n'),
]

V03 = fr.V03


def v03(**extra):
    return ("synth", dict(V03, syn_extra=dict(V03["syn_extra"], **extra)))


VARIANTS = {
    "v0.2": fr.SYSTEMS["any%5, v0.2 review rules"],
    "v0.3": fr.SYSTEMS["any%5, v0.3 review rules"],
    "band 40 %": v03(band=0.4),
    "band 30 %": v03(band=0.3),
    "band 20 %": v03(band=0.2),
    "half, score^2": v03(weight_k=2),
    "half, score^4": v03(weight_k=4),
    "band 30 %, score^2": v03(band=0.3, weight_k=2),
    "band 40 %, score^2": v03(band=0.4, weight_k=2),
    "half, score^2, amber/red first from top 10 %": v03(weight_k=2, top_first=(("amber", "red"), 0.1)),
    "half, amber/red first from top 10 %": v03(top_first=(("amber", "red"), 0.1)),
    "band 30 %, amber/red first from top 10 %": v03(band=0.3, top_first=(("amber", "red"), 0.1)),
    "v0.3 ranked (best first)": v03(rank=True, half=False),
    "clean canaries 20 %": ("synth", dict(V03, clean_share=0.2)),
    "canary every 2 PRs": ("synth", dict(V03, canary_every=2.0)),
    "canaries confirm perfect records first": v03(least_tested=False),
    "clean 20 %, confirm first": ("synth", dict(V03, clean_share=0.2, syn_extra=dict(V03["syn_extra"],
                                                                                      least_tested=False))),
    "band 30 %, score^2, clean 20 %, confirm first": (
        "synth", dict(V03, clean_share=0.2, syn_extra=dict(V03["syn_extra"], least_tested=False, band=0.3,
                                                          weight_k=2))),
    "band 30 %, score^2, confirm first": v03(least_tested=False, band=0.3, weight_k=2),
    "band 30 %, score^2, clean 30 %, confirm first": (
        "synth", dict(V03, clean_share=0.3, syn_extra=dict(V03["syn_extra"], least_tested=False, band=0.3,
                                                          weight_k=2))),
    "band 30 %, score^2, clean 20 % ^3, confirm first": (
        "synth", dict(V03, clean_share=0.2, syn_extra=dict(V03["syn_extra"], least_tested=False, band=0.3,
                                                          weight_k=2, clean_pow=3))),
    "band 30 %, score^2, clean 20 % ^2, confirm first": (
        "synth", dict(V03, clean_share=0.2, syn_extra=dict(V03["syn_extra"], least_tested=False, band=0.3,
                                                          weight_k=2, clean_pow=2))),
    "clean 20 % ^2, confirm first": (
        "synth", dict(V03, clean_share=0.2, syn_extra=dict(V03["syn_extra"], least_tested=False, clean_pow=2))),
    "clean 20 % ^3, confirm first": (
        "synth", dict(V03, clean_share=0.2, syn_extra=dict(V03["syn_extra"], least_tested=False, clean_pow=3))),
    "clean gate 0.5": v03(clean_gate=0.5),
    "band 30 %, score^2, clean gate 0.5, confirm first": v03(least_tested=False, band=0.3, weight_k=2, clean_gate=0.5),
    "ranked, clean 20 %, confirm first": (
        "synth", dict(V03, clean_share=0.2, syn_extra=dict(V03["syn_extra"], least_tested=False, rank=True,
                                                          half=False))),
}


def build():
    fr.build()
    src = (ROOT / "build" / "anyp5_fr.py").read_text(encoding="utf-8")
    for old, new in EXTRA:
        assert src.count(old) == 1, (old[:70], src.count(old))
        src = src.replace(old, new)
    (ROOT / "build" / "anyp5_dt.py").write_text(src, encoding="utf-8")


def job(args):
    system, population, seed, agents, days = args
    spec = importlib.util.spec_from_file_location("anyp5_dt", ROOT / "build" / "anyp5_dt.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    scenario, rules = VARIANTS[system]
    rules = dict(rules)
    cfg = dict(mod.BASE, **mod.SCENARIOS[scenario], **fr.COMMON, **fr.POPULATIONS[population])
    extra = rules.pop("syn_extra", None)
    cfg.update(rules)
    if extra:
        cfg["syn"] = dict(cfg["syn"], **extra)
    cfg.update(agents=agents, days=days, seed=seed, patience_h=3 * 24)
    t = time.time()
    r = mod.simulate(cfg)
    r.pop("_x", None)
    r.update({f"shipped soak {d}d": v for d, v in mod._SHIP.items()})
    return (system, population, seed, r, round(time.time() - t, 1))


if __name__ == "__main__":
    out = ROOT / "draw_tuning.json"
    seed0 = 1
    if "--seed0" in sys.argv:
        i = sys.argv.index("--seed0")
        seed0 = int(sys.argv[i + 1])
        del sys.argv[i:i + 2]
    if sys.argv[1:2] == ["--report"]:
        print(fr.report(json.loads(out.read_text(encoding="utf-8"))))
        sys.exit()
    agents, days, seeds = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    pick = sys.argv[4] if len(sys.argv) > 4 else "all"
    pops = [p for p in fr.POPULATIONS if pick == "all" or p.startswith(pick)]
    names = sys.argv[5:] or list(VARIANTS)
    build()
    t = time.time()
    seed_list = list(range(seed0, seed0 + seeds))
    jobs = [(s, p, seed, agents, days) for p in pops for s in names for seed in seed_list]
    with Pool(min(32, len(jobs))) as pool:
        rows = pool.map(job, jobs, chunksize=1)
    data = dict(setup=dict(agents=agents, days=days, seeds=seed_list, wall_s=round(time.time() - t),
                           max_run_s=max(r[4] for r in rows),
                           command="python draw_tuning.py " + " ".join(sys.argv[1:])),
                rows=rows)
    out.write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(fr.report(data))
