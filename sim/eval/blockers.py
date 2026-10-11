"""Serial blockers: a project where each blocker hides the next (a game that stops at its first crash).

Hourly model, 90 days. Members are online about 17 % of the time and 15 % have the full capability set
(Windows + the game + GPU). Revealing blocker j needs every earlier blocker fixed or bypassed, then a run by a
full-set member (1 h). A fix takes 4-16 h; 60 % of fixes need the full set (with artifact capture, 70 % of those
become capture 1-2 h full set -> fix without the game x1.2 -> verify 1 h full set). A merged fix lands 1-4 h
after the work (review).
Task supply after a run reveals a blocker: auto (from the run log, instant), propose (the runner writes it,
reviewed 1-4 h), maintainers (1 % of members, min 2, must be online to write it, 0.5 h).
Bypass hack (depth D > 1): a full-set member writes a temporary workaround (2-4 h, works for 70 % of blockers) so
the next blocker can be revealed while this one is being fixed; at most D blockers known and unfixed at once.
When a bypassed blocker's real fix lands, each later fix already merged is reopened with probability 0.1
(30 % of its work redone). 10 % of claims go silent and come back after 24 h.
Relay: when a holder of a fix goes offline, the fix goes back to the pool with its branch (ramp-up 1 h + 20 % of
the work done), so whoever is online continues it.

python blockers.py run && python blockers.py report
"""
import heapq
import json
import math
import random
import statistics
import sys
import time
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "blockers_results.json"


