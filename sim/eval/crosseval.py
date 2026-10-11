"""Cross-evaluation of all designs with a design-independent attacker trigger (days since joining)."""
import importlib.util, json, re, sys, statistics, time
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SIM = ROOT.parent
SOURCES = {"proposal": SIM / "trustsim.py",
           **{f"agent{i}": SIM / f"designs/agent{i}/trustsim.py" for i in range(1, 7)},
           "synth": SIM / "designs/synth/trustsim.py"}
RUNS = [("proposal", "status-quo-gate"), ("proposal", "proposal"), ("synth", "synth"), ("agent1", "mine"), ("agent2", "mine"), ("agent3", "mine"), ("agent3", "mine-robust"),
        ("agent4", "mine"), ("agent4", "mine-hedged"), ("agent5", "mine"), ("agent5", "mine-balanced"), ("agent6", "mine")]
PROFILES = {"eager": 0, "standard": 3, "patient": 15}


def patched(name):
    (ROOT / "build").mkdir(exist_ok=True)
    out = ROOT / "build" / f"{name}_eval.py"
    src = SOURCES[name].read_text(encoding="utf-8")
    src = re.sub(r"def simulate\(cfg\):\n", "def simulate(cfg):\n    JOINED = {}\n", src, count=1)
    n = 0
    for cond in (r'a\.trust >= cfg\["attack_trust"\]', r'ready >= cfg\["attack_trust"\]'):
        src, k = re.subn(cond, 'now - JOINED.get(a.id, 0) >= cfg["patience_h"]', src)
        n += k
    src, k2 = re.subn(r"agents\.append\(Agent\(len\(agents\), cfg, rng, ring_of\)\)",
                      "agents.append(Agent(len(agents), cfg, rng, ring_of)); JOINED[len(agents) - 1] = now", src)
    assert n == 1 and k2 >= 1, (name, n, k2)
    ship = ("    globals()['_SHIP'] = {d: sum(1 for p in prs if p.flaw == 'backdoor' and not p.canary and p.merged_at is not None"
            " and p.merged_at <= hours - d * 24 and (p.found_at is None or p.found_at - p.merged_at > d * 24)) for d in (0, 7, 14)}\n")
    src, k3 = re.subn(r"\n    live = sum\(", lambda _: "\n" + ship + "    live = sum(", src, count=1)
    assert k3 == 1, (name, "live")
    out.write_text(src, encoding="utf-8")
    return out


def job(args):
    name, scenario, profile, seed, days = args
    spec = importlib.util.spec_from_file_location(f"{name}_eval", ROOT / "build" / f"{name}_eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cfg = dict(mod.BASE, **mod.SCENARIOS[scenario], days=days, seed=seed, patience_h=PROFILES[profile] * 24)
    t = time.time()
    r = mod.simulate(cfg)
    r.update({f"shipped soak {d}d": v for d, v in mod._SHIP.items()})
    return (name, scenario, profile, seed, r, time.time() - t)


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    seeds = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    for name in SOURCES:
        patched(name)
    jobs = [(n, s, p, seed, days) for n, s in RUNS for p in PROFILES for seed in range(1, seeds + 1)]
    with Pool(12) as pool:
        results = pool.map(job, jobs)
    (ROOT / f"results_{days}d.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    print("done", len(results))
