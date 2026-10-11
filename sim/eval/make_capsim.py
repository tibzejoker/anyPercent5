"""Build capsim.py: lease_sim.py plus "away" declarations, member capabilities, artifact capture and task supply.

- away: a holder going offline for longer than their lease declares an absence that freezes the lease (and the
  cap clock), within limits: max length per declaration, budget per 30 days, and a rule for tasks others need
  (no away on hubs, or a 48 h limit on tasks with >= 2 dependents, after which another member may take over).
  Abusers (zombies, fake pushers) declare the whole budget.
- capabilities: members have a level (2 = full set, e.g. Windows + the game + GPU; 1 = Windows only; 0 = none)
  and a claimed level (liars claim 2). Tasks require a level. Members claim only tasks at or under their claimed
  level, highest requirement first. Reviews optionally restricted to members whose claimed level covers the task.
- artifact capture: a full-set task becomes capture (full set, 1-2 h) -> main work (no requirement) -> verify
  (full set, 0.5-1.5 h).
- task supply: auto (instant), maintainers (10 maintainers write 4 tasks per online hour), propose (anyone
  proposes, a task is ready after a 1-4 h review).
python make_capsim.py -> capsim.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
src = (ROOT / "lease_sim.py").read_text(encoding="utf-8")


def sub(old, new, count=1):
    global src
    assert src.count(old) == count, (old[:70], src.count(old))
    src = src.replace(old, new)


# ---------------------------------------------------------------- members
sub('''                 "cal_n", "cal_hit", "born")''', '''                 "cal_n", "cal_hit", "born", "cap", "ccap", "back_at")''')
sub('''        self.cal_n = self.cal_hit = 0
        self.born = 0
''', '''        self.cal_n = self.cal_hit = 0
        self.born = 0
        shares = cfg.get("caps") or (1.0, 0.0)
        r = rng.random()
        self.cap = 2 if r < shares[0] else 1 if r < shares[0] + shares[1] else 0
        self.ccap = self.cap
        lie = rng.random()
        if self.kind != "honest" and cfg.get("attackers_lie"):
            self.ccap = 2
        elif self.cap < 2 and lie < cfg.get("liars", 0.0):
            self.ccap = 2
        self.back_at = None
''')
sub('''    tiers = cfg.get("tiers", True)
''', '''    for a in agents[:cfg.get("n_maint", 0)]:
        a.kind, a.ring, a.diligence, a.cap, a.ccap = "honest", None, 0.9, 2, 2
    tiers = cfg.get("tiers", True)
''')
sub('''            if rng.random() < 1 / (cfg["churn_days"] * 24):''',
    '''            if a.id >= cfg.get("n_maint", 0) and rng.random() < 1 / (cfg["churn_days"] * 24):''')

# ---------------------------------------------------------------- online model + away
sub('''            if cfg.get("weekend") and (a.id * 2654435761) % 1000 < cfg["weekend"] * 1000:
                wk = (now // 24) % 7 >= 5
                p_on, p_off = (cfg["wk_on"], cfg["wk_off"]) if wk else (cfg["wd_on"], 0.5)
                a.online = rng.random() >= p_off if a.online else rng.random() < p_on
            else:
                a.online = rng.random() >= cfg["p_offline"] if a.online else rng.random() < cfg["p_online"]''',
    '''            if a.back_at is not None and now < a.back_at:
                a.online = False
            elif a.back_at is not None:
                a.online, a.back_at = True, None
            else:
                was = a.online
                a.online = rng.random() >= p_off_of(a, now) if a.online else rng.random() < p_on_of(a, now)
                if was and not a.online and dag and dag.c["away"] and a.job and a.job[0][0] == "impl":
                    gap = 1
                    while gap < 24 * 30 and rng.random() >= p_on_of(a, now + gap):
                        gap += 1
                    a.back_at = now + gap
                    dag.away(a, a.job, now, gap)''')
sub('''    def new_pr(author, now, task=None):''', '''    def weekender(a):
        return cfg.get("weekend") and (a.id * 2654435761) % 1000 < cfg["weekend"] * 1000

    def p_on_of(a, t):
        if weekender(a):
            return cfg["wk_on"] if (t // 24) % 7 >= 5 else cfg["wd_on"]
        return cfg["p_online"]

    def p_off_of(a, t):
        if weekender(a):
            return cfg["wk_off"] if (t // 24) % 7 >= 5 else 0.5
        return cfg["p_offline"]

    def new_pr(author, now, task=None):''')

# ---------------------------------------------------------------- liars get stuck, phases complete without a PR
sub('''            if dag and a.job[0][0] == "impl":
                dag.push(a.job, now)''', '''            if dag and a.job[0][0] == "impl" and len(a.job) > 6 and a.job[6]:
                continue
            if dag and a.job[0][0] == "impl":
                dag.push(a.job, now)''')
sub('''                if dag and a.job[0][0] == "impl":
                    dag.expire(a.job, now)
                else:
                    requeue(a.job, slots, a, cfg, orphans)
                a.job = None
            if cfg["spare_reviewers"]''', '''                if dag and a.job[0][0] == "impl":
                    dag.expire(a.job, now)
                    if len(a.job) > 6 and a.job[6] and rng.random() < 0.5:
                        a.ccap = a.cap
                else:
                    requeue(a.job, slots, a, cfg, orphans)
                a.job = None
            if cfg["spare_reviewers"]''')
sub('''                if work[0] == "impl":
                    new_pr(a.id, now, work[1] if len(work) > 1 else None)''',
    '''                if work[0] == "impl" and dag and len(work) > 1 and work[1].phase in ("capture", "verify"):
                    dag.phase_done(work[1], now)
                elif work[0] == "impl":
                    new_pr(a.id, now, work[1] if len(work) > 1 else None)''')

# ---------------------------------------------------------------- reviews restricted to the task's capability
sub('''                if cand.id != pr.author and cand.id not in pr.reviewers and not (''',
    '''                if cand.id != pr.author and cand.id not in pr.reviewers and (
                        not cfg.get("review_caps") or pr.canary or pr.task is None or cand.ccap >= pr.task.req) and not (''')
sub('''    dag = DAG(cfg, rng, m) if cfg.get("dag") else None
''', '''    dag = DAG(cfg, rng, m) if cfg.get("dag") else None
    if dag:
        dag.maint_online = lambda: sum(1 for a in agents[:cfg.get("n_maint", 0)] if a.online and not a.gone)
''')
sub('''            ship14=globals()['_SHIP'][14],''', '''            ship14=globals()['_SHIP'][14],
            ring_reviews=dbg.get("ring", 0),''')
sub('''        dbg["rlazy"] = dbg.get("rlazy", 0) + (agent.diligence < 0.5)''', '''        dbg["rlazy"] = dbg.get("rlazy", 0) + (agent.diligence < 0.5)
        if pr.flaw == "backdoor" and agent.kind != "honest":
            dbg["ring"] = dbg.get("ring", 0) + 1''')

# ---------------------------------------------------------------- DAG
sub('''    work_ratio=5.0,     # wall-clock hours per worked hour (online about 17-20 % of the time)
)''', '''    work_ratio=5.0,     # wall-clock hours per worked hour (online about 17-20 % of the time)
    away=False, away_max=5, away_month=14, away_block="none", away_declare=0.9, away_abuse=True,
    away_need_push=False, takeover_deps=2, away_pause_cap=True, away_shift_progress=False,
    req_mix=None,       # (full set, windows, none) shares of task requirements; None = no requirements
    conv=0.0,           # share of full-set tasks turned into capture -> main -> verify
    conv_overhead=1.2,  # main work without the game takes this much longer
    supply_mode="auto", maint_rate=4.0,
)''')
sub('''    __slots__ = ("id", "epic", "left", "deps", "hub", "state", "hours", "created", "ready_at", "done_at", "claims",
                 "big", "spent")''', '''    __slots__ = ("id", "epic", "left", "deps", "hub", "state", "hours", "created", "ready_at", "done_at", "claims",
                 "big", "spent", "req", "conv", "phase", "main_hours", "rs", "prop")''')
sub('''        self.big = False
        self.spent = 0.0
''', '''        self.big = False
        self.spent = 0.0
        self.req, self.conv, self.phase, self.main_hours, self.rs, self.prop = 0, False, "main", 0.0, now, False
''')
sub('''        self.ready, self.ready_hub = [], []      # heaps: oldest epic first (finish what is started)''',
    '''        self.q = {}                # (hub first, requirement) -> heap, oldest epic first
        self.tries = self.nothing = self.aways = self.away_blocked = self.liar_fail = self.pending = 0
        self.away_h, self.tokens = 0.0, 0.0
        self.away_used = {}
        self.waits = {0: [], 1: [], 2: []}
        self.full_ready = []''')
sub('''            t.big = big
''', '''            t.big = big
            if c["req_mix"]:
                t.req = rng.choices((2, 1, 0), c["req_mix"])[0]
                if t.req == 2 and rng.random() < c["conv"]:
                    t.conv, t.phase, t.main_hours = True, "capture", t.hours * c["conv_overhead"]
                    t.hours = rng.uniform(1, 2)
''')
sub('''            self.tasks.append(t)
            if t.left == 0:
                self.make_ready(t, now)''', '''            self.tasks.append(t)
            if t.left == 0 and c["supply_mode"] == "propose":
                t.prop, self.pending = True, self.pending + 1
                self.hold(t, now + rng.uniform(1, 4), t.hours)
            elif t.left == 0:
                self.make_ready(t, now)''')
sub('''        heapq.heappush(self.ready_hub if t.hub and self.c["hub_first"] else self.ready, (t.epic, t.id, t))''',
    '''        t.rs = now
        key = (bool(t.hub and self.c["hub_first"]), t.req)
        heapq.heappush(self.q.setdefault(key, []), (t.epic, t.id, t))''')
sub('''            _, _, t, left = heapq.heappop(self.phantom)
            t.hours = left
            self.make_ready(t, now)
        online = self.m["online"][-1] if self.m["online"] else 10
        while len(self.ready) + len(self.ready_hub) < max(2, self.c["supply"] * online):
            self.spawn(now)''', '''            _, _, t, left = heapq.heappop(self.phantom)
            t.hours = left
            if t.prop:
                t.prop, self.pending = False, self.pending - 1
            self.make_ready(t, now)
        online = self.m["online"][-1] if self.m["online"] else 10
        if self.c["supply_mode"] == "maintainers":
            self.tokens = min(self.tokens + self.c["maint_rate"] * self.maint_online(), 400)
        self.full_ready.append(sum(len(q) for (h, r), q in self.q.items() if r == 2))
        while sum(len(q) for q in self.q.values()) + self.pending < max(2, self.c["supply"] * online):
            if self.c["supply_mode"] == "maintainers":
                if self.tokens < self.c["epic"]:
                    break
                self.tokens -= self.c["epic"]
            self.spawn(now)''')
sub('''        t = None
        for q in (self.ready_hub, self.ready):
            while q and t is None:
                cand = heapq.heappop(q)[2]
                if cand.state == "ready":
                    t = cand
            if t:
                break
        if t is None:
            return None''', '''        t = None
        for hub in ((True, False) if self.c["hub_first"] else (False,)):
            for req in range(agent.ccap, -1, -1):
                q = self.q.get((hub, req))
                while q and t is None:
                    cand = heapq.heappop(q)[2]
                    if cand.state == "ready":
                        t = cand
                if t:
                    break
            if t:
                break
        self.tries += 1
        if t is None:
            self.nothing += 1
            return None
        self.waits[t.req].append(now - t.rs)''')
sub('''        if r < c["ghost"] + c["zombie"]:
            self.zombies += 1
            until = now + F''', '''        if r < c["ghost"] + c["zombie"]:
            self.zombies += 1
            until = now + F + self.abuse(t)''')
sub('''                until = now + windows * cap
            else:''', '''                until = now + windows * cap + self.abuse(t, fake=True)
            else:''')
sub('''        self.honest_claims += 1
        return [("impl", t), t.hours, now + F, now, None, 0.0]''', '''        self.honest_claims += 1
        return [("impl", t), t.hours, now + F, now, None, 0.0, agent.kind == "honest" and agent.cap < t.req]

    def abuse(self, t, fake=False):
        """Extra hold an abuser gets by declaring the whole away budget."""
        c = self.c
        if not (c["away"] and c["away_abuse"]) or c["away_need_push"] and not fake:
            return 0.0
        if fake and not c["away_pause_cap"]:
            return 0.0
        if c["away_block"] == "hubs" and t.hub:
            return 0.0
        if c["away_block"] == "takeover" and len(t.deps) >= c["takeover_deps"]:
            return 48.0
        return c["away_month"] * 24.0

    def away(self, agent, job, now, gap):
        c, t = self.c, job[0][1]
        if gap <= job[2] - now or self.rng.random() > c["away_declare"]:
            return
        if c["away_need_push"] and not job[5]:
            self.away_blocked += 1
            return
        if c["away_block"] == "hubs" and t.hub:
            self.away_blocked += 1
            return
        allowed = c["away_max"] * 24.0
        if c["away_block"] == "takeover" and len(t.deps) >= c["takeover_deps"]:
            allowed = min(allowed, 48.0)
        used = [(at, h) for at, h in self.away_used.get(agent.id, []) if at > now - 720]
        allowed = min(allowed, c["away_month"] * 24.0 - sum(h for _, h in used))
        if allowed <= 0:
            self.away_blocked += 1
            return
        frozen = min(gap, allowed)
        job[2] += frozen
        if c["away_pause_cap"]:
            job[3] += frozen
        if c["away_shift_progress"] and job[4] is not None:
            job[4] += frozen
        self.away_used[agent.id] = used + [(now, frozen)]
        self.aways += 1
        self.away_h += frozen

    def phase_done(self, t, now):
        if t.phase == "capture":
            t.phase, t.req, t.hours = "main", 0, t.main_hours
            self.make_ready(t, now)
        else:
            t.phase = "done"
            self.done(t, now)''')
sub('''        """The holder's lease ran out (or hit the cap) while they still exist: the task goes back to the pool."""
        self.lost += 1''', '''        """The holder's lease ran out (or hit the cap) while they still exist: the task goes back to the pool."""
        if len(job) > 6 and job[6]:
            self.liar_fail += 1
        self.lost += 1''')
sub('''    def done(self, t, now):
''', '''    def done(self, t, now):
        if t.conv and t.phase == "main":
            t.phase, t.req, t.hours = "verify", 2, self.rng.uniform(0.5, 1.5)
            t.state = "ready"
            self.make_ready(t, now)
            return
''')
sub('''                    big_n=len(bigs), big_open=sum(t.done_at is None for t in bigs),''',
    '''                    big_n=len(bigs), big_open=sum(t.done_at is None for t in bigs),
                    nothing=round(100 * self.nothing / max(1, self.tries), 1), aways=self.aways,
                    away_h=round(self.away_h), away_blocked=self.away_blocked, liar_fail=self.liar_fail,
                    wait_full_med=pct(self.waits[2], 0.5), wait_full_p95=pct(self.waits[2], 0.95),
                    wait_none_med=pct(self.waits[0], 0.5), wait_none_p95=pct(self.waits[0], 0.95),
                    full_ready=round(sum(self.full_ready) / max(1, len(self.full_ready)), 1),''')

(ROOT / "capsim.py").write_text(src, encoding="utf-8")
print("capsim.py written")
