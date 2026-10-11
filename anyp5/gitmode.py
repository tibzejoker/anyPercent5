"""Git mode: any%5 without a server. A git branch is the ledger, issue comments are the inbox, and a scheduled
GitHub Action runs the coordinator.

- Contributors post commands as issue comments, sealed to the coordinator and signed with their key.
- Changes are sent the same way, as `git format-patch` output, so they stay private until approved. A canary
  is then a copy of a pending change with one planted defect, and nothing public tells them apart.
- Each member's view (assigned diffs, their changes, calibration) is sealed to them in boxes/<login>.sealed.
- The coordinator applies approved patches to the project branch and pushes; reverts the same way.
- The engine state is sealed to the coordinator (it knows which reviews are canaries); log.jsonl is public.

Ledger layout: anyp5.json, members/<login>.json, boxes/<login>.sealed, tasks.json, status.json, log.jsonl,
state.sealed.
"""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

from . import seal
from .engine import Engine

EXTRA = """
create table if not exists keys(login text primary key, box text, sign text, seq int default 0);
create table if not exists boxes(login text primary key, digest text);
create table if not exists commits(change_id int primary key, sha text);
create table if not exists inbox(last int);
"""
ADMIN_ONLY = {"admit"}


def command(key, login, cmd, **args):
    """What a contributor posts: a signed command, sealed to the coordinator."""
    payload = {"login": login, "seq": time.time_ns() // 1000, "cmd": cmd, "args": args}
    return json.dumps(seal.sign(key, payload))


def git(cwd, *args, stdin=None, check=True):
    proc = subprocess.run(["git", *args], cwd=cwd, input=stdin, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout.strip()


class ListInbox:
    """Inbox for tests and local runs: (id, login, body, created_at) tuples."""

    def __init__(self):
        self.items = []

    def post(self, login, body, at):
        self.items.append((len(self.items) + 1, login, body, at))

    def fetch(self, after):
        return [i for i in self.items if i[0] > after]


class GitHubInbox:
    """Comments on one issue of the ledger repo. GitHub vouches for who posted each comment."""

    def __init__(self, repo, issue):
        self.repo, self.issue = repo, issue

    def fetch(self, after):
        from .forge import gh
        pages = gh("api", "--paginate", f"repos/{self.repo}/issues/{self.issue}/comments?per_page=100")
        items = []
        for chunk in pages.replace("][", "]\n[").splitlines():
            for c in json.loads(chunk or "[]"):
                if c["id"] > after:
                    at = datetime.fromisoformat(c["created_at"].replace("Z", "+00:00")).timestamp()
                    items.append((c["id"], c["user"]["login"], c["body"], at))
        return sorted(items)


class Project:
    """The project's git checkout: approved patches are applied with `git am` and pushed."""

    def __init__(self, workdir, branch="main", push=True):
        self.dir, self.branch, self.push = workdir, branch, push

    def sync(self):
        git(self.dir, "fetch", "origin", self.branch)
        git(self.dir, "checkout", "-q", "-B", self.branch, f"origin/{self.branch}")

    def apply(self, patch):
        if subprocess.run(["git", "am", "--3way"], cwd=self.dir, input=patch.encode(), capture_output=True).returncode:
            git(self.dir, "am", "--abort", check=False)
            return None
        return self.publish()

    def revert(self, sha):
        if subprocess.run(["git", "revert", "--no-edit", sha], cwd=self.dir, capture_output=True).returncode:
            git(self.dir, "revert", "--abort", check=False)
            return None
        return self.publish()

    def publish(self):
        if self.push:
            git(self.dir, "push", "-q", "origin", f"HEAD:{self.branch}")
        return git(self.dir, "rev-parse", "HEAD")


class Coordinator:
    def __init__(self, ledger, key, inbox, project, config=None):
        self.ledger, self.key, self.inbox, self.project = Path(ledger), key, inbox, project
        self.meta = json.loads((self.ledger / "anyp5.json").read_text())
        self.tmp = tempfile.mkdtemp(prefix="anyp5-")
        db = os.path.join(self.tmp, "state.sqlite")
        sealed = self.ledger / "state.sealed"
        if sealed.exists():
            Path(db).write_bytes(seal.unseal(key, sealed.read_text()))
        self.e = Engine(db, config={**self.meta.get("config", {}), **(config or {})})
        self.e.db.executescript(EXTRA)
        self.public_log = []

    # ------------------------------------------------------------ inbox
    def run(self, now=None):
        last = (self.e.one("select last from inbox") or {"last": 0})["last"]
        for item_id, author, body, at in self.inbox.fetch(last):
            try:
                self.handle(author, body, at)
            except (ValueError, KeyError, TypeError) as err:
                self.e.log(at, "refused", by=author, error=str(err))
            last = item_id
        self.e.db.execute("delete from inbox")
        self.e.db.execute("insert into inbox(last) values(?)", (last,))
        now = now or time.time()
        self.e.tick(now)
        self.forge(now)
        self.write(now)

    def handle(self, author, body, at):
        if not seal.is_sealed(body):
            return
        message = json.loads(seal.unseal(self.key, body))
        p = message["payload"]
        login, cmd, args = p["login"], p["cmd"], p["args"]
        if cmd == "join":
            if author != login:
                raise ValueError("join must be posted by the GitHub account it names")
            self.add_member(login, args["keys"], message, at)
            return
        signer = self.e.one("select * from keys where login=?", login)
        if signer is None or not seal.verify(signer["sign"], message):
            raise ValueError("bad signature")
        if p["seq"] <= signer["seq"]:
            raise ValueError("replayed command")
        self.e.db.execute("update keys set seq=? where login=?", (p["seq"], login))
        member = self.e.member(login=login)
        if member["banned"]:
            raise ValueError("banned")
        if cmd in ADMIN_ONLY and login not in self.meta.get("admins", []):
            raise ValueError("admin only")
        e = self.e
        e.alive(login, at)
        if cmd == "admit":
            self.add_member(args["member"], args["keys"], None, at)
        elif cmd == "task":
            e.add_task(args["title"], args.get("tier", "green"), at)
        elif cmd == "claim":
            e.claim(login, int(args["task"]), at)
        elif cmd == "handover":
            e.handover(int(args["task"]), args["branch"], at)
        elif cmd == "finish":
            e.finish_task(int(args["task"]), at)
        elif cmd == "submit":
            e.submit_patch(login, args["patch"], at)
        elif cmd == "verdict":
            mine = e.one("select id from assignments where id=? and reviewer=?", int(args["assignment"]), login)
            if mine is None:
                raise ValueError("not your assignment")
            e.verdict(login, int(args["assignment"]), args["verdict"], at, args.get("severity", "none"),
                      args.get("reason", ""))
        elif cmd != "alive":
            raise ValueError(f"unknown command {cmd}")
        e.db.commit()

    def add_member(self, login, keys, message, at):
        if message is not None and not seal.verify(keys["sign"], message):
            raise ValueError("join not signed by the key it registers")
        if self.e.member(login=login) is None:
            self.e.join(login, at)
        self.e.db.execute("insert or replace into keys(login, box, sign, seq) values(?,?,?,"
                          "coalesce((select seq from keys where login=?), 0))", (login, keys["box"], keys["sign"], login))
        self.e.db.commit()
        self.public_log.append({"at": at, "event": "join", "login": login})

    # ------------------------------------------------------------ the project branch
    def forge(self, now):
        e = self.e
        work = e.q("select * from changes where kind='pr' and patch is not null and state in ('approved','revert')")
        if not work:
            return
        self.project.sync()
        for c in work:
            if c["state"] == "approved":
                sha = self.project.apply(c["patch"])
                if sha is None:
                    e.db.execute("update changes set state='conflict' where id=?", (c["id"],))
                    e.log(now, "conflict", change=c["id"])
                    continue
                e.db.execute("insert or replace into commits(change_id, sha) values(?,?)", (c["id"], sha))
                e.merged(c["id"], now)
                self.public_log.append({"at": now, "event": "merged", "commit": sha, "tier": c["tier"]})
            else:
                row = e.one("select sha from commits where change_id=?", c["id"])
                if row is None or self.project.revert(row["sha"]):
                    e.reverted(c["id"], now)
                    self.public_log.append({"at": now, "event": "reverted", "commit": row and row["sha"]})
        e.db.commit()

    # ------------------------------------------------------------ what everyone can read
    def view(self, login, now):
        e = self.e
        m = e.member(login=login)
        reviews = e.q("select a.id, c.diff from assignments a join changes c on c.id=a.change_id "
                      "where a.reviewer=? and a.verdict is null order by a.drawn_at", login)
        mine = []
        for c in e.q("select id, tier, state from changes where kind='pr' and author=? order by id", login):
            reasons = [r["reason"] for r in e.q("select reason from assignments where change_id=? and verdict='reject' "
                                                "and reason != ''", c["id"])]
            sha = e.one("select sha from commits where change_id=?", c["id"])
            mine.append({"change": c["id"], "tier": c["tier"], "state": c["state"], "reasons": reasons,
                         "commit": sha["sha"] if sha else None})
        tasks = [dict(t) for t in e.q("select id, title, tier, branch from tasks where holder=? and state='leased'", login)]
        return {"login": login, "banned": bool(m["banned"]), "calibration": [m["cal_hits"], m["cal_seen"]],
                "reviews": [{"assignment": r["id"], "diff": r["diff"]} for r in reviews],
                "changes": mine, "tasks": tasks}

    def write(self, now):
        e, root = self.e, self.ledger
        (root / "members").mkdir(exist_ok=True)
        (root / "boxes").mkdir(exist_ok=True)
        for k in e.q("select * from keys"):
            (root / "members" / f'{k["login"]}.json').write_text(json.dumps({"box": k["box"], "sign": k["sign"]}))
            view = json.dumps(self.view(k["login"], now), sort_keys=True)
            digest = hashlib.sha256(view.encode()).hexdigest()
            old = e.one("select digest from boxes where login=?", k["login"])
            if old is None or old["digest"] != digest:
                (root / "boxes" / f'{k["login"]}.sealed').write_text(seal.seal(k["box"], view.encode()))
                e.db.execute("insert or replace into boxes(login, digest) values(?,?)", (k["login"], digest))
        tasks = [dict(t) for t in e.q("select id, title, tier, branch from tasks where state='open' order by id")]
        (root / "tasks.json").write_text(json.dumps(tasks, indent=1))
        release = e.releasable(now)
        sha = e.one("select sha from commits where change_id=?", release["id"]) if release else None
        counts = {r["state"]: r["n"] for r in e.q("select state, count(*) n from changes where kind='pr' group by state")}
        (root / "status.json").write_text(json.dumps({"updated": int(now), "changes": counts,
                                                      "releasable_commit": sha["sha"] if sha else None}, indent=1))
        for ev in e.q("select * from events where what='ban'"):
            entry = {"at": ev["at"], "event": "ban", "login": json.loads(ev["data"])["login"]}
            if entry not in self.public_log:
                self.public_log.append(entry)
        if self.public_log:
            with open(root / "log.jsonl", "a", encoding="utf-8") as f:
                for entry in self.public_log:
                    f.write(json.dumps(entry) + "\n")
            self.public_log = []
        e.db.commit()
        snapshot = os.path.join(self.tmp, "snapshot.sqlite")
        if os.path.exists(snapshot):
            os.remove(snapshot)
        dst = sqlite3.connect(snapshot)
        e.db.backup(dst)
        dst.close()
        (root / "state.sealed").write_text(seal.seal(self.meta["coordinator"]["box"], Path(snapshot).read_bytes()))

    def close(self):
        self.e.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


def init_ledger(path, coordinator_key, admins, project, branch="main", issue=None, config=None):
    root = Path(path)
    root.mkdir(parents=True, exist_ok=True)
    meta = {"coordinator": seal.public(coordinator_key), "admins": admins, "project": project, "branch": branch,
            "inbox_issue": issue, "config": {"canary_source": "pending", **(config or {})}}
    (root / "anyp5.json").write_text(json.dumps(meta, indent=1))
    return meta


class Client:
    """A contributor's side of git mode: read your sealed box, post sealed commands. Uses the `gh` CLI."""

    def __init__(self, repo, branch, login, key):
        self.repo, self.branch, self.login, self.key = repo, branch, login, key
        self._meta = None

    def read(self, path):
        from .forge import gh
        return gh("api", f"repos/{self.repo}/contents/{path}?ref={self.branch}", "-H", "Accept: application/vnd.github.raw")

    def meta(self):
        if self._meta is None:
            self._meta = json.loads(self.read("anyp5.json"))
        return self._meta

    def send(self, cmd, **args):
        meta = self.meta()
        body = seal.seal(meta["coordinator"]["box"], command(self.key, self.login, cmd, **args).encode())
        if len(body) > 65000:
            raise ValueError("command too large for one comment: split the change")
        subprocess.run(["gh", "issue", "comment", str(meta["inbox_issue"]), "--repo", self.repo, "--body-file", "-"],
                       input=body, text=True, check=True, capture_output=True)

    def box(self):
        try:
            return json.loads(seal.unseal(self.key, self.read(f"boxes/{self.login}.sealed")))
        except RuntimeError:
            return {"login": self.login, "note": "no box yet: the coordinator has not run since you joined"}
