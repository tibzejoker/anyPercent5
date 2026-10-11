"""The any%5 engine: leases, blind calibrated review draws, soak-gated releases, ban cascades.

Everything is a pure function of the ledger (SQLite) and an explicit `now` (unix seconds), so the server,
the GitHub adapter and the tests all drive the same code.
"""
import hashlib
import json
import secrets
import sqlite3

from .tiers import classify

DEFAULTS = {
    "reviewers": {"green": 1, "amber": 3, "red": 2},
    "post_review": {"green": 0.6, "amber": 1.0, "red": 0.0},
    "audit": {"green": 0.0, "amber": 0.0, "red": 1.0},
    "soak_days": 14,
    "lease_hours": {"task": 72, "review": 24},
    "spare_after_hours": 6,
    "canary_every": 2.5,
    "canary_confirm_max": 5,
    "active_hours": 48,
    "escalation_auditors": 2,
}

SCHEMA = """
create table if not exists members(login text primary key, token text unique, joined real, last_seen real,
    banned int default 0, cal_seen int default 0, cal_hits int default 0);
create table if not exists tasks(id integer primary key, title text, tier text, state text default 'open',
    holder text, lease_until real, branch text, created real);
create table if not exists changes(id integer primary key, kind text, pr int, head text, author text, tier text,
    diff text, state text, created real, merged_at real, defect text);
create table if not exists assignments(id integer primary key, change_id int, reviewer text, kind text,
    drawn_at real, lease_until real, verdict text, severity text, reason text, done_at real);
create table if not exists events(id integer primary key, at real, what text, data text);
"""


def score(m):
    return (m["cal_hits"] + 1) / (m["cal_seen"] + 2)


def chance(key, p):
    """Deterministic, publicly checkable coin: sha256(key) < p."""
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF < p


