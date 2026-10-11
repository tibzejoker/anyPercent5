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
    # rules added for scenario "mine" (defaults keep "proposal" unchanged)
    tier_reviewers=None, audit_tiers=None, audit_front=False, canary_gate=False,
    probation_audits=0, approver_taint=False, audit_credit=1, recover_impl=False, handover_hours=1, pick_by_trust=False, canary_taint=0, spare_reviewers=0,
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
    "mine": dict(
        tier_reviewers={"green": 1, "amber": 3, "red": 2},
        audit_tiers={"green": 0.5, "amber": 0.5, "red": 1.0},
        audit_level=1, audit_front=True, audit_credit=2, pr_cost=1,
        canary_gate=True, canary_every=20, approver_taint=True, recover_impl=True,
    ),
    "synth": dict(
        tier_reviewers={"green": 1, "amber": 3, "red": 2},
        audit_tiers={"green": 0.0, "amber": 0.0, "red": 1.0}, audit_level=0, audit_front=True, audit_credit=2,
        pr_cost=1, debt=10**9, canary_every=2.5, approver_taint=True, recover_impl=True,
        syn=dict(cal_vis=1.0, rank=True, canary_to_least="confirm", confirm_max=5, late_spare=True,
                 post_review={"green": 0.6, "amber": 1.0, "red": 0.0}),
    ),
    "synth-fast": dict(
        tier_reviewers={"green": 1, "amber": 3, "red": 2},
        audit_tiers={"green": 0.0, "amber": 0.0, "red": 1.0}, audit_level=0, audit_front=True, audit_credit=2,
        pr_cost=1, debt=10**9, canary_every=2.5, approver_taint=True, recover_impl=True,
        syn=dict(cal_vis=1.0, rank=True, canary_to_least="confirm", confirm_max=5),
    ),
    "mine-robust": dict(
        tier_reviewers={"green": 2, "amber": 3, "red": 2}, spare_reviewers=1,
        audit_tiers={"green": 0.35, "amber": 0.35, "red": 1.0},
        audit_level=1, audit_front=True, audit_credit=2, pr_cost=1,
        canary_gate=True, canary_every=20, approver_taint=True, recover_impl=True,
    ),
}


SYN = dict(                    # scenario "synth" knobs (None/0/False = behave like "mine")
    late_spare=False,          # spare reviewer keeps going after merge: its review is a post-merge check
    cal_vis=None,              # canaries scored by detects() at this visibility (calibration of diligence x skill)
    cal_prior=(1, 1),          # beta prior (hits, misses) of the calibration score
    probation=0,               # canaries a newcomer reviews before any other check
    juror_min=0.0,             # minimum calibration score to review/audit real changes
    rank=False,                # draw the best calibrated idle people first for reviews/audits
    rank_audits_only=False,
    canary_to_least=False,     # True: least-calibrated first; "confirm": people with a perfect record first
    confirm_max=3,
    review_evidence=0.0,
    audit_by_review=0.0,
    post_review=None,
    flat_reviews=False,
    approver_recal=False,      # an approver of a caught backdoor loses their calibration record        # reviewers of any tier drawn from the whole calibrated pool (no trust level needed)          # {tier: P(one extra post-merge review during the soak)} (needs late_spare)       # audit rate x (1 + k * (1 - 2 * calibration score of the weakest approver))       # weight of real review outcomes in the calibration score (flaws found vs expected)     # canaries go to the least-calibrated idle people
    soak_h=0,                  # random audits of changes merged longer ago than this are dropped
    young_audit=None,          # (account age hours, extra audit prob) for changes of young accounts
)


class Agent:
    __slots__ = ("id", "kind", "ring", "skill", "diligence", "trust", "credits", "online", "gone", "banned",
                 "job", "merged", "approved", "attacking", "canary_bad", "l1_merges",
                 "cal_n", "cal_hit", "born")

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
        self.canary_bad = 0
        self.l1_merges = 0
        self.cal_n = self.cal_hit = 0
        self.born = 0

    def level(self, tiers=True):
        if not tiers:
            return 3
        return sum(self.trust >= t for t in LEVELS) - 1


