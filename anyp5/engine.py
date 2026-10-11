"""The any%5 engine: leases, blind calibrated review draws, soak-gated releases, ban cascades.

Everything is a pure function of the ledger (SQLite) and an explicit `now` (unix seconds), so the server,
the GitHub adapter and the tests all drive the same code.
"""
import hashlib
import hmac
import json
import secrets
import sqlite3

from .tiers import classify, parse_diff

DEFAULTS = {
    "reviewers": {"green": 1, "amber": 3, "red": 2},
    "post_review": {"green": 0.6, "amber": 1.0, "red": 0.0},
    "audit": {"green": 0.0, "amber": 0.0, "red": 1.0},
    "soak_days": 14,
    "lease_hours": {"task": 72, "review": 24, "first_push": 24, "cap": 120, "extension": 120},
    "away": {"max_days": 5, "days_per_30": 21, "blocking_hours": 48},
    "relay_hours": 12,
    "spare_after_hours": 6,
    "canary_every": 2.5,
    "canary_confirm_max": 5,
    "active_hours": 48,
    "escalation_auditors": 2,
    "canary_source": "merged",
}

SCHEMA = """
create table if not exists members(login text primary key, token text unique, joined real, last_seen real,
    banned int default 0, cal_seen int default 0, cal_hits int default 0, caps text default '[]');
create table if not exists tasks(id integer primary key, title text, tier text, state text default 'open',
    holder text, lease_until real, branch text, created real, claimed real, cap_until real,
    requires text default '[]', deps text default '[]', critical int default 0, proposer text);
create table if not exists changes(id integer primary key, kind text, pr int, head text, author text, tier text,
    diff text, state text, created real, merged_at real, defect text, source int, patch text);
create table if not exists assignments(id integer primary key, change_id int, reviewer text, kind text,
    drawn_at real, lease_until real, verdict text, severity text, reason text, done_at real);
create table if not exists events(id integer primary key, at real, what text, data text);
create table if not exists secret(value text);
create index if not exists assignments_reviewer on assignments(reviewer, verdict);
create index if not exists assignments_change on assignments(change_id);
create index if not exists assignments_open on assignments(verdict, lease_until);
create index if not exists changes_state on changes(state, kind);
create index if not exists changes_author on changes(author);
create index if not exists tasks_state on tasks(state, holder);
create index if not exists events_what on events(what);
"""
EPOCH = 7 * 24 * 3600


def review_item(row):
    """What a reviewer sees. Real changes and canaries look the same; task proposals and progress checks say
    what is asked (they are never canaries)."""
    item = {"assignment": row["id"], "diff": row["diff"]}
    if row["kind"] == "task":
        item["question"] = "Is this a useful, well-scoped task for the project? Approve to open it."
    elif row["kind"] == "progress":
        item["question"] = "Is this branch real progress on its task (not padding)? Approve to extend the lease."
    return item


def score(m):
    return (m["cal_hits"] + 1) / (m["cal_seen"] + 2)


def chance(key, p, seed=b""):
    """Deterministic coin: hmac(seed, key) < p. With a hidden seed, an author cannot grind their change to dodge
    a post-merge check; once the seed is revealed, anyone can recompute it."""
    return int(hmac.new(seed, key.encode(), "sha256").hexdigest()[:8], 16) / 0xFFFFFFFF < p


