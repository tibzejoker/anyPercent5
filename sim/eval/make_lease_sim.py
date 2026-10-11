"""Build lease_sim.py: anyp5_sim.py with the lease model of anyp5/engine.py v0.2 made explicit.

Lease of an implementation task: first push due F hours after the claim, each pushed hour of work renews it to
now + R, never past claim + C (cap). At the cap the task is reassigned with its branch, unless the "extension"
variant grants another window to a holder whose recent progress a reviewer confirms. Handing a task over costs
the next holder a ramp-up (fixed hours + a share of the work already done on it).

Holder behaviours: honest (works when online, pushes each worked hour), silent (stops, maybe part way), zombie
(heartbeats only, never pushes), fake pusher (pushes token commits forever, never finishes).
Worlds add weekend-only contributors and a long tail of big tasks.  python make_lease_sim.py -> lease_sim.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
src = (ROOT / "anyp5_sim.py").read_text(encoding="utf-8")


def sub(old, new, count=1):
    global src
    assert src.count(old) == count, (old[:60], src.count(old))
    src = src.replace(old, new)


# --- online model: optional weekend-only contributors
sub('''            a.online = rng.random() >= cfg["p_offline"] if a.online else rng.random() < cfg["p_online"]''',
    '''            if cfg.get("weekend") and (a.id * 2654435761) % 1000 < cfg["weekend"] * 1000:
                wk = (now // 24) % 7 >= 5
                p_on, p_off = (cfg["wk_on"], cfg["wk_off"]) if wk else (cfg["wd_on"], 0.5)
                a.online = rng.random() >= p_off if a.online else rng.random() < p_on
            else:
                a.online = rng.random() >= cfg["p_offline"] if a.online else rng.random() < cfg["p_online"]''')

# --- pushed work renews implementation leases (heartbeats still renew review leases)
sub('''            if cfg["heartbeat"]:
                a.job[2] = now + (cfg["lease_impl"] if a.job[0][0] == "impl" else cfg["lease_review"])''',
    '''            if dag and a.job[0][0] == "impl":
                dag.push(a.job, now)
            elif cfg["heartbeat"]:
                a.job[2] = now + (cfg["lease_impl"] if a.job[0][0] == "impl" else cfg["lease_review"])''')

sub('''    first_push=None,    # hours: a claim with no pushed commit by then expires (silent claims never start)
)''', '''    first_push=None,    # hours: a claim with no pushed commit by then expires (silent claims never start)
    renew_h=72,         # hours: each pushed hour of work renews the lease to now + renew_h
    extension=False,    # at the cap, a holder with progress in the last 48 h confirmed by a reviewer keeps the task
    ext_detect=0.8,     # P(a reviewer sees that a fake pusher's "progress" is not real)
    fake=0.0,           # P(a claim is a fake pusher: token commits forever, never finishes)
    handover_fixed=2.0, # hours of ramp-up for the next holder
    handover_frac=0.2,  # plus this share of the work already done on the task
    big=0.0,            # share of big tasks
    big_hours=(40, 120),
    work_ratio=5.0,     # wall-clock hours per worked hour (online about 17-20 % of the time)
)''')

sub('''    __slots__ = ("id", "epic", "left", "deps", "hub", "state", "hours", "created", "ready_at", "done_at", "claims")''',
    '''    __slots__ = ("id", "epic", "left", "deps", "hub", "state", "hours", "created", "ready_at", "done_at", "claims",
                 "big", "spent")''')
sub('''        self.claims = 0
''', '''        self.claims = 0
        self.big = False
        self.spent = 0.0
''')
sub('''        self.zombie_hold = []
        if self.c["renew"] == "progress":
            self.c["push_p"] = 1.0''', '''        self.zombie_hold = []
        self.c["push_p"] = 1.0
        self.lost_cap = self.lost_lapse = self.extensions = 0
        self.handover = 0.0
        self.honest_claims = 0''')
sub('''            t = Task(base + k, e, k < c["hubs"], rng.uniform(*self.cfg["impl_hours"]), now)''',
    '''            big = k >= c["hubs"] and rng.random() < c["big"]
            t = Task(base + k, e, k < c["hubs"], rng.uniform(*(c["big_hours"] if big else self.cfg["impl_hours"])), now)
            t.big = big''')

# --- claim: lease = first push window; silent / zombie / fake pushers hold the task as "phantoms"
sub('''        if r < c["ghost"]:
            self.ghosts += 1
            # half of silent claims never start ("I'll take it"), the others stop part way through
            done = 0.0 if rng.random() < 0.5 else rng.random() * t.hours * 0.5
            self.ghost_work += done
            if done and rng.random() < c["push_p"]:
                left = t.hours - done + self.cfg["handover_hours"]
            else:
                left, self.wasted = t.hours, self.wasted + done
            until = now + done + lease
            if c["first_push"] and not done:
                until = now + c["first_push"]
            self.hold(t, min(until, now + cap) if cap else until, left)
            return None
        if r < c["ghost"] + c["zombie"]:
            self.zombies += 1
            if c["first_push"]:
                until = now + c["first_push"]
            elif c["renew"] == "progress":
                until = now + lease
            else:
                until = now + rng.expovariate(1 / (self.cfg["churn_days"] * 24)) + lease
            if cap:
                until = min(until, now + cap)
            self.zombie_hold.append(until - now)
            self.hold(t, until, t.hours)
            return None
        return [("impl", t), t.hours, now + lease, now]''', '''        F, R = c["first_push"], c["renew_h"]
        if r < c["ghost"]:
            self.ghosts += 1
            # half of silent claims never start ("I'll take it"), the others stop part way through
            done = 0.0 if rng.random() < 0.5 else rng.random() * t.hours * 0.5
            self.ghost_work += done
            until = now + F if not done else now + done * c["work_ratio"] + R
            if cap:
                until = min(until, now + cap)
            left = t.hours - done
            if done:
                left += self.ramp(t, done)
            self.hold(t, until, left)
            return None
        if r < c["ghost"] + c["zombie"]:
            self.zombies += 1
            until = now + F
            self.zombie_hold.append(until - now)
            self.hold(t, until, t.hours)
            return None
        if r < c["ghost"] + c["zombie"] + c["fake"]:
            self.zombies += 1
            if cap:
                windows = 1
                if c["extension"]:
                    while rng.random() > c["ext_detect"]:
                        windows += 1
                until = now + windows * cap
            else:
                until = now + rng.expovariate(1 / (self.cfg["churn_days"] * 24)) + R
            self.zombie_hold.append(until - now)
            self.hold(t, until, t.hours)
            return None
        self.honest_claims += 1
        return [("impl", t), t.hours, now + F, now, None, 0.0]

    def ramp(self, t, done):
        cost = self.c["handover_fixed"] + self.c["handover_frac"] * (t.spent + done)
        self.handover += cost
        return cost

    def push(self, job, now):
        job[4] = now
        job[5] += 1
        job[2] = now + self.c["renew_h"]''')

sub('''    def capped(self, job, now):
        cap = self.lease_cap(job[0][1])
        return bool(cap) and now - job[3] > cap''', '''    def capped(self, job, now):
        cap = self.lease_cap(job[0][1])
        if not cap or now - job[3] <= cap:
            return False
        if self.c["extension"] and job[4] is not None and now - job[4] < 48:
            self.extensions += 1
            job[3] = now
            return False
        self.capped_now = True
        return True''')

sub('''    def lose(self, job):
        t, left = job[0][1], job[1]
        if self.rng.random() < self.c["push_p"]:
            return left + self.cfg["handover_hours"]
        self.wasted += t.hours - left
        return t.hours''', '''    def lose(self, job):
        t, left = job[0][1], job[1]
        done = job[5]
        cost = self.ramp(t, done) if done else 0.0
        t.spent += done
        return left + cost''')
sub('''        self.lost += 1
        t = job[0][1]
        t.hours = self.lose(job)''', '''        self.lost += 1
        if getattr(self, "capped_now", False):
            self.lost_cap += 1
        else:
            self.lost_lapse += 1
        self.capped_now = False
        t = job[0][1]
        t.hours = self.lose(job)''')
sub('''        left = self.lose(job)
        cap = self.lease_cap(t)
        self.hold(t, now + min(self.cfg["lease_impl"], cap or 10**9), left)''', '''        left = self.lose(job)
        cap = self.lease_cap(t)
        until = job[2]
        if cap:
            until = min(until, job[3] + cap)
        self.hold(t, max(now, until), left)''')

sub('''        work = self.m["work_hours"] + self.ghost_work''', '''        work = self.m["work_hours"] + self.ghost_work
        bigs = [t for t in self.tasks if t.big and t.created < hours - 30 * 24]
        big_done = [t.done_at - t.ready_at for t in bigs if t.done_at is not None and t.ready_at is not None]''')
sub('''                    held_by_nobody_end=len(self.phantom),''', '''                    held_by_nobody_end=len(self.phantom),
                    lost_cap=self.lost_cap, lost_lapse=self.lost_lapse, extensions=self.extensions,
                    honest_claims=self.honest_claims, handover=round(100 * self.handover / max(1, work), 1),
                    big_n=len(bigs), big_open=sum(t.done_at is None for t in bigs),
                    big_med=pct(big_done, 0.5), big_p95=pct(big_done, 0.95),''')

(ROOT / "lease_sim.py").write_text(src, encoding="utf-8")
print("lease_sim.py written")
