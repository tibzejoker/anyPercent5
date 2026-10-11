"""Statistical simulation of a leased-task / random-review / earned-trust contribution system.

No AI agents: each participant is a handful of numbers (skill, diligence, honest or not, online or not).
Time step: one hour. Usage: python trustsim.py [--agents 10000] [--days 30] [--seed 1] [--only NAME]
"""
import argparse
import random
import statistics
from collections import deque

LEVELS = (0, 5, 20, 60)                      # trust needed for level 0..3
TIER_LEVEL = {"green": 0, "amber": 1, "red": 2}
TIER_REVIEWERS = {"green": 2, "amber": 2, "red": 3}
TASK_MIX = ((1, 0, 0), (0.7, 0.3, 0), (0.5, 0.35, 0.15), (0.4, 0.4, 0.2))   # tasks handed out per level
VISIBILITY = {"green": 0.35, "amber": 0.6, "red": 0.9}

BASE = dict(
    agents=10000, days=30, seed=1,
    malicious=0.01, colluders=0.01, ring=5, lazy=0.2,
    p_online=0.05, p_offline=0.25, churn_days=20,
    impl_hours=(4, 16), review_hours=(1, 2), audit_hours=(2, 4),
    lease_impl=72, lease_review=24,
    pr_cost=2, start_credits=2, seed_trusted=0.02,
    honest_bug=0.15, attack_trust=5, attack_rate=0.5, tool_catches=0.3,
    canary_every=20, audit_rate=0.05, audit_level=2,
    revert_on_ban=True, heartbeat=True, refund=True, debt=4,
    random_reviewers=True, contagion="graduated", audits=True,
)

SCENARIOS = {
    "proposal": {},
    "no-heartbeat-no-debt": {"heartbeat": False, "refund": False, "debt": 0},
    "strong-audits": {"audit_rate": 0.2, "audit_level": 1},
    "tool-flags-80%": {"tool_catches": 0.8},
    "tool-80%+strong-audits": {"tool_catches": 0.8, "audit_rate": 0.2, "audit_level": 1},
    "no-revert-on-ban": {"revert_on_ban": False},
    "no-canaries": {"canary_every": 0},
    "no-audits": {"audits": False},
    "author-picks-reviewers": {"random_reviewers": False},
    "full-contagion": {"contagion": "full"},
    "no-trust-tiers": {"tiers": False},
    "mine": {"design": "mine"},
    "mine-balanced": {"design": "mine", "mine": {"audit_rate": {"green": 0.6, "amber": 0.3, "red": 0.05}}},
}

MINE = dict(
    mix=(0.5, 0.35, 0.15),                    # tier is a property of the files touched, same for everyone
    reviewers={"green": 1, "amber": 2, "red": 2},
    audit_rate={"green": 1.0, "amber": 0.5, "red": 0.05},   # post-merge, non-blocking
    qualified=0.5, gate=0.6, gate_trials=2, gate_wait=6, prior=(1, 2),
    canary_env=False, canary_every=20, audit_gate=False, audit_wait=24,   # canary hit-rate posterior thresholds
    suspend_hours=7 * 24, resample=0.5,       # approvers of a caught backdoor
    rotation=True,
)


class Agent:
    __slots__ = ("id", "kind", "ring", "skill", "diligence", "trust", "credits", "online", "gone", "banned",
                 "job", "merged", "approved", "attacking", "hits", "trials", "seed", "suspended", "seen")

    def __init__(self, i, cfg, rng, ring_of):
        self.id = i
        r = rng.random()
        self.kind = "malicious" if r < cfg["malicious"] else "colluder" if r < cfg["malicious"] + cfg["colluders"] else "honest"
        self.ring = ring_of(self) if self.kind == "colluder" else None
        self.skill = rng.uniform(0.4, 0.95)
        lazy = self.kind == "honest" and rng.random() < cfg["lazy"]
        self.diligence = 0.3 if lazy else 0.9
        self.trust = 0.0
        self.credits = cfg["start_credits"]
        self.online = rng.random() < 0.17
        self.gone = self.banned = False
        self.job = None
        self.merged, self.approved = [], []
        self.attacking = False
        self.hits = self.trials = 0
        self.seed = False
        self.suspended = -1
        self.seen = set()

    def acc(self, prior=(1, 2)):
        return (self.hits + prior[0]) / (self.trials + prior[1])

    def level(self, tiers=True):
        if not tiers:
            return 3
        return sum(self.trust >= t for t in LEVELS) - 1


