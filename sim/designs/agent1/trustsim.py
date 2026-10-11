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
    mine=False,
)

MINE = dict(
    mine=True,
    reviewers="new1-else-r2d1",  # sandboxed newcomers (trust < 5): 1 review; others: 2 reviews + 1 deep review; red: 3
    tier_level={"green": 0, "amber": 1, "red": 1},   # red/flagged work: any trust >= 5 reviewer with a clean canary record
    screen=True,               # canary record gates who may review/audit
    screen_rule="strict",
    canary_rehab=0.0,          # share of canaries sent to screened-out people so they can earn their way back
    rank_reviewers=False,      # tried: ranking reviewers by measured catch rate did not help (signal too noisy)
    resume_impl=True,          # abandoned implementation is handed over with its remaining hours
    audit_front=True,          # audits are served before new reviews
    canary_fail_audit=5,       # missing a canary sends the reviewer's last 5 approvals to audit
    audit_rate=0.0,
    audit_new_band=(5, 60, 0.1),  # post-merge audit rate for non-sandboxed authors below maintainer trust
    audit_level=1, audit_credit=2,
    debt=10**9,                # "jury duty": reviews are assigned first, credits never block an idle contributor
)

SCENARIOS = {
    "proposal": {},
    "mine": MINE,
    "mine-evidence-screen": dict(MINE, screen_rule="evidence", canary_rehab=0.3),
    "mine-no-newcomer-discount": dict(MINE, reviewers="all-r2d1"),
    "mine-no-discount-open-deep": dict(MINE, reviewers="all-r2d1", audit_level=0, tier_level=TIER_LEVEL),
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
}


REVIEW_POLICIES = {
    "three": lambda tier, a: 3,
    "new1-else3": lambda tier, a: 3 if tier == "red" else (1 if a.trust < 5 else 3),
    "one": lambda tier, a: 3 if tier == "red" else 1,
    "new1-else2": lambda tier, a: 3 if tier == "red" else (1 if a.trust < 5 else 2),
    "new1-else-r1d1": lambda tier, a: 3 if tier == "red" else (1 if a.trust < 5 else (1, 1)),
    "new1-else-r2d1": lambda tier, a: 3 if tier == "red" else (1 if a.trust < 5 else (2, 1)),
    "all-r2d1": lambda tier, a: 3 if tier == "red" else (2, 1),
    "new1-else-r1d2": lambda tier, a: 3 if tier == "red" else (1 if a.trust < 5 else (1, 2)),
    "new1-else-r2d2": lambda tier, a: 3 if tier == "red" else (1 if a.trust < 5 else (2, 2)),
    "new1-else-r0d2": lambda tier, a: 3 if tier == "red" else (1 if a.trust < 5 else (0, 2)),
}


