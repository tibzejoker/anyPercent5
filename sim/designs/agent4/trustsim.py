"""Statistical simulation of a leased-task / random-review / earned-trust contribution system.

No AI agents: each participant is a handful of numbers (skill, diligence, honest or not, online or not).
Time step: one hour. Usage: python trustsim.py [--agents 10000] [--days 30] [--seed 1] [--only NAME]

Scenario `mine` (agent4 design, see ../DESIGN.md) only changes the RULES of the system through the
`mine_*` keys below; every world parameter (who participants are, how they behave, detection model)
is shared with `proposal`, whose behaviour is unchanged (all new keys default to the proposal rules).
"""
import argparse
import random
import statistics
from collections import deque

LEVELS = (0, 5, 20, 60)                      # trust needed for level 0..3
CUR_LEVELS = list(LEVELS)                    # thresholds in force (mine_relative_levels moves 2 and 3 daily)
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
    # ---- rules added for scenario "mine"; defaults reproduce the proposal rules exactly ----
    mine_reviewers=None,        # dict tier -> number of independent random reviewers
    mine_newbie_extra=0,        # extra reviewer when the author is level 0
    mine_senior=False,          # amber/red changes need >= 1 reviewer of level >= mine_senior_level
    mine_senior_level=2,
    mine_audit=None,            # dict tier -> post-merge audit probability (replaces audit_rate)
    mine_probation=0,
    mine_probation_reviewers=0, # if > 0, probation adds reviewers before merge instead of post-merge audits           # merges audited at 100 % right after an author reaches a new level
    mine_audit_first=False,     # audits jump the queue (they are the safety net, never starve)
    mine_audit_credits=1,       # credits paid for an audit
    mine_vest_hours=0,          # author trust for a merge is granted only after this quarantine
    mine_review_trust=0.2,      # trust per plain review
    mine_canary_ban=0,          # hours of review ban after approving a canary
    mine_taint=0.0,             # when a backdoor is found, share of each approver's approvals re-audited
    mine_resume=False,          # lapsed implementation leases resume (remaining hours) instead of restarting
    mine_lease_mult=0,          # lease = mult * job duration (0 = fixed lease_impl / lease_review)
    mine_lease_min=0,
    mine_handoff=1,
    mine_relative_levels=None,  # (q2, q3): levels 2/3 = trust quantiles of active members, capped at LEVELS
    mine_hedge=0,               # hours a review/audit holder may be silent before a backup reviewer is drafted             # hours lost re-reading a resumed branch
)

MINE = dict(
    mine_reviewers={"green": 2, "amber": 2, "red": 3},
    mine_newbie_extra=0,
    mine_senior=True,
    mine_audit={"green": 0.05, "amber": 0.05, "red": 0.10},
    mine_probation=6,
    mine_audit_first=True,
    mine_audit_credits=2,
    mine_vest_hours=7 * 24,
    mine_review_trust=0.1,
    mine_canary_ban=7 * 24,
    mine_taint=0.25,
    mine_resume=True,
    mine_relative_levels=(0.85, 0.97),
    audit_level=2,
    lease_review=48,
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
    "mine": MINE,
    "mine-no-vesting": dict(MINE, mine_vest_hours=0),
    "mine-no-probation": dict(MINE, mine_probation=0),
    "mine-hedged": dict(MINE, mine_hedge=4),
}