def simulate(n, depth, supply, conv, seed, relay=False, days=90):
    rng = random.Random(seed)
    full = [rng.random() < 0.15 for _ in range(n)]
    n_maint = max(2, n // 100)
    for i in range(n_maint):
        full[i] = True
    online = [rng.random() < 0.17 for _ in range(n)]
    busy = [None] * n            # (task, hours left)
    blockers = []                # dicts: state hidden/known/fixed, bypassed, bypass_failed
    tasks = []                   # ready tasks: dict(kind, b, req, hours)
    later = []                   # (ready_at, seq, task): delayed tasks (review of a proposal, silent claims)
    merges = []                  # (merge_at, seq, blocker index, task)
    run_pending = False
    seq = 0
    stats = dict(idle=0, online=0, full_busy=0, full_online=0, fixed=0, reopened=0, bypasses=0, parallel=[])
    maint_queue = []             # blockers revealed, waiting for a maintainer to write the task

    def first_unfixed():
        for i, b in enumerate(blockers):
            if b["state"] != "fixed":
                return i
        return len(blockers)

    def frontier_open():
        """Can the next hidden blocker be revealed? Every known blocker must be fixed or bypassed."""
        known = [b for b in blockers if b["state"] != "fixed"]
        if len(known) >= depth:
            return False
        return all(b["state"] == "fixed" or b["bypassed"] for b in blockers)

    def fix_task(i):
        hours = rng.uniform(4, 16)
        if rng.random() < 0.6:
            if rng.random() < conv:
                return dict(kind="capture", b=i, req=True, hours=rng.uniform(1, 2), main=hours * 1.2)
            return dict(kind="fix", b=i, req=True, hours=hours)
        return dict(kind="fix", b=i, req=False, hours=hours)

    def publish(i, now):
        nonlocal seq
        t = fix_task(i)
        if supply == "auto":
            tasks.append(t)
        elif supply == "propose":
            seq += 1
            heapq.heappush(later, (now + 0.5 + rng.uniform(1, 4), seq, t))
        else:
            maint_queue.append(t)
        if depth > 1:
            if rng.random() < 0.7:
                tasks.append(dict(kind="bypass", b=i, req=True, hours=rng.uniform(2, 4)))

    for now in range(days * 24):
        while later and later[0][0] <= now:
            tasks.append(heapq.heappop(later)[2])
        while merges and merges[0][0] <= now:
            _, _, i, t = heapq.heappop(merges)
            b = blockers[i]
            if t["kind"] in ("fix", "verify"):
                b["state"] = "fixed"
                stats["fixed"] += 1
                if b["bypassed"]:
                    for j in range(i + 1, len(blockers)):
                        if blockers[j]["state"] == "fixed" and rng.random() < 0.1:
                            blockers[j]["state"] = "known"
                            stats["fixed"] -= 1
                            stats["reopened"] += 1
                            tasks.append(dict(kind="fix", b=j, req=rng.random() < 0.6, hours=rng.uniform(1.2, 4.8)))
        if not run_pending and frontier_open():
            run_pending = True
            tasks.append(dict(kind="run", b=None, req=True, hours=1))
        for k in range(n):
            was = online[k]
            online[k] = rng.random() >= 0.25 if online[k] else rng.random() < 0.05
            if relay and was and not online[k] and busy[k] is not None and busy[k][0]["kind"] in ("fix", "fixmain"):
                t, left = busy[k]
                tasks.append(dict(t, hours=left + 1 + 0.2 * (t["hours"] - left)))
                busy[k] = None
        if supply == "maintainers" and maint_queue and any(online[k] for k in range(n_maint)):
            for t in maint_queue:
                tasks.append(t)
            maint_queue.clear()
        tasks[:] = [t for t in tasks if not (t["kind"] == "bypass" and blockers[t["b"]]["state"] == "fixed")]
        order = sorted(tasks, key=lambda t: ({"run": 0, "bypass": 1, "capture": 2, "verify": 2}.get(t["kind"], 3),
                                            t["b"] if t["b"] is not None else -1))
        tasks[:] = order
        known_open = sum(1 for b in blockers if b["state"] != "fixed")
        stats["parallel"].append(known_open)
        for k in range(n):
            if not online[k]:
                continue
            stats["online"] += 1
            if full[k]:
                stats["full_online"] += 1
            if busy[k] is None:
                pick = next((t for t in tasks if full[k] or not t["req"]), None)
                if pick is None:
                    stats["idle"] += 1
                    continue
                tasks.remove(pick)
                if rng.random() < 0.10:
                    seq += 1
                    heapq.heappush(later, (now + 24, seq, pick))
                    continue
                busy[k] = [pick, pick["hours"]]
            t = busy[k][0]
            if full[k]:
                stats["full_busy"] += 1
            busy[k][1] -= 1
            if busy[k][1] > 0:
                continue
            busy[k] = None
            if t["kind"] == "run":
                run_pending = False
                blockers.append(dict(state="known", bypassed=False))
                publish(len(blockers) - 1, now)
            elif t["kind"] == "bypass":
                blockers[t["b"]]["bypassed"] = True
                stats["bypasses"] += 1
            elif t["kind"] == "capture":
                tasks.append(dict(kind="fixmain", b=t["b"], req=False, hours=t["main"]))
            elif t["kind"] == "fixmain":
                seq += 1
                heapq.heappush(later, (now + rng.uniform(1, 4), seq, dict(kind="verify", b=t["b"], req=True,
                                                                          hours=rng.uniform(0.5, 1.5))))
            else:
                seq += 1
                heapq.heappush(merges, (now + rng.uniform(1, 4), seq, t["b"], t))
    hours = days * 24
    return dict(fixed=stats["fixed"], revealed=len(blockers), reopened=stats["reopened"], bypasses=stats["bypasses"],
                parallel=statistics.mean(stats["parallel"]),
                idle=100 * stats["idle"] / max(1, stats["online"]),
                full_busy=100 * stats["full_busy"] / max(1, stats["full_online"]),
                days_per_blocker=days / max(1, stats["fixed"]))


def job(args):
    n, depth, supply, conv, relay, seed = args
    return dict(n=n, depth=depth, supply=supply, conv=conv, relay=relay, seed=seed,
                **simulate(n, depth, supply, conv, seed, relay))


def run():
    jobs = [(n, d, s, c, rl, seed) for n in (100, 1000) for d in (1, 3, 5, 10) for s in ("maintainers", "propose", "auto")
            for c in (0.0, 0.7) for rl in (False, True) for seed in range(1, 21)]
    t = time.time()
    with Pool(28, maxtasksperchild=20) as pool:
        res = pool.map(job, jobs, chunksize=4)
    OUT.write_text(json.dumps(res), encoding="utf-8")
    print(f"{len(res)} runs in {time.time() - t:.0f} s")


def report():
    res = json.loads(OUT.read_text())
    print("| Members | Bypass depth | Task supply | Artifact capture | Relay | Blockers fixed in 90 d | Blockers worked in parallel | Reopened by bypass | Idle online hours | Full-set members busy |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    keys = sorted({(r["n"], r["depth"], r["supply"], r["conv"], r["relay"]) for r in res})
    for k in keys:
        rows = [r for r in res if (r["n"], r["depth"], r["supply"], r["conv"], r["relay"]) == k]
        f = [r["fixed"] for r in rows]
        mu, half = statistics.mean(f), 1.96 * statistics.stdev(f) / math.sqrt(len(f))
        print(f"| {k[0]} | {'none' if k[1] == 1 else k[1]} | {k[2]} | {'yes' if k[3] else 'no'} | {'yes' if k[4] else 'no'} | {mu:.1f} ± {half:.1f} | "
              f"{statistics.mean(r['parallel'] for r in rows):.1f} | {statistics.mean(r['reopened'] for r in rows):.1f} | "
              f"{statistics.mean(r['idle'] for r in rows):.0f} % | {statistics.mean(r['full_busy'] for r in rows):.0f} % |")


if __name__ == "__main__":
    {"run": run, "report": report}[sys.argv[1]]()
