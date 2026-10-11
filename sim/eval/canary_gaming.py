"""Can reviewers game the canaries? any%5 (synth) under canary gamers, fair-eval harness (python canary_gaming.py [days] [seeds]).

Gamers: every attacker and colluder, plus a share of lazy reviewers. A gamer rejects a canary it recognises, approves
everything else without looking (real reviews, post-merge reviews and audits alike), and so climbs the calibration
ranking when it recognises canaries. Canary sources:
  merged     merged diff + defect: found by a git-history lookup, always recognised
  variant    copy of a real pending change + defect, served only blind: recognised when the gamer, or a gamer of its
             group, authored or was assigned the original; otherwise with p_variant
  synthetic  agent-made change: a classifier recognises it with p_synth
"""
import importlib.util
import json
import statistics
import sys
import time
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import crosseval  # noqa: E402

PROFILES = crosseval.PROFILES
GAME_DEFAULTS = dict(game=None, gamer_lazy=0.5, coord="ring", p_variant=0.05, p_synth=0.3, rand_slot=0.0)
COUNTERS = dict(rand_slot=0.25, syn={"approver_recal": True})
SCENARIOS = {
    "no gaming (reference)": {},
    "merged canaries": dict(game="merged"),
    "pending-variant canaries (rings coordinate)": dict(game="variant"),
    "pending-variant canaries (all attackers coordinate)": dict(game="variant", coord="all"),
    "synthetic canaries (30% recognised)": dict(game="synthetic"),
    "merged + random reviewer slot 25%": dict(game="merged", rand_slot=0.25),
    "merged + approver recalibration": dict(game="merged", syn={"approver_recal": True}),
    "merged + both counters": dict(game="merged", **COUNTERS),
    "pending-variant (all) + both counters": dict(game="variant", coord="all", **COUNTERS),
    "synthetic + both counters": dict(game="synthetic", **COUNTERS),
    "no gaming + both counters": dict(**COUNTERS),
}

PATCHES = [
    ("    JOINED = {}\n", '''    JOINED = {}
    GAME = cfg.get("game")
    G = dict(rn=0, rg=0, q=0.0, cg=0, cgc=0)
    ORIG = {}
    _gam = {}

    def gamer(a):
        if not GAME:
            return False
        g = _gam.get(a.id)
        if g is None:
            g = a.kind != "honest" or (a.diligence < 0.5 and random.Random(cfg["seed"] * 1000003 + a.id).random() < cfg["gamer_lazy"])
            _gam[a.id] = g
        return g

    def group(a):
        if a.kind == "colluder":
            return ("ring", a.ring) if cfg["coord"] == "ring" else "bad"
        if a.kind == "malicious" and cfg["coord"] == "all":
            return "bad"
        return ("solo", a.id)

    def recognizes(a, pr):
        if GAME == "merged":
            return True
        if GAME == "synthetic":
            return rng.random() < cfg["p_synth"]
        o = ORIG.get(pr.id)
        if o is not None:
            gid = group(a)
            for rid in o.reviewers + [o.author]:
                if rid == a.id or (gamer(agents[rid]) and group(agents[rid]) == gid):
                    return True
        return rng.random() < cfg["p_variant"]
'''),
    ('        pr = PR(len(prs), -1, "green", "backdoor", vis, now, canary=True)\n',
     '''        pr = PR(len(prs), -1, "green", "backdoor", vis, now, canary=True)
        if GAME == "variant":
            for p in reversed(prs[-300:]):
                if not p.canary and p.merged_at is None and not p.rejected:
                    ORIG[pr.id] = p
                    break
'''),
    ("    def finish_review(agent, pr, now):\n        agent.credits += 1\n",
     '''    def finish_review(agent, pr, now):
        agent.credits += 1
        if pr.canary and gamer(agent):
            agent.cal_n += 1
            G["cg"] += 1
            if recognizes(agent, pr):
                agent.cal_hit += 1
                m["canary_caught"] += 1
                agent.trust += 1
                G["cgc"] += 1
            else:
                agent.trust -= 4
            return
'''),
    ('        dbg["rlazy"] = dbg.get("rlazy", 0) + (agent.diligence < 0.5)\n',
     '''        dbg["rlazy"] = dbg.get("rlazy", 0) + (agent.diligence < 0.5)
        G["rn"] += 1
        if gamer(agent):
            G["rg"] += 1
        else:
            G["q"] += agent.skill * agent.diligence
'''),
    ("        if pr.flaw is None:\n            return False\n",
     '''        if pr.flaw is None:
            return False
        if not pr.canary and gamer(agent):
            return False
'''),
    ("                cand = pool.pop()\n",
     '''                if cfg["rand_slot"] and not pr.canary and len(pool) > 1 and rng.random() < cfg["rand_slot"]:
                    cand = pool.pop(rng.randrange(len(pool)))
                else:
                    cand = pool.pop()
'''),
    ("    globals()['_SHIP']", "    globals()['_GAME'] = dict(G)\n    globals()['_SHIP']"),
]