class Engine:
    def __init__(self, path=":memory:", config=None, rules=None):
        self.cfg = json.loads(json.dumps(DEFAULTS))
        for key, value in (config or {}).items():
            self.cfg[key] = value
        self.rules = rules
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        if self.one("select value from secret") is None:
            self.db.execute("insert into secret(value) values(?)", (secrets.token_hex(32),))
            self.db.commit()
        for table, col, kind in (("changes", "source", "int"), ("changes", "patch", "text"),
                                 ("tasks", "claimed", "real"), ("tasks", "cap_until", "real"),
                                 ("tasks", "requires", "text default '[]'"), ("tasks", "deps", "text default '[]'"),
                                 ("tasks", "critical", "int default 0"), ("tasks", "proposer", "text"),
                                 ("members", "caps", "text default '[]'"), ("changes", "task", "int")):
            if col not in {r["name"] for r in self.q(f"pragma table_info({table})")}:
                self.db.execute(f"alter table {table} add column {col} {kind}")

    # ------------------------------------------------------------ helpers
    def q(self, sql, *args):
        return self.db.execute(sql, args).fetchall()

    def one(self, sql, *args):
        return self.db.execute(sql, args).fetchone()

    def log(self, now, what, **data):
        self.db.execute("insert into events(at, what, data) values(?,?,?)", (now, what, json.dumps(data)))

    def seed(self, now):
        """This week's draw seed. Hidden from everyone but the coordinator while it is in use, so nobody can
        predict or steer who reviews a change; `sha256(seed)` is published first and the seed revealed later."""
        epoch = int(now // EPOCH)
        key = bytes.fromhex(self.one("select value from secret")["value"])
        return epoch, hmac.new(key, f"epoch:{epoch}".encode(), "sha256").digest()

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
        """Heartbeat: keeps the member in the reviewer pool and renews review leases. Task leases are renewed
        only by pushed work (`handover`): a heartbeat alone would let a stalled holder block dependent tasks."""
        self.db.execute("update members set last_seen=? where login=?", (now, login))
        self.db.execute("update assignments set lease_until=? where reviewer=? and verdict is null",
                        (now + self.hours(self.cfg["lease_hours"]["review"]), login))
        self.db.commit()

    # ------------------------------------------------------------ capabilities
    def set_caps(self, login, caps, now):
        """What a member can run (e.g. windows, gpu, game). Unverified: it only decides which tasks they may
        claim, never who reviews; ask for run evidence in the first push of a task that needs it."""
        self.db.execute("update members set caps=? where login=?", (json.dumps(sorted(set(caps))), login))
        self.db.commit()

    # ------------------------------------------------------------ tasks (work is leased, never owned)
    def add_task(self, title, tier, now, requires=(), deps=(), critical=False, proposer=None):
        """A task from a maintainer opens at once; one proposed by a member is reviewed first, like a change."""
        state = "proposed" if proposer else "open"
        cur = self.db.execute("insert into tasks(title, tier, created, requires, deps, critical, state, proposer) "
                              "values(?,?,?,?,?,?,?,?)", (title, tier, now, json.dumps(sorted(set(requires))),
                                                          json.dumps(sorted(set(map(int, deps)))), int(critical),
                                                          state, proposer))
        if proposer:
            text = (f"Proposed task: {title}\ntier: {tier}\nrequires: {', '.join(requires) or 'nothing'}\n"
                    f"depends on tasks: {', '.join(map(str, deps)) or 'none'}\n")
            change = self.db.execute("insert into changes(kind, author, tier, diff, state, created, source) "
                                     "values('task', ?, 'green', ?, 'review', ?, ?)", (proposer, text, now, cur.lastrowid))
            self.log(now, "task-proposed", task=cur.lastrowid, change=change.lastrowid)
        self.db.commit()
        return cur.lastrowid

    def blocked(self, task):
        deps = json.loads(task["deps"] or "[]")
        if not deps:
            return False
        done = self.one("select count(*) n from tasks where state='done' and id in (%s)" % ",".join("?" * len(deps)), *deps)
        return done["n"] < len(deps)

    def dependents(self, task_id):
        return [t["id"] for t in self.q("select id, deps from tasks where state!='done' and deps!='[]'")
                if task_id in json.loads(t["deps"])]

    def can_do(self, login, task):
        caps = set(json.loads(self.member(login=login)["caps"] or "[]"))
        return set(json.loads(task["requires"] or "[]")) <= caps

    def open_tasks(self, login=None, limit=50):
        """Claimable tasks, the ones others wait on first. With a login: only those this member can do."""
        rows = [t for t in self.q("select * from tasks where state='open'") if not self.blocked(t)]
        if login:
            rows = [t for t in rows if self.can_do(login, t)]
        waiting = {t["id"]: len(self.dependents(t["id"])) for t in rows}
        rows.sort(key=lambda t: (-t["critical"], -waiting[t["id"]], t["id"]))
        return [dict(t, waiting=waiting[t["id"]]) for t in rows[:limit]]

    def claim(self, login, task_id, now):
        if self.pending_checks(login):
            raise ValueError("jury duty first: finish your assigned reviews before claiming new work")
        task = self.one("select * from tasks where id=?", task_id)
        if task is None or task["state"] != "open":
            raise ValueError("task is not open")
        if self.blocked(task):
            raise ValueError("task waits on unfinished tasks")
        if not self.can_do(login, task):
            raise ValueError("task needs capabilities you have not declared: " + task["requires"])
        lease = self.cfg["lease_hours"]
        self.db.execute("update tasks set state='leased', holder=?, lease_until=?, claimed=?, cap_until=? where id=?",
                        (login, now + self.hours(lease["first_push"]), now, now + self.hours(lease["cap"]), task_id))
        self.log(now, "claim", login=login, task=task_id)
        self.db.commit()

    def handover(self, task_id, branch, now):
        """Pushed work renews the lease (and ends an absence), never past the cap; the next holder resumes the
        branch. The cap moves only when a reviewer confirms real progress (`extend`)."""
        lease = self.cfg["lease_hours"]
        self.db.execute("update tasks set branch=?, lease_until=min(?, coalesce(cap_until, ?)) "
                        "where id=? and state='leased'", (branch, now + self.hours(lease["task"]), now, task_id))
        self.db.execute("update tasks set branch=? where id=?", (branch, task_id))
        self.log(now, "push", task=task_id)
        self.db.commit()

    def away(self, login, task_id, days, now):
        """A declared absence freezes the lease, not the cap. Only after a first push; short on a task others
        wait on, never on a critical one; bounded per month."""
        cfg = self.cfg["away"]
        task = self.one("select * from tasks where id=? and holder=? and state='leased'", task_id, login)
        if task is None:
            raise ValueError("you do not hold this task")
        if not task["branch"]:
            raise ValueError("push a first commit before declaring an absence")
        if task["critical"]:
            raise ValueError("critical tasks relay to whoever is online: hand it over instead")
        used = sum(d["hours"] for d in (json.loads(r["data"]) for r in self.q(
            "select data from events where what='away' and at>?", now - 30 * 86400)) if d["login"] == login)
        hours = min(min(float(days), cfg["max_days"]) * 24, cfg["days_per_30"] * 24 - used)
        if self.dependents(task_id):
            hours = min(hours, cfg["blocking_hours"])
        if hours <= 0:
            raise ValueError("no absence left this month")
        remaining = max(task["lease_until"] - now, 0)
        self.db.execute("update tasks set lease_until=min(?, cap_until) where id=?",
                        (now + self.hours(hours) + remaining, task_id))
        self.log(now, "away", login=login, task=task_id, hours=hours)
        self.db.commit()
        return hours

    def extend(self, login, task_id, diff, now):
        """At the cap, a holder who is really progressing keeps the task: one reviewer checks the branch diff."""
        task = self.one("select * from tasks where id=? and holder=? and state='leased'", task_id, login)
        if task is None:
            raise ValueError("you do not hold this task")
        cur = self.db.execute("insert into changes(kind, author, tier, diff, state, created, source) "
                              "values('progress', ?, 'green', ?, 'review', ?, ?)", (login, diff, now, task_id))
        self.db.commit()
        return cur.lastrowid

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

    def submit_patch(self, author, patch, now, task=None):
        """A change sent privately (git mode): reviewers see only its diff, never the author or the message."""
        head = hashlib.sha256(patch.encode()).hexdigest()[:40]
        old = self.one("select id from changes where kind='pr' and head=?", head)
        if old:
            return old["id"]
        start = patch.find("diff --git")
        diff = patch[start:] if start >= 0 else patch
        diff = diff.split("\n-- \n")[0] + "\n"
        paths, added = parse_diff(diff)
        tier = classify(paths, added, self.rules)
        cur = self.db.execute("insert into changes(kind, head, author, tier, diff, state, created, patch, task) "
                              "values('pr',?,?,?,?,'review',?,?,?)", (head, author, tier, diff, now, patch, task))
        self.log(now, "submit", change=cur.lastrowid, tier=tier)
        self.db.commit()
        return cur.lastrowid

    def related(self, change):
        """The same change in several versions (a pending change and its canary copies): one reviewer each,
        so nobody can compare two versions and spot the planted defect."""
        root = change["source"] or change["id"]
        return [r["id"] for r in self.q("select id from changes where id=? or source=?", root, root)]

    def pending_checks(self, login):
        return self.q("select * from assignments where reviewer=? and verdict is null", login)

    def idle_pool(self, now, exclude=()):
        rows = self.q("select * from members where banned=0 and last_seen>=?",
                      now - self.hours(self.cfg["active_hours"]))
        busy = {r["reviewer"] for r in self.q("select reviewer from assignments where verdict is null")}
        return [m for m in rows if m["login"] not in busy and m["login"] not in exclude]

    def draw(self, change, count, kind, now, pool=None, least_tested=False):
        """Ranked by calibration score; ties broken by a keyed hash of this week's hidden seed."""
        ids = self.related(change)
        taken = {r["reviewer"] for r in self.q("select reviewer from assignments where change_id in (%s)"
                                               % ",".join("?" * len(ids)), *ids)}
        pool = pool if pool is not None else self.idle_pool(now, exclude=taken | {change["author"]})
        salt = f'{change["id"]}:{change["head"]}:{kind}:{len(taken)}'
        epoch, seed = self.seed(now)
        tie = lambda m: hmac.new(seed, f'{salt}:{m["login"]}'.encode(), "sha256").hexdigest()
        if least_tested:
            pool = sorted(pool, key=lambda m: (m["cal_seen"], tie(m)))
        else:
            pool = sorted(pool, key=lambda m: (-score(m), tie(m)))
        chosen = pool[:count]
        for m in chosen:
            self.db.execute("insert into assignments(change_id, reviewer, kind, drawn_at, lease_until) values(?,?,?,?,?)",
                            (change["id"], m["login"], kind, now, now + self.hours(self.cfg["lease_hours"]["review"])))
            self.log(now, "draw", change=change["id"], reviewer=m["login"], kind=kind, salt=salt, epoch=epoch)
        return [m["login"] for m in chosen]

    # ------------------------------------------------------------ the blind review interface
    def next_review(self, login, now):
        """What a reviewer sees: an id and a diff. Canaries and real changes look the same."""
        self.alive(login, now)
        rows = self.q("select a.id, c.diff, c.kind from assignments a join changes c on c.id=a.change_id "
                      "where a.reviewer=? and a.verdict is null order by a.drawn_at", login)
        return [review_item(r) for r in rows]

    def verdict(self, login, assignment_id, verdict, now, severity="none", reason=""):
        a = self.one("select * from assignments where id=? and reviewer=? and verdict is null", assignment_id, login)
        if a is None:
            raise ValueError("no such open assignment")
        if verdict not in ("approve", "reject"):
            raise ValueError("verdict is approve or reject")
        self.db.execute("update assignments set verdict=?, severity=?, reason=?, done_at=? where id=?",
                        (verdict, severity, reason, now, assignment_id))
        change = self.one("select * from changes where id=?", a["change_id"])
        if change["kind"] in ("task", "progress"):
            self.settle_side(change, verdict, now)
        elif change["kind"] == "canary":
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

    def settle_side(self, change, verdict, now):
        """Task proposals and progress checks: one reviewer decides, no calibration involved."""
        self.db.execute("update changes set state=? where id=?",
                        ("approved" if verdict == "approve" else "rejected", change["id"]))
        if change["kind"] == "task":
            self.db.execute("update tasks set state=? where id=? and state='proposed'",
                            ("open" if verdict == "approve" else "rejected", change["source"]))
        elif verdict == "approve":
            ext = self.hours(self.cfg["lease_hours"]["extension"])
            self.db.execute("update tasks set cap_until=max(coalesce(cap_until, ?), ?) + ? where id=? and state='leased'",
                            (now, now, ext, change["source"]))
            self.db.execute("update tasks set lease_until=max(lease_until, ?) where id=? and state='leased'",
                            (now + self.hours(self.cfg["lease_hours"]["task"]), change["source"]))
            self.log(now, "extended", task=change["source"])

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
        if change["task"]:
            self.db.execute("update tasks set state='done' where id=?", (change["task"],))
        for kind, rates in (("post", self.cfg["post_review"]), ("audit", self.cfg["audit"])):
            if chance(f'{kind}:{change_id}:{change["head"]}', rates[change["tier"]], self.seed(now)[1]):
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
        relay = now - self.hours(self.cfg["relay_hours"])
        for t in self.q("select t.* from tasks t join members m on m.login=t.holder where t.state='leased' "
                        "and t.critical=1 and m.last_seen<?", relay):
            self.db.execute("update tasks set state='open', holder=null where id=?", (t["id"],))
            self.log(now, "relay", task=t["id"], holder=t["holder"], branch=t["branch"])
        for a in self.q("select * from assignments where verdict is null and lease_until<?", now):
            self.db.execute("update assignments set verdict='expired', done_at=? where id=?", (now, a["id"]))
            self.log(now, "review-expired", assignment=a["id"], reviewer=a["reviewer"])
        for change in self.q("select * from changes where state='review'"):
            live = self.q("select * from assignments where change_id=? and kind='review' "
                          "and (verdict is null or verdict='approve')", change["id"])
            required = 1 if change["kind"] in ("task", "progress") else self.cfg["reviewers"][change["tier"]]
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
        # "pending": copy a change still under review, so a canary cannot be found in the public history.
        state = "review" if self.cfg["canary_source"] == "pending" else "merged"
        source = self.q("select * from changes where kind='pr' and state=? order by random() limit 1", state)
        if real < (made + 1) * self.cfg["canary_every"] or not source:
            return
        from .canary import plant
        planted = plant(source[0]["diff"], seed=f"{made}:{source[0]['id']}")
        if planted is None:
            return
        diff, defect = planted
        cur = self.db.execute("insert into changes(kind, author, tier, diff, state, created, defect, head, source) "
                              "values('canary','',?,?, 'canary', ?, ?, ?, ?)",
                              (source[0]["tier"], diff, now, defect, hashlib.sha256(diff.encode()).hexdigest(),
                               source[0]["id"]))
        change = self.one("select * from changes where id=?", cur.lastrowid)
        ids = self.related(change)
        seen = {r["reviewer"] for r in self.q("select reviewer from assignments where change_id in (%s)"
                                              % ",".join("?" * len(ids)), *ids)}
        tested = self.idle_pool(now, exclude=seen | {source[0]["author"]})
        fresh = [m for m in tested if m["cal_seen"] < self.cfg["canary_confirm_max"]]
        self.draw(dict(change), 1, "review", now, pool=fresh or tested, least_tested=True)

    def reverted(self, change_id, now):
        """The forge merged the revert of a banned author's change: it no longer blocks releases."""
        self.db.execute("update changes set state='reverted' where id=? and state='revert'", (change_id,))
        self.log(now, "reverted", change=change_id)
        self.db.commit()

    # ------------------------------------------------------------ what the project needs
    def wanted(self, now):
        """Supply and demand per capability set: where newcomers can start, and which profile is the bottleneck."""
        active = self.q("select caps from members where banned=0 and last_seen>=?",
                        now - self.hours(self.cfg["active_hours"]))
        rows = {}
        for t in self.q("select * from tasks where state='open'"):
            need = tuple(json.loads(t["requires"] or "[]"))
            row = rows.setdefault(need, {"requires": list(need), "open": 0, "blocked": 0, "members": 0})
            row["blocked" if self.blocked(t) else "open"] += 1
        for need, row in rows.items():
            row["members"] = sum(1 for m in active if set(need) <= set(json.loads(m["caps"] or "[]")))
            row["tasks_per_member"] = round(row["open"] / max(row["members"], 1), 1)
        return sorted(rows.values(), key=lambda r: -r["tasks_per_member"])

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