class Agent:
    __slots__ = ("id", "kind", "ring", "skill", "diligence", "trust", "credits", "online", "gone", "banned",
                 "job", "merged", "approved", "attacking",
                 "can_ok", "can_bad", "reviews", "catches")

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
        self.can_ok = self.can_bad = self.reviews = self.catches = 0

    def rep(self):
        diligence = (self.can_ok + 1) / (self.can_ok + self.can_bad + 2)
        acuity = (self.catches + 1) / (self.reviews + 8)
        return diligence * acuity

    def screened_out(self, rule="strict"):
        if rule == "evidence":
            return self.can_bad >= 2 and self.can_bad > self.can_ok
        return self.can_bad >= 1 and (self.can_ok + 1) / (self.can_ok + self.can_bad + 2) < 0.6

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
    tiers = cfg.get("tiers", True)
    prs = []
    resume = deque()          # remaining hours of abandoned implementation work
    slots = deque()           # (pr, kind) waiting for a reviewer/auditor
    m = dict(attempts=0, caught_review=0, caught_audit=0, merged_backdoors=0, merged=0, canaries=0, canary_caught=0,
             abandoned=0, reverted=0, honest_banned=0, bad_banned=0, bugs_merged=0, latency=[], live_hours=[], prs=0)
    hours = cfg["days"] * 24

    def new_pr(author, now):
        a = agents[author]
        tier = rng.choices(("green", "amber", "red"), TASK_MIX[max(0, a.level(tiers))])[0]
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
        need = REVIEW_POLICIES[cfg["reviewers"]](tier, a) if cfg.get("reviewers") else TIER_REVIEWERS[tier]
        deep = 0
        if isinstance(need, tuple):
            need, deep = need
        pr.pending = need + deep
        for _ in range(deep):
            slots.append((pr, "deep"))
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
        lo, hi = cfg["audit_hours"] if work[0] in ("audit", "deep") else cfg["review_hours"]
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
            if cfg["contagion"] == "full":
                slash(rid, now)
            else:
                r.trust = r.trust * 0.5 - 5

    def finish_review(agent, pr, now, careful=False):
        agent.credits += cfg.get("audit_credit", 1) if careful else 1
        if pr.canary:
            if rng.random() < agent.diligence and rng.random() < 0.9:
                agent.trust += 1
                agent.can_ok += 1
                m["canary_caught"] += 1
            else:
                agent.trust -= 4
                agent.can_bad += 1
                if cfg.get("canary_fail_audit") and cfg["audits"]:
                    for old in agent.approved[-cfg["canary_fail_audit"]:]:
                        if old.found_at is None:
                            slots.appendleft((old, "audit"))
            return
        agent.reviews += 1
        agent.trust += 0.2
        if pr.rejected or pr.found_at is not None:
            return
        if detects(agent, pr, careful):
            agent.catches += 1
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
            rate = cfg["audit_rate"]
            band = cfg.get("audit_new_band")
            if band and band[0] <= author.trust < band[1]:
                rate += band[2]
            if cfg["audits"] and rng.random() < rate:
                if cfg.get("audit_front"):
                    slots.appendleft((pr, "audit"))
                else:
                    slots.append((pr, "audit"))

    def finish_audit(agent, pr, now):
        agent.credits += cfg.get("audit_credit", 1)
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
                    requeue(a.job, slots, a, cfg, resume)
                    a.job = None
                agents.append(Agent(len(agents), cfg, rng, ring_of))
                continue
            a.online = rng.random() >= cfg["p_offline"] if a.online else rng.random() < cfg["p_online"]
            if a.job and now > a.job[2]:
                m["abandoned"] += 1
                requeue(a.job, slots, a, cfg, resume)
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
                elif work[0] in ("review", "deep"):
                    finish_review(a, work[1], now, careful=work[0] == "deep")
                else:
                    finish_audit(a, work[1], now)

        if cfg["canary_every"] and m["prs"]:
            while m["canaries"] * cfg["canary_every"] < m["prs"]:
                canary(now)

        idle = [a for a in agents if a.online and not a.gone and not a.banned and a.job is None]
        rng.shuffle(idle)
        checkers = idle
        if cfg.get("screen"):
            checkers = [a for a in idle if not a.screened_out(cfg.get("screen_rule", "strict"))]
        if cfg.get("rank_reviewers"):
            checkers = sorted(checkers, key=Agent.rep)
        by_level = [[a for a in checkers if a.level(tiers) >= lv] for lv in range(4)]
        rehab = []
        if cfg.get("canary_rehab") and checkers is not idle:
            ok = {a.id for a in checkers}
            rehab = [a for a in idle if a.id not in ok]
        taken = set()
        for _ in range(len(slots)):
            pr, kind = slots.popleft()
            if pr.rejected and kind in ("review", "deep") or (kind == "audit" and pr.found_at is not None):
                continue
            need = cfg["audit_level"] if kind in ("audit", "deep") else (0 if pr.canary else cfg.get("tier_level", TIER_LEVEL)[pr.tier])
            pool = by_level[need]
            if pr.canary and rehab and rng.random() < cfg["canary_rehab"]:
                pool = rehab
            pick, skipped = None, []
            while pool:
                cand = pool.pop()
                if cand.id in taken:
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
            give(pick, (kind, pr), now)
        for a in idle:
            if a.id not in taken and a.job is None and a.credits - cfg["pr_cost"] >= -cfg["debt"]:
                a.credits -= cfg["pr_cost"]
                todo = resume.popleft() if resume else rng.uniform(*cfg["impl_hours"])
                a.job = [("impl",), todo, now + cfg["lease_impl"]]

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


def requeue(job, slots, agent=None, cfg=None, resume=None):
    work = job[0]
    if work[0] == "impl" and agent is not None and cfg["refund"]:
        agent.credits += cfg["pr_cost"]
    if work[0] == "impl" and cfg and cfg.get("resume_impl") and resume is not None:
        resume.append(max(1.0, job[1]))
    if work[0] in ("review", "audit", "deep"):
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