def build():
    src = crosseval.patched("synth").read_text(encoding="utf-8")
    for old, new in PATCHES:
        assert src.count(old) == 1, old
        src = src.replace(old, new)
    out = ROOT / "build" / "synth_game.py"
    out.write_text(src, encoding="utf-8")
    return out


def job(args):
    scenario, profile, seed, days = args
    spec = importlib.util.spec_from_file_location("synth_game", ROOT / "build" / "synth_game.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    change = dict(SCENARIOS[scenario])
    syn = dict(mod.SCENARIOS["synth"]["syn"], **change.pop("syn", {}))
    cfg = dict(mod.BASE, **mod.SCENARIOS["synth"], **GAME_DEFAULTS)
    cfg.update(change, syn=syn, days=days, seed=seed, patience_h=PROFILES[profile] * 24)
    t = time.time()
    r = mod.simulate(cfg)
    r.update({f"shipped soak {d}d": v for d, v in mod._SHIP.items()})
    r["game"] = mod._GAME
    return (scenario, profile, seed, r, time.time() - t)


def report(rows, days):
    groups = {}
    for scenario, profile, _, r, _ in rows:
        groups.setdefault((scenario, profile), []).append(r)
    mean = lambda rs, f: statistics.mean(f(r) for r in rs)

    def shipped(rs, soak):
        per_day = mean(rs, lambda r: r["merged/day (last 7 d)"])
        return 1e4 * mean(rs, lambda r: r[f"shipped soak {soak}d"]) / (per_day * (days - soak)) if per_day else 0.0

    attempts = lambda rs: mean(rs, lambda r: r["backdoor attempts"])
    leak = lambda rs: mean(rs, lambda r: r["shipped soak 14d"]) / attempts(rs)
    ref = next(iter(SCENARIOS))

    def normalized(scenario, p):
        rs = groups[(scenario, p)]
        return shipped(rs, 14) * attempts(groups[(ref, p)]) / attempts(rs)

    lines = ["| Scenario | Merged/day | Median h | Shipped / 10k merges, raw (eager / standard / patient) "
             "| Backdoor attempts (std) | Leak per attempt (std) | Shipped / 10k merges at reference attempt count "
             "| Look quality per real review | Real reviews by gamers | Canaries recognised by gamers |",
             "|---|---:|---:|---|---:|---:|---|---:|---:|---:|"]
    for scenario in SCENARIOS:
        std = groups[(scenario, "standard")]
        raw = " / ".join(f"{shipped(groups[(scenario, p)], 14):.1f}" for p in PROFILES)
        norm = " / ".join(f"{normalized(scenario, p):.1f}" for p in PROFILES)
        q = mean(std, lambda r: r["game"]["q"] / max(1, r["game"]["rn"]))
        share = mean(std, lambda r: r["game"]["rg"] / max(1, r["game"]["rn"]))
        rec = mean(std, lambda r: r["game"]["cgc"] / r["game"]["cg"] if r["game"]["cg"] else 0.0)
        lines.append(f"| {scenario} | {mean(std, lambda r: r['merged/day (last 7 d)']):.0f} | "
                     f"{mean(std, lambda r: r['median hours to merge']):.0f} | {raw} | {attempts(std):.0f} | "
                     f"{100 * leak(std):.1f} % | {norm} | {q:.2f} | {100 * share:.1f} % | {100 * rec:.0f} % |")
    return "\n".join(lines)


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    seeds = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    if len(sys.argv) > 3 and sys.argv[3] == "--report":
        print(report(json.loads((ROOT / f"canary_gaming_{days}d.json").read_text(encoding="utf-8")), days))
        sys.exit()
    build()
    jobs = [(s, p, seed, days) for s in SCENARIOS for p in PROFILES for seed in range(1, seeds + 1)]
    with Pool(24) as pool:
        rows = pool.map(job, jobs, chunksize=1)
    (ROOT / f"canary_gaming_{days}d.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(report(rows, days))