class Agent:
    __slots__ = ("id", "kind", "ring", "skill", "diligence", "trust", "credits", "online", "gone", "banned",
                 "job", "merged", "approved", "attacking", "max_level", "probation", "review_ban")

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
        self.max_level = 0
        self.probation = 0
        self.review_ban = -1

    def level(self, tiers=True):
        if not tiers:
            return 3
        return sum(self.trust >= t for t in CUR_LEVELS) - 1


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
    CUR_LEVELS[:] = LEVELS
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
        a.max_level = a.level(cfg.get("tiers", True))
    tiers = cfg.get("tiers", True)
    prs = []
    slots = deque()           # (pr, kind) waiting for a reviewer/auditor
    orphans = deque()         # remaining hours of lapsed implementation leases (mine_resume)
    vesting = deque()         # (due hour, pr) author trust waiting for the quarantine to pass
    m = dict(attempts=0, caught_review=0, caught_audit=0, merged_backdoors=0, merged=0, canaries=0, canary_caught=0,
             abandoned=0, reverted=0, honest_banned=0, bad_banned=0, bugs_merged=0, latency=[], live_hours=[], prs=0)
    hours = cfg["days"] * 24
    hrs = {}
    reviewers = cfg["mine_reviewers"] or TIER_REVIEWERS

    def lease_for(kind, duration):
        if cfg["mine_lease_mult"]:
            return max(cfg["mine_lease_min"], cfg["mine_lease_mult"] * duration)
        return cfg["lease_impl"] if kind == "impl" else cfg["lease_review"]

    def queue_audit(pr, front=False):
        if front or cfg["mine_audit_first"]:
            slots.appendleft((pr, "audit"))
        else:
            slots.append((pr, "audit"))

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
        lv = a.level(tiers)
        if lv > a.max_level:
            a.max_level = lv
            a.probation = cfg["mine_probation"]
        need = reviewers[tier] + (cfg["mine_newbie_extra"] if lv == 0 else 0)
        if a.probation > 0 and cfg["mine_probation_reviewers"]:
            need += cfg["mine_probation_reviewers"]
            a.probation -= 1
        pr.pending = need
        if not cfg["random_reviewers"] and a.kind == "colluder":
            mates = [b for b in agents if b.ring == a.ring and b.id != author and not b.banned and not b.gone
                     and b.online and b.job is None and b.level(tiers) >= TIER_LEVEL[tier]]
            for b in mates[:need]:
                give(b, ("review", pr), now)
                pr.reviewers.append(b.id)
            need -= len(mates[:need])
        senior = cfg["mine_senior"] and tier != "green"
        for k in range(need):
            slots.append((pr, "review_sr" if senior and k == 0 else "review"))

    def canary(now):
        pr = PR(len(prs), -1, "green", "backdoor", VISIBILITY["green"] * 1.4, now, canary=True)
        prs.append(pr)
        pr.pending = 1
        m["canaries"] += 1
        slots.append((pr, "review"))

    def give(agent, work, now):
        lo, hi = cfg["audit_hours"] if work[0] == "audit" else cfg["review_hours"]
        d = rng.uniform(lo, hi)
        lease = lease_for(work[0], d)
        agent.job = [work, d, now + lease, lease, False, now]

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
                if cfg["mine_taint"] and cfg["audits"] and not r.banned:
                    for q in r.approved:
                        if q.found_at is None and rng.random() < cfg["mine_taint"]:
                            queue_audit(q, front=True)

    def finish_review(agent, pr, now):
        agent.credits += 1
        if pr.canary:
            if rng.random() < agent.diligence and rng.random() < 0.9:
                agent.trust += 1
                m["canary_caught"] += 1
            else:
                agent.trust -= 4
                if cfg["mine_canary_ban"]:
                    agent.review_ban = now + cfg["mine_canary_ban"]
            return
        agent.trust += cfg["mine_review_trust"]
        if pr.rejected or pr.found_at is not None or pr.merged_at is not None:
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
            if cfg["mine_vest_hours"]:
                vesting.append((now + cfg["mine_vest_hours"], pr))
            else:
                author.trust += 1
            for rid in pr.approvals:
                agents[rid].approved.append(pr)
            if pr.flaw == "backdoor":
                m["merged_backdoors"] += 1
            elif pr.flaw == "bug":
                m["bugs_merged"] += 1
            if cfg["audits"]:
                if author.probation > 0 and not cfg["mine_probation_reviewers"]:
                    author.probation -= 1
                    queue_audit(pr)
                elif cfg["mine_audit"] is not None:
                    if rng.random() < cfg["mine_audit"][pr.tier]:
                        queue_audit(pr)
                elif rng.random() < cfg["audit_rate"]:
                    queue_audit(pr)

    def finish_audit(agent, pr, now):
        agent.credits += cfg["mine_audit_credits"]
        agent.trust += 0.5
        if pr.found_at is None and pr.flaw == "backdoor" and detects(agent, pr, careful=True):
            found(pr, now, "audit")

    for now in range(hours):
        if cfg["mine_relative_levels"] and now % 24 == 0:
            ts = sorted(a.trust for a in agents if not a.gone and not a.banned)
            q2, q3 = cfg["mine_relative_levels"]
            l2 = max(LEVELS[1] + 1, ts[int(len(ts) * q2)])
            CUR_LEVELS[2] = min(LEVELS[2], l2)
            CUR_LEVELS[3] = min(LEVELS[3], max(CUR_LEVELS[2] + 1, ts[int(len(ts) * q3)]))
        while vesting and vesting[0][0] <= now:
            _, pr = vesting.popleft()
            author = agents[pr.author]
            if pr.found_at is None and not author.banned:
                author.trust += 1
        for a in agents:
            if a.gone:
                continue
            if rng.random() < 1 / (cfg["churn_days"] * 24):
                a.gone = True
                if a.job:
                    m["abandoned"] += 1
                    requeue(a.job, slots, a, cfg, orphans)
                    a.job = None
                agents.append(Agent(len(agents), cfg, rng, ring_of))
                continue
            a.online = rng.random() >= cfg["p_offline"] if a.online else rng.random() < cfg["p_online"]
            if a.job and now > a.job[2]:
                m["abandoned"] += 1
                requeue(a.job, slots, a, cfg, orphans)
                a.job = None
            if a.job and cfg["mine_hedge"] and not a.job[4] and a.job[0][0] in ("review", "review_sr", "audit")                     and not a.job[0][1].canary and now - a.job[5] >= cfg["mine_hedge"] and (not a.online or a.banned):
                token = [False]
                a.job[4] = True
                a.job[0] = a.job[0][:2] + (token,)
                slots.append((a.job[0][1], a.job[0][0], token))
            if a.job and len(a.job[0]) > 2 and a.job[0][2][0]:
                a.job = None
            if not a.online or a.banned or not a.job:
                continue
            a.job[5] = now
            if cfg.get("debug"):
                hrs[a.job[0][0]] = hrs.get(a.job[0][0], 0) + 1
            if cfg["heartbeat"]:
                if cfg["mine_lease_mult"]:
                    a.job[2] = now + a.job[3]
                else:
                    a.job[2] = now + (cfg["lease_impl"] if a.job[0][0] == "impl" else cfg["lease_review"])
            a.job[1] -= 1
            if a.job[1] <= 0:
                work, a.job = a.job[0], None
                if len(work) > 2:
                    work[2][0] = True
                if work[0] == "impl":
                    new_pr(a.id, now)
                elif work[0] in ("review", "review_sr"):
                    finish_review(a, work[1], now)
                else:
                    finish_audit(a, work[1], now)

        if cfg["canary_every"] and m["prs"]:
            while m["canaries"] * cfg["canary_every"] < m["prs"]:
                canary(now)

        idle = [a for a in agents if a.online and not a.gone and not a.banned and a.job is None]
        rng.shuffle(idle)
        by_level = [[a for a in idle if a.level(tiers) >= lv] for lv in range(4)]
        taken = set()
        for _ in range(len(slots)):
            item = slots.popleft()
            pr, kind = item[0], item[1]
            if pr.rejected and kind != "audit" or (kind == "audit" and pr.found_at is not None):
                continue
            if kind == "audit":
                need = cfg["audit_level"]
            elif pr.canary:
                need = 0
            else:
                need = max(TIER_LEVEL[pr.tier], cfg["mine_senior_level"] if kind == "review_sr" else 0)
            pool = by_level[need]
            pick, skipped = None, []
            while pool:
                cand = pool.pop()
                if cand.id in taken:
                    continue
                if cand.id != pr.author and cand.id not in pr.reviewers and \
                        (kind == "audit" or pr.canary or cand.review_ban < now):
                    pick = cand
                    break
                skipped.append(cand)
            pool.extend(skipped)
            if pick is None:
                slots.append(item)
                continue
            taken.add(pick.id)
            pr.reviewers.append(pick.id)
            give(pick, (kind, pr) + item[2:], now)
        for a in idle:
            if a.id not in taken and a.job is None and a.credits - cfg["pr_cost"] >= -cfg["debt"]:
                a.credits -= cfg["pr_cost"]
                d = orphans.popleft() if orphans else rng.uniform(*cfg["impl_hours"])
                lease = lease_for("impl", d)
                a.job = [("impl",), d, now + lease, lease, False, now]

    if cfg.get("debug"):
        from collections import Counter
        idle_h = 0
        print("hours", {k: v // 1000 for k, v in hrs.items()}, "prs", m["prs"], "merged", m["merged"], "rejected", sum(p.rejected for p in prs))
        print("slots", Counter((k, p.tier, p.canary) for p, k, *_ in slots).most_common(8))
        lv = Counter(a.level(tiers) for a in agents if not a.gone and not a.banned)
        print("levels", sorted(lv.items()), "audits done", sum(1 for p in prs for r in p.reviewers) )
        bad = [a for a in agents if a.kind != "honest"]
        print("bad trust>=5", sum(a.trust >= 5 for a in bad), "of", len(bad), "banned", sum(a.banned for a in bad))
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


def requeue(job, slots, agent=None, cfg=None, orphans=None):
    work = job[0]
    if work[0] == "impl" and agent is not None and cfg["refund"]:
        agent.credits += cfg["pr_cost"]
    if work[0] == "impl" and cfg is not None and cfg["mine_resume"] and orphans is not None and job[1] > 0:
        orphans.append(job[1] + cfg["mine_handoff"])
    if work[0] in ("review", "review_sr", "audit") and not job[4]:
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