class PR:
    __slots__ = ("id", "author", "tier", "flaw", "vis", "canary", "submitted", "reviewers", "pending", "done",
                 "approvals", "merged_at", "found_at", "rejected")

    def __init__(self, i, author, tier, flaw, vis, now, canary=False):
        self.id, self.author, self.tier, self.flaw, self.vis, self.canary = i, author, tier, flaw, vis, canary
        self.submitted = now
        self.reviewers, self.pending, self.done, self.approvals = [], 0, 0, []
        self.merged_at = self.found_at = None
        self.rejected = False


def simulate(cfg):
    rng = random.Random(cfg["seed"])
    rings = {"next": 0, "fill": 0}

    def ring_of(_):
        if rings["fill"] >= cfg["ring"]:
            rings["next"] += 1
            rings["fill"] = 0
        rings["fill"] += 1
        return rings["next"]

    agents = [Agent(i, cfg, rng, ring_of) for i in range(cfg["agents"])]
    for a in rng.sample(agents, int(cfg["seed_trusted"] * len(agents))):
        a.kind, a.ring, a.diligence, a.trust = "honest", None, 0.9, 60.0
        a.seed = True
    mine = cfg.get("design") == "mine"
    mc = dict(MINE, **cfg.get("mine", {}))
    tiers = cfg.get("tiers", True)
    prs = []
    slots = deque()           # (pr, kind) waiting for a reviewer/auditor
    m = dict(attempts=0, caught_review=0, caught_audit=0, merged_backdoors=0, merged=0, canaries=0, canary_caught=0,
             abandoned=0, reverted=0, honest_banned=0, bad_banned=0, bugs_merged=0, latency=[], live_hours=[], prs=0)
    hours = cfg["days"] * 24

    def new_pr(author, now):
        a = agents[author]
        tier = rng.choices(("green", "amber", "red"), mc["mix"] if mine else TASK_MIX[max(0, a.level(tiers))])[0]
        flaw, vis = None, VISIBILITY[tier]
        if a.kind != "honest" and a.trust >= cfg["attack_trust"] and rng.random() < cfg["attack_rate"]:
            flaw, tier = "backdoor", rng.choices(("green", "amber"), (0.7, 0.3))[0]
            vis = VISIBILITY[tier] * rng.uniform(0.5, 1.0)
            if rng.random() < cfg["tool_catches"]:
                tier, vis = "red", VISIBILITY["red"]
            m["attempts"] += 1
        elif rng.random() < cfg["honest_bug"]:
            flaw = "bug"
        pr = PR(len(prs), author, tier, flaw, vis, now)
        prs.append(pr)
        m["prs"] += 1
        need = (mc["reviewers"] if mine else TIER_REVIEWERS)[tier]
        pr.pending = need
        if not cfg["random_reviewers"] and a.kind == "colluder":
            mates = [b for b in agents if b.ring == a.ring and b.id != author and not b.banned and not b.gone
                     and b.online and b.job is None and b.level(tiers) >= TIER_LEVEL[tier]]
            for b in mates[:need]:
                give(b, ("review", pr), now)
                pr.reviewers.append(b.id)
            need -= len(mates[:need])
        for _ in range(need):
            slots.append((pr, "review"))

    def canary(now):
        pr = PR(len(prs), -1, "green", "backdoor", VISIBILITY["green"] * 1.4, now, canary=True)
        prs.append(pr)
        pr.pending = 1
        m["canaries"] += 1
        slots.append((pr, "review"))

    def give(agent, work, now):
        lo, hi = cfg["audit_hours"] if work[0] == "audit" else cfg["review_hours"]
        agent.job = [work, rng.uniform(lo, hi), now + cfg["lease_review"]]

    def detects(agent, pr, careful=False):
        if pr.flaw is None:
            return False
        if agent.kind == "colluder" and pr.author >= 0 and agents[pr.author].ring == agent.ring:
            return False
        diligence = 1.0 if careful else agent.diligence
        if rng.random() > diligence:
            return False
        return rng.random() < min(0.95, agent.skill * pr.vis * (1.5 if careful else 1.0))

    def slash(author_id, now, depth=0):
        a = agents[author_id]
        if a.banned:
            return
        a.banned, a.trust = True, 0.0
        if a.kind == "honest":
            m["honest_banned"] += 1
        else:
            m["bad_banned"] += 1
        if cfg["revert_on_ban"]:
            for pr in a.merged:
                if pr.found_at is None:
                    pr.found_at = now
                    if pr.flaw == "backdoor":
                        m["reverted"] += 1
                        m["live_hours"].append(now - pr.merged_at)
        if cfg["audits"]:
            for pr in a.merged + a.approved:
                if pr.found_at is None:
                    slots.appendleft((pr, "audit"))

    def found(pr, now, how):
        pr.found_at = now
        m["caught_" + how] += 1
        if pr.merged_at is not None:
            m["live_hours"].append(now - pr.merged_at)
        slash(pr.author, now)
        for rid in pr.approvals:
            r = agents[rid]
            if mine:
                r.suspended = now + mc["suspend_hours"]
                for q in r.approved:
                    if q.found_at is None and q is not pr and rng.random() < mc["resample"]:
                        slots.appendleft((q, "audit"))
                continue
            if cfg["contagion"] == "full":
                slash(rid, now)
            else:
                r.trust = r.trust * 0.5 - 5

    def finish_review(agent, pr, now):
        agent.credits += 1
        if pr.canary:
            agent.trials += 1
            if (detects(agent, pr) if mine and mc["canary_env"] else
                    rng.random() < agent.diligence and rng.random() < 0.9):
                agent.hits += 1
                agent.trust += 0.2 if mine else 1
                m["canary_caught"] += 1
            else:
                agent.trust += 0.2 if mine else -4
            return
        agent.trust += 0.2
        if pr.rejected or pr.found_at is not None:
            return
        if detects(agent, pr):
            pr.rejected = True
            if pr.flaw == "backdoor":
                found(pr, now, "review")
            return
        pr.approvals.append(agent.id)
        pr.done += 1
        if pr.done >= pr.pending:
            pr.merged_at = now
            m["merged"] += 1
            m["latency"].append(now - pr.submitted)
            author = agents[pr.author]
            author.merged.append(pr)
            author.trust += 1
            for rid in pr.approvals:
                agents[rid].approved.append(pr)
            if pr.flaw == "backdoor":
                m["merged_backdoors"] += 1
            elif pr.flaw == "bug":
                m["bugs_merged"] += 1
            if cfg["audits"] and rng.random() < (mc["audit_rate"][pr.tier] if mine else cfg["audit_rate"]):
                slots.append((pr, "audit"))

    def finish_audit(agent, pr, now):
        agent.credits += 1
        agent.trust += 0.5
        if pr.found_at is None and pr.flaw == "backdoor" and detects(agent, pr, careful=True):
            found(pr, now, "audit")

    for now in range(hours):
        for a in agents:
            if a.gone:
                continue
            if rng.random() < 1 / (cfg["churn_days"] * 24):
                a.gone = True
                if a.job:
                    m["abandoned"] += 1
                    requeue(a.job, slots, a, cfg)
                    a.job = None
                agents.append(Agent(len(agents), cfg, rng, ring_of))
                continue
            a.online = rng.random() >= cfg["p_offline"] if a.online else rng.random() < cfg["p_online"]
            if a.job and now > a.job[2]:
                m["abandoned"] += 1
                requeue(a.job, slots, a, cfg)
                a.job = None
            if not a.online or a.banned or not a.job:
                continue
            if cfg["heartbeat"]:
                a.job[2] = now + (cfg["lease_impl"] if a.job[0][0] == "impl" else cfg["lease_review"])
            a.job[1] -= 1
            if a.job[1] <= 0:
                work, a.job = a.job[0], None
                if work[0] == "impl":
                    new_pr(a.id, now)
                elif work[0] == "review":
                    finish_review(a, work[1], now)
                else:
                    finish_audit(a, work[1], now)

        every = mc["canary_every"] if mine else cfg["canary_every"]
        if every and m["prs"]:
            while m["canaries"] * every < m["prs"]:
                canary(now)

        idle = [a for a in agents if a.online and not a.gone and not a.banned and a.job is None]
        rng.shuffle(idle)
        if mine:
            def is_gate(a):
                return a.seed or (a.trials >= mc["gate_trials"] and a.acc(mc["prior"]) >= mc["gate"])
            ok = [a for a in idle if a.suspended < now and (a.trials == 0 or a.acc(mc["prior"]) >= mc["qualified"])]
            gate = [a for a in ok if is_gate(a)]
            fresh = sorted(idle, key=lambda a: -a.trials)
            pools = {"basic": ok, "gate": gate, "canary": fresh}
        else:
            by_level = [[a for a in idle if a.level(tiers) >= lv] for lv in range(4)]
        taken = set()
        for _ in range(len(slots)):
            pr, kind = slots.popleft()
            if pr.rejected and kind == "review" or (kind == "audit" and pr.found_at is not None):
                continue
            if mine:
                if pr.canary:
                    pool = pools["canary"]
                elif kind == "review" and pr.tier == "red" and now - pr.submitted < mc["gate_wait"] and not any(
                        is_gate(agents[r]) for r in pr.reviewers):
                    pool = pools["gate"]
                elif kind == "audit" and mc["audit_gate"] and now - pr.merged_at < mc["audit_wait"]:
                    pool = pools["gate"]
                else:
                    pool = pools["basic"]
            else:
                need = cfg["audit_level"] if kind == "audit" else (0 if pr.canary else TIER_LEVEL[pr.tier])
                pool = by_level[need]
            pick, skipped = None, []
            while pool:
                cand = pool.pop()
                if cand.id in taken:
                    continue
                if mine and mc["rotation"] and pr.author >= 0 and pr.author in cand.seen:
                    skipped.append(cand)
                    continue
                if cand.id != pr.author and cand.id not in pr.reviewers:
                    pick = cand
                    break
                skipped.append(cand)
            pool.extend(skipped)
            if pick is None:
                slots.append((pr, kind))
                continue
            taken.add(pick.id)
            pr.reviewers.append(pick.id)
            if mine and pr.author >= 0:
                pick.seen.add(pr.author)
            give(pick, (kind, pr), now)
        for a in idle:
            if a.id not in taken and a.job is None and a.credits - cfg["pr_cost"] >= -cfg["debt"]:
                a.credits -= cfg["pr_cost"]
                a.job = [("impl",), rng.uniform(*cfg["impl_hours"]), now + cfg["lease_impl"]]

    live = sum(1 for p in prs if p.flaw == "backdoor" and not p.canary and p.merged_at is not None and p.found_at is None)
    tail = [p for p in prs if p.merged_at is not None and p.merged_at >= hours - 7 * 24 and not p.canary]
    return {
        "merged/day (last 7 d)": round(len(tail) / 7),
        "median hours to merge": round(statistics.median(m["latency"]), 1) if m["latency"] else None,
        "backdoor attempts": m["attempts"],
        "stopped in review": m["caught_review"],
        "merged": m["merged_backdoors"],
        "found later by audit": m["caught_audit"],
        "reverted with author": m["reverted"],
        "still live at end": live,
        "backdoor-days live": round((sum(m["live_hours"]) + sum(hours - p.merged_at for p in prs if p.flaw == "backdoor"
                                    and not p.canary and p.merged_at is not None and p.found_at is None)) / 24),
        "canaries caught %": round(100 * m["canary_caught"] / max(1, m["canaries"])),
        "bad actors banned": m["bad_banned"],
        "honest banned": m["honest_banned"],
        "abandoned leases": m["abandoned"],
        "reviews waiting at end": len(slots),
    }


