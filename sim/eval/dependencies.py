"""What happens when the people holding tasks others depend on stop responding, under several lease rules.

Tasks come in epics of 20 with prerequisites (2 hub tasks most of the epic depends on). A claim can go silent
(no heartbeat, no release) or zombie (heartbeats forever, never finishes). python dependencies.py -> dependencies_results.json
"""
import json
import statistics
import sys
import time
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

RULES = {
    "R0 current: 72 h lease, heartbeat renews": ({}, {}),
    "R1 24 h lease, heartbeat renews": ({"lease_impl": 24}, {}),
    "R2 72 h lease, only pushed progress renews": ({}, {"renew": "progress"}),
    "R3 R0 + 7-day cap, branch handed over": ({}, {"cap": 168, "push_p": 1.0}),
    "R4 R2 + 7-day cap + hubs first, 72 h cap on hubs": ({}, {"renew": "progress", "cap": 168, "hub_first": True,
                                                              "hub_cap": 72}),
    "R5 R4 + first pushed commit within 24 h": ({}, {"renew": "progress", "cap": 168, "hub_first": True,
                                                     "hub_cap": 72, "first_push": 24}),
}
BEHAVIOUR = [(0.0, 0.0), (0.05, 0.0), (0.2, 0.0), (0.5, 0.0), (0.05, 0.05), (0.2, 0.05)]


def job(args):
    import anyp5_sim as M
    rule, ghost, zombie, n, seed, days = args
    sim_extra, dag_extra = RULES[rule]
    cfg = dict(M.BASE, **M.SCENARIOS["synth"], days=days, seed=seed, patience_h=72)
    cfg["agents"] = n
    cfg["syn"] = dict(cfg["syn"], flat_reviews=True)
    cfg.update(sim_extra)
    cfg["dag"] = dict(dag_extra, ghost=ghost, zombie=zombie)
    t = time.time()
    r = M.simulate(cfg)
    x = r.pop("_x")
    return dict(rule=rule, ghost=ghost, zombie=zombie, n=n, seed=seed, runtime=time.time() - t,
                merged_day=r["merged/day (last 7 d)"], claims=x["claims"], **x["dag"])


def main():
    days = 90
    jobs = [(rule, g, z, 1000, s, days) for rule in RULES for g, z in BEHAVIOUR for s in range(1, 6)]
    jobs += [(rule, 0.2, 0.05, 10000, s, days) for rule in RULES for s in range(1, 3)]
    jobs.sort(key=lambda j: -j[3])
    with Pool(10, maxtasksperchild=8) as pool:
        results = pool.map(job, jobs, chunksize=1)
    (ROOT / "dependencies_results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    print("done", len(results))


if __name__ == "__main__":
    main()