class PR:
    __slots__ = ("id", "author", "tier", "flaw", "vis", "canary", "submitted", "reviewers", "pending", "done",
                 "approvals", "merged_at", "found_at", "rejected", "checks")

    def __init__(self, i, author, tier, flaw, vis, now, canary=False):
        self.id, self.author, self.tier, self.flaw, self.vis, self.canary = i, author, tier, flaw, vis, canary
        self.submitted = now
        self.reviewers, self.pending, self.done, self.approvals = [], 0, 0, []
        self.merged_at = self.found_at = None
        self.rejected = False
        self.checks = 0


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
    syn = dict(SYN, **cfg["syn"]) if cfg.get("syn") is not None else None
    if syn:
        for a in agents:
            if a.trust >= 60:
                a.cal_n = a.cal_hit = syn["probation"]
        prior = syn["cal_prior"]

        def score(a):
            if syn.get("oracle"):
                return a.skill * a.diligence
            return (a.cal_hit + prior[0]) / (a.cal_n + prior[0] + prior[1])
    dbg = {"h_impl": 0, "h_review": 0, "h_audit": 0, "h_idle": 0, "audits": 0, "audit_found": 0, "late_found": 0}
    prs = []
    slots = deque()           # (pr, kind) waiting for a reviewer/auditor
    orphans = deque()         # remaining hours of abandoned implementation work (recover_impl)
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
        need = (cfg["tier_reviewers"] or TIER_REVIEWERS)[tier]
        pr.pending = need
        if not cfg["random_reviewers"] and a.kind == "colluder":
            mates = [b for b in agents if b.ring == a.ring and b.id != author and not b.banned and not b.gone
                     and b.online and b.job is None and b.level(tiers) >= TIER_LEVEL[tier]]
            for b in mates[:need]:
                give(b, ("review", pr), now)
                pr.reviewers.append(b.id)
            need -= len(mates[:need])
        for _ in range(need + (cfg["spare_reviewers"] if need else 0)):
            slots.append((pr, "review"))

    def canary(now, queue=True):
        vis = syn["cal_vis"] if syn and syn["cal_vis"] else VISIBILITY["green"] * 1.4
        pr = PR(len(prs), -1, "green", "backdoor", vis, now, canary=True)
        prs.append(pr)
        pr.pending = 1
        m["canaries"] += 1
        if queue:
            slots.append((pr, "review"))
        return pr

    def give(agent, work, now):
        lo, hi = cfg["audit_hours"] if work[0] in ("audit", "sweep") else cfg["review_hours"]
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
                    slots.appendleft((pr, "sweep" if syn else "audit"))

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
                if syn and syn["approver_recal"]:
                    r.cal_n, r.cal_hit = r.cal_n + 1, 0
                if cfg["approver_taint"] and cfg["audits"]:
                    for other in r.approved:
                        if other.found_at is None and other is not pr:
                            slots.appendleft((other, "sweep" if syn else "audit"))

    def finish_review(agent, pr, now):
        agent.credits += 1
        if pr.canary and syn and syn["cal_vis"]:
            agent.cal_n += 1
            if detects(agent, pr):
                agent.cal_hit += 1
                m["canary_caught"] += 1
                agent.trust += 1
            else:
                agent.trust -= 4
            return
        if pr.canary:
            if rng.random() < agent.diligence and rng.random() < 0.9:
                agent.trust += 1
                m["canary_caught"] += 1
                agent.canary_bad = max(0, agent.canary_bad - 1)
            else:
                agent.trust -= 4
                agent.canary_bad += 1
                if cfg["canary_taint"] and cfg["audits"]:
                    for other in agent.approved[-cfg["canary_taint"]:]:
                        if other.found_at is None:
                            slots.appendleft((other, "audit"))
            return
        agent.trust += 0.2
        if syn and syn["late_spare"] and pr.merged_at is not None and pr.found_at is None and not pr.rejected:
            if detects(agent, pr) and pr.flaw == "backdoor":
                dbg["late_found"] += 1
                found(pr, now, "audit")
            return
        if pr.rejected or pr.found_at is not None or pr.merged_at is not None:
            return
        dbg["rq"] = dbg.get("rq", 0) + agent.skill * agent.diligence
        dbg["rn"] = dbg.get("rn", 0) + 1
        dbg["rlazy"] = dbg.get("rlazy", 0) + (agent.diligence < 0.5)
        if syn and syn["review_evidence"]:
            agent.cal_n += syn["review_evidence"] * cfg["honest_bug"] * VISIBILITY[pr.tier]
        if detects(agent, pr):
            if syn and syn["review_evidence"]:
                agent.cal_hit += syn["review_evidence"]
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
            if cfg["audit_tiers"] is None:
                if cfg["audits"] and rng.random() < cfg["audit_rate"]:
                    slots.append((pr, "audit"))
            else:
                probation = False
                if cfg["probation_audits"] and author.level(tiers) >= 1:
                    author.l1_merges += 1
                    probation = author.l1_merges <= cfg["probation_audits"]
                rate = cfg["audit_tiers"][pr.tier]
                if syn and syn["audit_by_review"] and pr.tier != "red":
                    weak = 1 - min(score(agents[rid]) for rid in pr.approvals)
                    rate = min(1.0, rate * (1 + syn["audit_by_review"] * (weak - 0.5) * 2))
                if syn and syn["post_review"]:
                    prate = syn["post_review"][pr.tier]
                    if syn["audit_by_review"]:
                        weak = 1 - min(score(agents[rid]) for rid in pr.approvals)
                        prate = min(1.0, prate * (1 + syn["audit_by_review"] * (weak - 0.5) * 2))
                    if rng.random() < prate:
                        slots.appendleft((pr, "review"))
                if syn and syn["young_audit"] and now - author.born < syn["young_audit"][0]:
                    rate = min(1.0, rate + syn["young_audit"][1])
                if cfg["audits"] and (probation or rng.random() < rate):
                    (slots.appendleft if cfg["audit_front"] else slots.append)((pr, "audit"))

    def finish_audit(agent, pr, now):
        agent.credits += cfg["audit_credit"]
        agent.trust += 0.5
        dbg["audits"] += 1
        dbg["aq"] = dbg.get("aq", 0) + agent.skill
        pr.checks += 1
        if pr.found_at is None and pr.flaw == "backdoor" and detects(agent, pr, careful=True):
            dbg["audit_found"] += 1
            found(pr, now, "audit")

    for now in range(hours):
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
                agents[-1].born = now
                continue
            a.online = rng.random() >= cfg["p_offline"] if a.online else rng.random() < cfg["p_online"]
            if a.job and now > a.job[2]:
                m["abandoned"] += 1
                requeue(a.job, slots, a, cfg, orphans)
                a.job = None
            if cfg["spare_reviewers"] and a.job and a.job[0][0] == "review" and not (
                    syn and syn["late_spare"] and not a.job[0][1].rejected and a.job[0][1].found_at is None):
                pr = a.job[0][1]
                if pr.merged_at is not None or pr.rejected:
                    a.job = None
                    m["cancelled"] = m.get("cancelled", 0) + 1
            if not a.online or a.banned or not a.job:
                if a.online and not a.banned and cfg.get("debug"):
                    dbg["h_idle"] += 1
                continue
            if cfg.get("debug"):
                dbg["h_" + {"impl": "impl", "review": "review"}.get(a.job[0][0], "audit")] += 1
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

        if cfg["canary_every"] and m["prs"]:
            while m["canaries"] * cfg["canary_every"] < m["prs"]:
                canary(now)

        idle = [a for a in agents if a.online and not a.gone and not a.banned and a.job is None]
        rng.shuffle(idle)
        if cfg["pick_by_trust"]:
            idle.sort(key=lambda a: a.trust)
        taken = set()
        if syn:
            if syn["probation"]:
                for a in idle:
                    if a.cal_n < syn["probation"]:
                        taken.add(a.id)
                        give(a, ("review", canary(now, queue=False)), now)
            jur = [a for a in idle if a.id not in taken and a.cal_n >= syn["probation"] and score(a) >= syn["juror_min"]]
            if syn["rank_audits_only"]:
                by_level_rev = [[a for a in jur if a.level(tiers) >= lv] for lv in range(4)]
            if syn["rank"] or syn["rank_audits_only"]:
                jur.sort(key=score)
            by_level = [[a for a in jur if a.level(tiers) >= lv] for lv in range(4)]
            if syn["canary_to_least"] == "confirm":
                canary_pool = sorted((a for a in idle if a.id not in taken),
                                     key=lambda a: (2 if 0 < a.cal_n == a.cal_hit < syn["confirm_max"] else
                                                    1 if a.cal_n == 0 else 0, -a.cal_n))
            elif syn["canary_to_least"]:
                canary_pool = sorted((a for a in idle if a.id not in taken), key=lambda a: -a.cal_n)
            else:
                canary_pool = None
        else:
            by_level = [[a for a in idle if a.level(tiers) >= lv] for lv in range(4)]
        for _ in range(len(slots)):
            pr, kind = slots.popleft()
            if (pr.rejected or pr.merged_at is not None) and kind == "review" and not (
                    syn and syn["late_spare"] and not pr.rejected and pr.found_at is None
                    and not pr.canary) or (kind in ("audit", "sweep") and pr.found_at is not None):
                continue
            if syn and kind == "audit" and syn["soak_h"] and now - pr.merged_at > syn["soak_h"]:
                continue
            need = cfg["audit_level"] if kind != "review" else (0 if pr.canary or syn and syn["flat_reviews"] else TIER_LEVEL[pr.tier])
            pool = by_level[need]
            if syn and pr.canary and canary_pool is not None:
                pool = canary_pool
            elif syn and syn["rank_audits_only"] and kind == "review":
                pool = by_level_rev[need]
            pick, skipped = None, []
            while pool:
                cand = pool.pop()
                if cand.id in taken:
                    continue
                if cand.id != pr.author and cand.id not in pr.reviewers and not (
                        cfg["canary_gate"] and not pr.canary and cand.canary_bad > 0):
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
                length = orphans.popleft() if orphans else rng.uniform(*cfg["impl_hours"])
                a.job = [("impl",), length, now + cfg["lease_impl"]]

    live = sum(1 for p in prs if p.flaw == "backdoor" and not p.canary and p.merged_at is not None and p.found_at is None)
    tail = [p for p in prs if p.merged_at is not None and p.merged_at >= hours - 7 * 24 and not p.canary]
    extra = {}
    if cfg.get("debug"):
        extra = dict(dbg, merges=m["merged"], prs=m["prs"], merged_bd=m["merged_backdoors"], caught_review=m["caught_review"],
                     tiers={t: sum(1 for p in prs if p.merged_at is not None and p.tier == t and not p.canary) for t in TIER_LEVEL},
                     bd_tiers={t: sum(1 for p in prs if p.merged_at is not None and p.tier == t and p.flaw == "backdoor"
                                      and not p.canary) for t in TIER_LEVEL},
                     ship14={f"{t}/{c}": sum(1 for p in prs if p.flaw == "backdoor" and not p.canary and p.tier == t
                                             and p.merged_at is not None and p.merged_at <= hours - 336 and min(p.checks, 2) == c
                                             and (p.found_at is None or p.found_at - p.merged_at > 336))
                             for t in TIER_LEVEL for c in range(3)},
                     ship14_banned_later=sum(1 for p in prs if p.flaw == "backdoor" and not p.canary and p.merged_at is not None
                                             and p.merged_at <= hours - 336 and p.found_at is not None and p.found_at - p.merged_at > 336),
                     levels=[sum(1 for a in agents if not a.gone and not a.banned and a.level(tiers) == lv) for lv in range(4)])
    return {
        **extra,
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
    if work[0] == "impl" and cfg["recover_impl"]:
        orphans.append(job[1] + cfg["handover_hours"])
    if work[0] == "impl" and agent is not None and cfg["refund"]:
        agent.credits += cfg["pr_cost"]
    if work[0] in ("review", "audit", "sweep"):
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