class Engine:
    def __init__(self, path=":memory:", config=None, rules=None):
        self.cfg = json.loads(json.dumps(DEFAULTS))
        for key, value in (config or {}).items():
            self.cfg[key] = value
        self.rules = rules
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ------------------------------------------------------------ helpers
    def q(self, sql, *args):
        return self.db.execute(sql, args).fetchall()

    def one(self, sql, *args):
        return self.db.execute(sql, args).fetchone()

    def log(self, now, what, **data):
        self.db.execute("insert into events(at, what, data) values(?,?,?)", (now, what, json.dumps(data)))

    def hours(self, h):
        return h * 3600

    # ------------------------------------------------------------ members
    def join(self, login, now):
        token = secrets.token_hex(16)
        self.db.execute("insert into members(login, token, joined, last_seen) values(?,?,?,?)", (login, token, now, now))
        self.log(now, "join", login=login)
        self.db.commit()
        return token

    def member(self, token=None, login=None):
        row = self.one("select * from members where token=?", token) if token else \
            self.one("select * from members where login=?", login)
        return row

    def alive(self, login, now):
        """Heartbeat: renews every lease the member holds (the timeout only fires when they go quiet)."""
        self.db.execute("update members set last_seen=? where login=?", (now, login))
        self.db.execute("update tasks set lease_until=? where holder=? and state='leased'",
                        (now + self.hours(self.cfg["lease_hours"]["task"]), login))
        self.db.execute("update assignments set lease_until=? where reviewer=? and verdict is null",
                        (now + self.hours(self.cfg["lease_hours"]["review"]), login))
        self.db.commit()

    # ------------------------------------------------------------ tasks (work is leased, never owned)
    def add_task(self, title, tier, now):
        cur = self.db.execute("insert into tasks(title, tier, created) values(?,?,?)", (title, tier, now))
        self.db.commit()
        return cur.lastrowid

    def claim(self, login, task_id, now):
        if self.pending_checks(login):
            raise ValueError("jury duty first: finish your assigned reviews before claiming new work")
        task = self.one("select * from tasks where id=?", task_id)
        if task is None or task["state"] != "open":
            raise ValueError("task is not open")
        self.db.execute("update tasks set state='leased', holder=?, lease_until=? where id=?",
                        (login, now + self.hours(self.cfg["lease_hours"]["task"]), task_id))
        self.log(now, "claim", login=login, task=task_id)
        self.db.commit()

    def handover(self, task_id, branch, now):
        """Partial work stays on a branch; the next holder resumes it."""
        self.db.execute("update tasks set branch=? where id=?", (branch, task_id))
        self.db.commit()

    def finish_task(self, task_id, now):
        self.db.execute("update tasks set state='done' where id=?", (task_id,))
        self.db.commit()

    # ------------------------------------------------------------ changes
    def submit(self, pr, head, author, paths, added, diff, now):
        old = self.one("select id from changes where kind='pr' and pr=? and head=?", pr, head)
        if old:
            return old["id"]
        self.db.execute("update changes set state='superseded' where kind='pr' and pr=? and state='review'", (pr,))
        tier = classify(paths, added, self.rules)
        cur = self.db.execute("insert into changes(kind, pr, head, author, tier, diff, state, created) "
                              "values('pr',?,?,?,?,?,'review',?)", (pr, head, author, tier, diff, now))
        self.log(now, "submit", pr=pr, head=head, tier=tier)
        self.db.commit()
        return cur.lastrowid

    def pending_checks(self, login):
        return self.q("select * from assignments where reviewer=? and verdict is null", login)

    def idle_pool(self, now, exclude=()):
        rows = self.q("select * from members where banned=0 and last_seen>=?",
                      now - self.hours(self.cfg["active_hours"]))
        busy = {r["reviewer"] for r in self.q("select reviewer from assignments where verdict is null")}
        return [m for m in rows if m["login"] not in busy and m["login"] not in exclude]

    def draw(self, change, count, kind, now, pool=None, least_tested=False):
        """Ranked by calibration score; ties broken by a hash anyone can recompute."""
        taken = {r["reviewer"] for r in self.q("select reviewer from assignments where change_id=?", change["id"])}
        pool = pool if pool is not None else self.idle_pool(now, exclude=taken | {change["author"]})
        salt = f'{change["id"]}:{change["head"]}:{kind}:{len(taken)}'
        tie = lambda m: hashlib.sha256(f'{salt}:{m["login"]}'.encode()).hexdigest()
        if least_tested:
            pool = sorted(pool, key=lambda m: (m["cal_seen"], tie(m)))
        else:
            pool = sorted(pool, key=lambda m: (-score(m), tie(m)))
        chosen = pool[:count]
        for m in chosen:
            self.db.execute("insert into assignments(change_id, reviewer, kind, drawn_at, lease_until) values(?,?,?,?,?)",
                            (change["id"], m["login"], kind, now, now + self.hours(self.cfg["lease_hours"]["review"])))
            self.log(now, "draw", change=change["id"], reviewer=m["login"], kind=kind, salt=salt)
        return [m["login"] for m in chosen]

    # ------------------------------------------------------------ the blind review interface
    def next_review(self, login, now):
        """What a reviewer sees: an id and a diff. Canaries and real changes look the same."""
        self.alive(login, now)
        rows = self.q("select a.id, c.diff from assignments a join changes c on c.id=a.change_id "
                      "where a.reviewer=? and a.verdict is null order by a.drawn_at", login)
        return [{"assignment": r["id"], "diff": r["diff"]} for r in rows]

    def verdict(self, login, assignment_id, verdict, now, severity="none", reason=""):
        a = self.one("select * from assignments where id=? and reviewer=? and verdict is null", assignment_id, login)
        if a is None:
            raise ValueError("no such open assignment")
        if verdict not in ("approve", "reject"):
            raise ValueError("verdict is approve or reject")
        self.db.execute("update assignments set verdict=?, severity=?, reason=?, done_at=? where id=?",
                        (verdict, severity, reason, now, assignment_id))
        change = self.one("select * from changes where id=?", a["change_id"])
        if change["kind"] == "canary":
            hit = verdict == "reject"
            self.db.execute("update members set cal_seen=cal_seen+1, cal_hits=cal_hits+? where login=?", (int(hit), login))
            self.log(now, "canary", login=login, hit=hit)
        elif a["kind"] == "escalation":
            self.check_escalation(change, now)
        elif verdict == "reject" and severity == "backdoor":
            self.escalate(change, now)
        elif verdict == "reject" and a["kind"] == "review":
            self.db.execute("update changes set state='rejected' where id=?", (change["id"],))
        elif verdict == "reject":
            self.add_task(f'fix: defect found after merge in PR #{change["pr"]}: {reason}', change["tier"], now)
        self.db.commit()
        self.settle(change["id"], now)

    # ------------------------------------------------------------ outcomes
    def approvals(self, change_id):
        return [r["reviewer"] for r in self.q("select reviewer from assignments where change_id=? and kind='review' "
                                              "and verdict='approve'", change_id)]

    def settle(self, change_id, now):
        change = self.one("select * from changes where id=?", change_id)
        if change["kind"] == "pr" and change["state"] == "review" and \
                len(self.approvals(change_id)) >= self.cfg["reviewers"][change["tier"]]:
            self.db.execute("update changes set state='approved' where id=?", (change_id,))
            self.log(now, "approved", change=change_id, pr=change["pr"])
            self.db.commit()

    def merged(self, change_id, now):
        """Called once the forge has merged an approved change: it now soaks, with post-merge checks."""
        change = self.one("select * from changes where id=?", change_id)
        self.db.execute("update changes set state='merged', merged_at=? where id=?", (now, change_id))
        for kind, rates in (("post", self.cfg["post_review"]), ("audit", self.cfg["audit"])):
            if chance(f'{kind}:{change_id}:{change["head"]}', rates[change["tier"]]):
                self.draw(dict(change), 1, kind, now)
        self.log(now, "merged", change=change_id)
        self.db.commit()

    def escalate(self, change, now):
        """A backdoor claim is never acted on by one person: the best-calibrated auditors confirm it first."""
        if change["state"] in ("banned-author",):
            return
        open_ = self.q("select id from assignments where change_id=? and kind='escalation'", change["id"])
        if not open_:
            self.draw(dict(change), self.cfg["escalation_auditors"], "escalation", now)
            self.log(now, "escalate", change=change["id"])

    def check_escalation(self, change, now):
        rows = self.q("select verdict, severity from assignments where change_id=? and kind='escalation'", change["id"])
        if len(rows) < self.cfg["escalation_auditors"] or any(r["verdict"] is None for r in rows):
            return
        if all(r["verdict"] == "reject" and r["severity"] == "backdoor" for r in rows):
            self.ban(change["author"], change, now)
        else:
            self.log(now, "escalation-cleared", change=change["id"])

    def ban(self, login, change, now):
        """Ban the author, revert everything they merged, re-check everything their approvers passed."""
        self.db.execute("update members set banned=1 where login=?", (login,))
        self.db.execute("update changes set state='rejected' where author=? and state in ('review','approved')", (login,))
        reverted = self.q("select * from changes where author=? and state='merged'", login)
        self.db.execute("update changes set state='revert' where author=? and state='merged'", (login,))
        approvers = set(self.approvals(change["id"])) - {login}
        tainted = self.q("select distinct c.* from changes c join assignments a on a.change_id=c.id "
                         "where a.reviewer in (%s) and a.verdict='approve' and c.state='merged' and c.kind='pr'"
                         % ",".join("?" * len(approvers)), *approvers) if approvers else []
        for c in tainted:
            self.draw(dict(c), 1, "audit", now)
        self.log(now, "ban", login=login, reverted=[c["id"] for c in reverted], audited=[c["id"] for c in tainted])
        self.db.commit()

    # ------------------------------------------------------------ the clock
    def tick(self, now):
        """Run periodically: expire leases, keep every change staffed, mix in canaries."""
        for t in self.q("select * from tasks where state='leased' and lease_until<?", now):
            self.db.execute("update tasks set state='open', holder=null where id=?", (t["id"],))
            self.log(now, "task-expired", task=t["id"], holder=t["holder"], branch=t["branch"])
        for a in self.q("select * from assignments where verdict is null and lease_until<?", now):
            self.db.execute("update assignments set verdict='expired', done_at=? where id=?", (now, a["id"]))
            self.log(now, "review-expired", assignment=a["id"], reviewer=a["reviewer"])
        for change in self.q("select * from changes where state='review'"):
            live = self.q("select * from assignments where change_id=? and kind='review' "
                          "and (verdict is null or verdict='approve')", change["id"])
            required = self.cfg["reviewers"][change["tier"]]
            if len(live) < required:
                self.draw(dict(change), required - len(live), "review", now)
            stale = [r for r in live if r["verdict"] is None
                     and now - r["drawn_at"] > self.hours(self.cfg["spare_after_hours"])]
            if stale and len(live) <= required:
                self.draw(dict(change), 1, "review", now)
        self.mix_canaries(now)
        self.db.commit()

    def mix_canaries(self, now):
        real = self.one("select count(*) n from changes where kind='pr'")["n"]
        made = self.one("select count(*) n from changes where kind='canary'")["n"]
        source = self.q("select * from changes where kind='pr' and state='merged' order by random() limit 1")
        if real < made * self.cfg["canary_every"] or not source:
            return
        from .canary import plant
        planted = plant(source[0]["diff"], seed=f"{made}:{source[0]['id']}")
        if planted is None:
            return
        diff, defect = planted
        cur = self.db.execute("insert into changes(kind, author, tier, diff, state, created, defect, head) "
                              "values('canary','',?,?, 'canary', ?, ?, ?)",
                              (source[0]["tier"], diff, now, defect, hashlib.sha256(diff.encode()).hexdigest()))
        change = self.one("select * from changes where id=?", cur.lastrowid)
        tested = self.idle_pool(now)
        fresh = [m for m in tested if m["cal_seen"] < self.cfg["canary_confirm_max"]]
        self.draw(dict(change), 1, "review", now, pool=fresh or tested, least_tested=True)

    def reverted(self, change_id, now):
        """The forge merged the revert of a banned author's change: it no longer blocks releases."""
        self.db.execute("update changes set state='reverted' where id=? and state='revert'", (change_id,))
        self.log(now, "reverted", change=change_id)
        self.db.commit()

    # ------------------------------------------------------------ releases
    def releasable(self, now):
        """Newest merged change such that every change merged up to it has soaked and passed its checks."""
        soak = self.hours(24 * self.cfg["soak_days"])
        best = None
        for c in self.q("select * from changes where kind='pr' and state in ('merged','revert','reverted') order by merged_at"):
            if c["state"] == "reverted":
                continue
            open_checks = self.q("select id from assignments where change_id=? and verdict is null", c["id"])
            if c["state"] == "revert" or open_checks or now - c["merged_at"] < soak:
                break
            best = c
        return dict(best) if best else None