def requeue(job, slots, agent=None, cfg=None):
    work = job[0]
    if work[0] == "impl" and agent is not None and cfg["refund"]:
        agent.credits += cfg["pr_cost"]
    if work[0] in ("review", "audit"):
        slots.append((work[1], work[0]))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--agents", type=int, default=BASE["agents"])
    p.add_argument("--days", type=int, default=BASE["days"])
    p.add_argument("--seed", type=int, default=BASE["seed"])
    p.add_argument("--only")
    p.add_argument("--seeds", type=int, default=1)
    args = p.parse_args()
    results = {}
    for name, change in SCENARIOS.items():
        if args.only and name != args.only:
            continue
        runs = [simulate(dict(BASE, **change, agents=args.agents, days=args.days, seed=args.seed + k))
                for k in range(args.seeds)]
        results[name] = {k: round(statistics.mean(r[k] for r in runs)) if all(r[k] is not None for r in runs) else None
                         for k in runs[0]}
        print(name, results[name], flush=True)
    keys = list(next(iter(results.values())))
    width = max(map(len, keys))
    print("\n" + " " * width + "".join(f"{n[:14]:>16}" for n in results))
    for k in keys:
        print(f"{k:<{width}}" + "".join(f"{str(r[k]):>16}" for r in results.values()))


if __name__ == "__main__":
    main()
