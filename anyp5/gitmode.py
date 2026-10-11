"""Git mode: any%5 without a server. A git branch is the ledger, issue comments are the inbox, and a scheduled
GitHub Action runs the coordinator.

- Contributors post commands as issue comments, sealed to the coordinator and signed with their key.
- Changes are sent the same way, as `git format-patch` output, so they stay private until approved. A canary
  is then a copy of a pending change with one planted defect, and nothing public tells them apart.
- Each member's view (assigned diffs, their changes, their tasks) is sealed to them in boxes/<login>.sealed.
  It never shows calibration: watching it change would tell which reviews were canaries.
- The coordinator applies approved patches to the project branch and pushes; reverts the same way. With CI,
  approved patches go through a merge queue first: batches are tested on a candidate branch and only a green
  candidate is fast-forwarded to the project branch (see `Coordinator.queue`).
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
from .engine import Engine, review_item

EXTRA = """
create table if not exists keys(login text primary key, box text, sign text, seq int default 0);
create table if not exists boxes(login text primary key, digest text);
create table if not exists commits(change_id int primary key, sha text);
create table if not exists inbox(last int);
create table if not exists lane(data text);
create table if not exists ci_failed(change_id int primary key, sha text, url text);
"""
CI = {"required": "auto", "workflow": "anyp5-ci.yml", "check": "anyp5-ci", "branch": "anyp5-queue", "batch": 16,
      "timeout_minutes": 60}
ADMIN_ONLY = {"admit"}


_last_seq = 0


def command(key, login, cmd, project=None, **args):
    """What a contributor posts: a signed command, sealed to the coordinator."""
    global _last_seq
    _last_seq = max(time.time_ns() // 1000, _last_seq + 1)
    payload = {"login": login, "seq": _last_seq, "cmd": cmd, "args": args, "project": project}
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


INBOX_TITLE = "anyp5 inbox"
INBOX_ROTATE_AT = 2000      # GitHub disables commenting on an issue at 2,500 comments


def inbox_issues(repo, gh=None, trusted=None):
    """Every inbox issue opened by the bot or an admin, newest first: [(number, comment count, open)]. Anyone can
    open an issue with the same title; those are ignored."""
    if gh is None:
        from .forge import gh
    found = json.loads(gh("api", "-X", "GET", "search/issues", "-f",
                          f'q=repo:{repo} is:issue in:title "{INBOX_TITLE}"', "-f", "sort=created",
                          "-f", "order=desc", "-f", "per_page=100") or "{}").get("items", [])
    trusted = set(trusted or ()) | {"github-actions[bot]"}
    return [(i["number"], i["comments"], i["state"] == "open") for i in found
            if i["title"] == INBOX_TITLE and i["user"]["login"] in trusted]


class GitHubInbox:
    """Comments on the inbox issues. GitHub vouches for who posted each comment.

    Reads the repository's comment stream newest first and stops at the last processed comment: one pass costs
    about one API call per 100 new comments. An issue stops taking comments at 2,500, so the inbox rotates to a
    new issue at 2,000 (`rotate`); clients post to the newest open one."""

    def __init__(self, repo, issue, gh=None, trusted=()):
        from .forge import gh as default_gh
        self.repo, self.issue, self.gh, self.trusted = repo, issue, gh or default_gh, trusted

    def fetch(self, after):
        numbers = {self.issue} | {n for n, _, _ in inbox_issues(self.repo, self.gh, self.trusted)}
        suffixes = tuple(f"/issues/{n}" for n in numbers)
        items, page = [], 1
        while True:
            batch = json.loads(self.gh("api", f"repos/{self.repo}/issues/comments?sort=created&direction=desc"
                                              f"&per_page=100&page={page}") or "[]")
            for c in batch:
                if c["id"] > after and c["issue_url"].endswith(suffixes):
                    at = datetime.fromisoformat(c["created_at"].replace("Z", "+00:00")).timestamp()
                    items.append((c["id"], c["user"]["login"], c["body"], at))
            if len(batch) < 100 or min(c["id"] for c in batch) <= after:
                return sorted(items)
            page += 1

    def rotate(self):
        """Open a fresh inbox issue before the current one fills up, and lock the full ones."""
        from .forge import gh
        issues = inbox_issues(self.repo, trusted=self.trusted)
        current = [i for i in issues if i[2]]
        if current and current[0][1] < INBOX_ROTATE_AT:
            return None
        gh("issue", "create", "--repo", self.repo, "--title", INBOX_TITLE,
           "--body", "Sealed any%5 commands. Post them with the anyp5 CLI.")
        for number, _, is_open in issues:
            if is_open:
                gh("issue", "close", str(number), "--repo", self.repo, "--comment",
                   "Inbox full: the anyp5 CLI now posts to the newest open \"anyp5 inbox\" issue.")
        return True


def touches_ci(diff):
    from .tiers import parse_diff
    paths, _ = parse_diff(diff or "")
    return any(path.startswith(".github/") for path in paths)


class GitHubCI:
    """CI results of merge queue candidates, through `gh`. `start` runs the project's CI workflow on the queue
    branch (a push made with the Action's own token starts no workflow, a workflow_dispatch does); `status` reads
    the check run of that workflow's job for one commit: (success | failure | pending | error, url)."""

    def __init__(self, repo, workflow=CI["workflow"], check=CI["check"], gh=None):
        from .forge import gh as default_gh
        self.repo, self.workflow, self.check, self.gh = repo, workflow, check, gh or default_gh

    def start(self, branch, sha):
        self.gh("workflow", "run", self.workflow, "--repo", self.repo, "--ref", branch)

    def status(self, sha):
        runs = json.loads(self.gh("api", f"repos/{self.repo}/commits/{sha}/check-runs?check_name={self.check}"
                                         "&filter=latest") or "{}").get("check_runs", [])
        runs = [r for r in runs if (r.get("app") or {}).get("slug") == "github-actions"]
        if not runs:
            return "pending", None
        run = max(runs, key=lambda r: r["id"])
        if run["status"] != "completed":
            return "pending", run.get("html_url")
        if run["conclusion"] in ("success", "failure"):
            return run["conclusion"], run.get("html_url")
        return "error", run.get("html_url")     # cancelled, timed out, stale: the runner failed, not the code


def same_change(landed, reviewed):
    """Same files and same added and removed lines, ignoring index lines and hunk offsets."""
    def key(diff):
        out = []
        for line in diff.splitlines():
            if line.startswith(("diff --git", "+", "-", "rename ", "copy ", "new file mode", "deleted file mode",
                                "old mode", "new mode", "GIT binary patch", "Binary files")) or \
                    line[:1] in ("z",) and False:
                out.append(line.rstrip())
        return sorted(out)
    return key(landed) == key(reviewed)


class Project:
    """The project's git checkout: approved patches are applied with `git am`, then pushed once per pass."""

    def __init__(self, workdir, branch="main", push=True):
        self.dir, self.branch, self.push = workdir, branch, push

    def sync(self):
        git(self.dir, "fetch", "origin", self.branch)
        git(self.dir, "checkout", "-q", "-B", self.branch, f"origin/{self.branch}")

    def apply(self, patch, reviewed=None, login=None):
        """git am, then check that exactly one commit landed and that it changes exactly what reviewers saw;
        the commit is attributed to the member who sent it, whatever the patch claims."""
        base = git(self.dir, "rev-parse", "HEAD")
        if subprocess.run(["git", "am", "--3way"], cwd=self.dir, input=patch.encode(), capture_output=True).returncode:
            git(self.dir, "am", "--abort", check=False)
            return None
        landed = git(self.dir, "show", "--format=", "--binary", "HEAD")
        if git(self.dir, "rev-list", "--count", f"{base}..HEAD") != "1" or \
                (reviewed is not None and same_change(landed, reviewed) is False):
            git(self.dir, "reset", "-q", "--hard", base)
            return None
        if login:
            git(self.dir, "commit", "-q", "--amend", "--no-edit", "--no-verify",
                f"--author={login} <{login}@users.noreply.github.com>")
        return git(self.dir, "rev-parse", "HEAD")

    def revert(self, sha):
        if subprocess.run(["git", "revert", "--no-edit", sha], cwd=self.dir, capture_output=True).returncode:
            git(self.dir, "revert", "--abort", check=False)
            return None
        return git(self.dir, "rev-parse", "HEAD")

    def fetch(self, branch):
        """Fetch another branch of the project (the merge queue); its head or None."""
        git(self.dir, "fetch", "-q", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}", check=False)
        return git(self.dir, "rev-parse", "-q", "--verify", f"refs/remotes/origin/{branch}^{{commit}}",
                   check=False) or None

    def head(self):
        return git(self.dir, "rev-parse", "HEAD")

    def has_ci(self, workflow):
        return (Path(self.dir) / ".github" / "workflows" / workflow).is_file()

    def descends(self, sha):
        """True if `sha` is built on the current HEAD, so HEAD can be fast-forwarded to it."""
        return subprocess.run(["git", "merge-base", "--is-ancestor", "HEAD", sha], cwd=self.dir,
                              capture_output=True).returncode == 0

    def fast_forward(self, sha):
        git(self.dir, "merge", "-q", "--ff-only", sha)

    def checkout(self, branch, start):
        git(self.dir, "checkout", "-q", "-B", branch, start)

    def publish_refs(self, refs):
        """One atomic push of {branch: sha}: the project branch must fast-forward, other branches (the queue) are
        replaced. False if refused: nothing moved, retry next pass."""
        if not self.push or not refs:
            return True
        specs = [f"{sha}:refs/heads/{b}" if b == self.branch else f"+{sha}:refs/heads/{b}" for b, sha in refs.items()]
        if subprocess.run(["git", "push", "-q", "--atomic", "origin", *specs], cwd=self.dir,
                          capture_output=True).returncode == 0:
            return True
        git(self.dir, "checkout", "-q", "-B", self.branch, f"origin/{self.branch}", check=False)
        return False

    def publish(self):
        """One push for the whole pass. False if the branch moved meanwhile: nothing is recorded, retry next pass."""
        if not self.push:
            return True
        if subprocess.run(["git", "push", "-q", "origin", f"HEAD:{self.branch}"], cwd=self.dir,
                          capture_output=True).returncode == 0:
            return True
        git(self.dir, "reset", "-q", "--hard", f"origin/{self.branch}", check=False)
        return False


class Coordinator:
    def __init__(self, ledger, key, inbox, project, config=None, ci=None):
        self.ledger, self.key, self.inbox, self.project, self.ci = Path(ledger), key, inbox, project, ci
        self.meta = json.loads((self.ledger / "anyp5.json").read_text())
        self.tmp = tempfile.mkdtemp(prefix="anyp5-")
        db = os.path.join(self.tmp, "state.sqlite")
        sealed = self.ledger / "state.sealed"
        if sealed.exists():
            Path(db).write_bytes(seal.unseal(key, sealed.read_text()))
        merged = {**self.meta.get("config", {}), **(config or {})}
        # Project canaries are data here (no script hooks: the Action never runs code from the ledger).
        from .canary import check_recipe, load_recipes
        merged["canaries"] = [check_recipe(r, hooks=False) for r in merged.get("canaries", [])] + \
            load_recipes(self.ledger / "canaries", key=key)
        self.e = Engine(db, config=merged)
        self.e.db.executescript(EXTRA)
        self.public_log = []
        self.joined = set()

    # ------------------------------------------------------------ inbox
    def run(self, now=None):
        # The database is a scratch copy: only the sealed snapshot written at the end of the pass is kept, so a
        # disk sync per command buys nothing (it was most of the inbox time at 10,000 members).
        self.e.db.execute("pragma synchronous=off")
        self.e.db.execute("pragma journal_mode=memory")
        last = (self.e.one("select last from inbox") or {"last": 0})["last"]
        for item_id, author, body, at in self.inbox.fetch(last):
            try:
                self.handle(author, body, at)
            except Exception as err:   # one bad comment must never stop the coordinator
                self.e.db.rollback()
                self.e.log(at, "refused", by=author, error=f"{type(err).__name__}: {err}"[:300])
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
        if p.get("project") != self.meta.get("project"):
            raise ValueError("command is for another project")
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
            admin = login in self.meta.get("admins", [])
            e.add_task(args["title"], args.get("tier", "green"), at, args.get("requires", []), args.get("deps", []),
                       bool(args.get("critical")) and admin, None if admin else login)
        elif cmd == "direct":
            e.direct(login, int(args["task"]), args["decision"], at, str(args.get("reason", "")))
        elif cmd == "caps":
            e.set_caps(login, args["caps"], at)
        elif cmd == "away":
            e.away(login, int(args["task"]), float(args["days"]), at)
        elif cmd == "extend":
            e.extend(login, int(args["task"]), args["diff"], at)
        elif cmd == "claim":
            e.claim(login, int(args["task"]), at)
        elif cmd == "handover":
            e.handover(int(args["task"]), str(args["branch"]), at, login=login)
        elif cmd == "finish":
            if login not in self.meta.get("admins", []):
                raise ValueError("a task finishes when its change merges")
            e.finish_task(int(args["task"]), at)
        elif cmd == "confirm-ban":
            if login not in self.meta.get("admins", []):
                raise ValueError("admin only")
            e.confirm_ban(int(args["change"]), at)
        elif cmd == "submit":
            e.submit_patch(login, args["patch"], at, int(args["task"]) if args.get("task") else None)
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
        known = self.e.one("select * from keys where login=?", login)
        if known is not None and message is not None and known["sign"] != keys["sign"]:
            raise ValueError("this login already has keys; a maintainer must re-admit it")
        if self.e.member(login=login) is None:
            self.e.join(login, at)
        self.e.db.execute("insert or replace into keys(login, box, sign, seq) values(?,?,?,"
                          "coalesce((select seq from keys where login=?), 0))", (login, keys["box"], keys["sign"], login))
        self.e.db.commit()
        self.joined.add(login)
        self.public_log.append({"at": at, "event": "join", "login": login})

    # ------------------------------------------------------------ the project branch
    def forge(self, now):
        """Apply approved changes to the project branch: one push per pass; if that push is refused, each change
        is retried alone and a change whose own push is refused is marked as a conflict, so one change can never
        hold `main` still. Changes to the CI configuration are left to a maintainer: code that merges must not
        be able to rewrite the workflow that holds the coordinator key. When CI is required, approved changes go
        through the merge queue instead (`queue`)."""
        e = self.e
        self.prune()
        work = e.q("select * from changes where kind='pr' and ((state='approved' and patch is not null) or "
                   "(state='revert' and id in (select change_id from commits)))")
        lane = self.lane()
        busy = bool(lane["candidate"] or lane["groups"])
        if not work and not busy:
            return
        for c in [c for c in work if c["state"] == "approved" and touches_ci(c["diff"])]:
            e.db.execute("update changes set state='maintainer' where id=?", (c["id"],))
            e.log(now, "needs-maintainer", change=c["id"])
        work = [c for c in work if not (c["state"] == "approved" and touches_ci(c["diff"]))]
        if not work and not busy:
            e.db.commit()
            return
        self.project.sync()
        if self.ci_required():
            self.queue(work, lane, now)
            e.db.commit()
            return
        done = self.land(work)
        if done is None:
            done, refused = [], []
            for c in work:
                self.project.sync()
                one = self.land([c])
                (refused.append(c) if one is None else done.extend(one))
            if not done:
                e.log(now, "push-refused", changes=len(work))
                e.db.commit()
                return
            for c in refused:
                e.db.execute("update changes set state='conflict' where id=?", (c["id"],))
                e.log(now, "push-refused", change=c["id"])
        self.record(done, now)
        e.db.commit()

    def record(self, done, now):
        e = self.e
        for c, sha in done:
            if sha is None:
                e.db.execute("update changes set state='conflict' where id=?", (c["id"],))
                e.log(now, "conflict", change=c["id"])
            elif c["state"] == "approved":
                e.db.execute("insert or replace into commits(change_id, sha) values(?,?)", (c["id"], sha))
                e.db.execute("update changes set patch=null where id=?", (c["id"],))
                e.merged(c["id"], now)
                self.public_log.append({"at": now, "event": "merged", "commit": sha, "tier": c["tier"]})
            else:
                e.reverted(c["id"], now)
                self.public_log.append({"at": now, "event": "reverted", "commit": sha})

    # ------------------------------------------------------------ the merge queue
    def ci_settings(self):
        return {**CI, **(self.e.cfg.get("ci") or {})}

    def ci_required(self):
        """`required: auto` (the default) gates merges on CI when the project branch holds the CI workflow; a
        project without one keeps the direct push, rather than a queue that would wait forever."""
        cfg = self.ci_settings()
        required = cfg["required"]
        if required == "auto":
            required = self.ci is not None and self.project.has_ci(cfg["workflow"])
        if required and self.ci is None:
            raise RuntimeError("CI is required but the coordinator has no CI provider")
        return bool(required)

    def lane(self):
        row = self.e.one("select data from lane")
        return json.loads(row["data"]) if row else {"groups": [], "candidate": None}

    def queue(self, work, lane, now):
        """One lane of the merge queue, advanced once per pass.

        Approved changes are tested in batches (`ci.batch`, default 16) on the candidate branch `ci.branch`:
        the project branch plus the batch, one commit per change. A green candidate is fast-forwarded to the
        project branch. A red one is split in halves, first half first, until a change that fails alone is
        isolated: it goes to `ci_failed`, which tells its author and costs nothing else. A candidate with no
        result after `ci.timeout_minutes`, or whose run was cancelled, is rebuilt and tested again. Reverts go to
        the project branch directly. Everything moves in one atomic push; if it is refused, nothing is recorded."""
        e, cfg, project = self.e, self.ci_settings(), self.project
        rows = {c["id"]: c for c in work}
        approved = {i for i, c in rows.items() if c["state"] == "approved"}
        groups = [[i for i in g if i in approved] for g in lane["groups"]]
        cand = lane["candidate"]
        landed, failed, conflicts, events = [], [], [], []
        if cand:
            live = [i for i in cand["changes"] if i in approved]
            current = project.fetch(cfg["branch"]) == cand["sha"]
            if live != cand["changes"] or not current or not project.descends(cand["sha"]):
                groups.insert(0, live)
                events.append(("ci-stale", {"changes": cand["changes"]}))
                cand = None
            else:
                try:
                    status, url = self.ci.status(cand["sha"])
                except Exception:    # the forge API is down: wait, the timeout still applies
                    status, url = "pending", None
                if status == "success":
                    project.fast_forward(cand["sha"])
                    landed = [(rows[i], cand["shas"][str(i)]) for i in live]
                    events.append(("ci-green", {"commit": cand["sha"], "changes": live}))
                    cand = None
                elif status == "failure":
                    if len(live) == 1:
                        failed.append((live[0], cand["sha"], url))
                    else:
                        half = (len(live) + 1) // 2
                        groups[:0] = [live[:half], live[half:]]
                    events.append(("ci-red", {"commit": cand["sha"], "changes": live}))
                    cand = None
                elif status == "error" or now - cand["started"] > cfg["timeout_minutes"] * 60:
                    groups.insert(0, live)
                    events.append(("ci-retry", {"commit": cand["sha"], "changes": live, "status": status}))
                    cand = None
        reverts = []
        for c in work:
            if c["state"] == "revert":
                row = e.one("select sha from commits where change_id=?", c["id"])
                reverts.append((c, row["sha"] if project.revert(row["sha"]) else None))
        main = project.head()
        refs = {project.branch: main} if landed or any(sha for _, sha in reverts) else {}
        if cand is None:
            queued = {i for g in groups for i in g}
            fresh = sorted(i for i in approved if i not in queued)
            groups = [g for g in groups if g] + [fresh[k:k + cfg["batch"]] for k in range(0, len(fresh), cfg["batch"])]
            while groups and cand is None:
                group = groups.pop(0)
                project.checkout(cfg["branch"], main)
                shas = {}
                for i in group:
                    sha = project.apply(rows[i]["patch"], rows[i]["diff"], rows[i]["author"])
                    if sha is None:
                        conflicts.append(i)
                    else:
                        shas[str(i)] = sha
                if shas:
                    cand = {"sha": project.head(), "changes": [int(i) for i in shas], "shas": shas, "started": now}
                    refs[cfg["branch"]] = cand["sha"]
            project.checkout(project.branch, main)
        groups = [g for g in groups if g]
        if not project.publish_refs(refs):
            e.log(now, "push-refused", changes=len(work))
            return
        for what, data in events:
            e.log(now, what, **data)
        if cfg["branch"] in refs and project.push:
            try:
                self.ci.start(cfg["branch"], cand["sha"])
            except Exception as err:     # no result will come: the candidate times out and is retried
                e.log(now, "ci-start-failed", error=f"{type(err).__name__}: {err}"[:300])
        for i in conflicts:
            e.db.execute("update changes set state='conflict' where id=?", (i,))
            e.log(now, "conflict", change=i)
        for i, sha, url in failed:
            e.db.execute("update changes set state='ci_failed' where id=?", (i,))
            e.db.execute("insert or replace into ci_failed(change_id, sha, url) values(?,?,?)", (i, sha, url))
            e.log(now, "ci-failed", change=i)
        self.record(landed + reverts, now)
        e.db.execute("delete from lane")
        e.db.execute("insert into lane(data) values(?)", (json.dumps({"groups": groups, "candidate": cand}),))

    def land(self, changes):
        """Apply then push; [(change, sha or None)] or None when the push was refused."""
        out = []
        for c in changes:
            if c["state"] == "approved":
                out.append((c, self.project.apply(c["patch"], c["diff"], c["author"])))
            else:
                row = self.e.one("select sha from commits where change_id=?", c["id"])
                out.append((c, row["sha"] if self.project.revert(row["sha"]) else None))
        if any(sha for _, sha in out) and not self.project.publish():
            return None
        return out

    def prune(self):
        """Settled changes keep their metadata but not their text, so the state follows open work, not history."""
        self.e.db.execute("update changes set patch=null, diff=null where kind='pr' and diff is not null and "
                          "state in ('rejected','superseded','conflict','ci_failed') and id not in "
                          "(select change_id from assignments where verdict is null)")
        self.e.db.execute("update changes set diff=null where kind='canary' and diff is not null and id not in "
                          "(select change_id from assignments where verdict is null)")

    # ------------------------------------------------------------ what everyone can read
    def view(self, login, now):
        """A member's box. All boxes are built from a few grouped queries once per pass, not per member."""
        if getattr(self, "_views_at", None) != now:
            self._views, self._views_at = self.all_views(), now
        return self._views[login]

    def all_views(self, recent=50):
        e = self.e
        reviews, tasks, mine = {}, {}, {}
        for r in e.q("select a.id, a.reviewer, c.diff, c.kind from assignments a join changes c on c.id=a.change_id "
                     "where a.verdict is null order by a.drawn_at"):
            reviews.setdefault(r["reviewer"], []).append(review_item(r))
        for t in e.q("select id, title, tier, branch, holder from tasks where state='leased'"):
            tasks.setdefault(t["holder"], []).append({k: t[k] for k in ("id", "title", "tier", "branch")})
        rows = e.q("select * from (select id, author, tier, state, row_number() over "
                   "(partition by author order by id desc) rank from changes where kind='pr') "
                   "where rank <= ? order by id", recent)
        reasons, shas = {}, {}
        if rows:
            first = min(r["id"] for r in rows)
            for r in e.q("select change_id, reason from assignments where verdict='reject' and reason != '' "
                         "and change_id >= ?", first):
                reasons.setdefault(r["change_id"], []).append(r["reason"])
            shas = {r["change_id"]: r["sha"] for r in e.q("select * from commits where change_id >= ?", first)}
            for r in e.q("select * from ci_failed where change_id >= ?", first):
                reasons.setdefault(r["change_id"], []).append(
                    f'CI failed with this change alone on commit {r["sha"]}' + (f': {r["url"]}' if r["url"] else ""))
        for c in rows:
            mine.setdefault(c["author"], []).append({"change": c["id"], "tier": c["tier"], "state": c["state"],
                                                     "reasons": reasons.get(c["id"], []), "commit": shas.get(c["id"])})
        proposals = {}
        for t in e.q("select id, title, state, proposer, direction from tasks where proposer is not null "
                     "and state in ('proposed', 'rejected', 'off-direction') order by id desc limit 1000"):
            proposals.setdefault(t["proposer"], []).append({"task": t["id"], "title": t["title"], "state": t["state"],
                                                            "reason": t["direction"]})
        direction = e.awaiting_direction()
        views = {}
        for m in e.q("select * from members"):
            view = {"login": m["login"], "banned": bool(m["banned"]), "reviews": reviews.get(m["login"], []),
                    "changes": mine.get(m["login"], []), "tasks": tasks.get(m["login"], [])}
            if m["login"] in proposals:
                view["proposals"] = proposals[m["login"]]
            if m["login"] in e.cfg["architects"]:
                view["direction"] = direction
            views[m["login"]] = view
        return views

    def seeds(self, now, reveal_after=4 * 7 * 24 * 3600):
        """Commit to this week's draw seed now, reveal it once it is old: draws are unpredictable while they
        matter, and checkable afterwards."""
        from .engine import EPOCH
        e = self.e
        done = {(r["what"], json.loads(r["data"])["epoch"])
                for r in e.q("select what, data from events where what in ('seed-commit','seed-reveal')")}
        epoch, seed = e.seed(now)
        if ("seed-commit", epoch) not in done:
            e.log(now, "seed-commit", epoch=epoch)
            self.public_log.append({"at": now, "event": "seed-commit", "epoch": epoch,
                                    "sha256": hashlib.sha256(seed).hexdigest()})
        for what, old in sorted(done):
            if what == "seed-commit" and ("seed-reveal", old) not in done and (old + 1) * EPOCH + reveal_after <= now:
                e.log(now, "seed-reveal", epoch=old)
                self.public_log.append({"at": now, "event": "seed-reveal", "epoch": old,
                                        "seed": e.seed(old * EPOCH)[1].hex()})

    def write(self, now):
        e, root = self.e, self.ledger
        (root / "members").mkdir(exist_ok=True)
        (root / "boxes").mkdir(exist_ok=True)
        # Boxes are written in a seeded order and stamped with the pass time only: neither the write order nor a
        # timestamp follows the order in which commands arrived.
        for k in e.shuffled(e.q("select * from keys"), now, "boxes", key=lambda k: k["login"]):
            card = root / "members" / f'{k["login"]}.json'
            if k["login"] in self.joined or not card.exists():
                card.write_text(json.dumps({"box": k["box"], "sign": k["sign"]}))
            view = self.view(k["login"], now)
            digest = hashlib.sha256(json.dumps(view, sort_keys=True).encode()).hexdigest()
            old = e.one("select digest from boxes where login=?", k["login"])
            if old is None or old["digest"] != digest:
                body = json.dumps({**view, "updated": int(now)}, sort_keys=True)
                (root / "boxes" / f'{k["login"]}.sealed').write_text(seal.seal(k["box"], body.encode()))
                e.db.execute("insert or replace into boxes(login, digest) values(?,?)", (k["login"], digest))
        tasks = [{k: t[k] for k in ("id", "title", "tier", "branch", "requires", "critical", "waiting")}
                 for t in e.open_tasks(limit=500)]
        (root / "tasks.json").write_text(json.dumps(tasks, indent=1))
        board = e.wanted(now)
        (root / "wanted.json").write_text(json.dumps({"updated": int(now), "capabilities": board}, indent=1))
        (root / "wanted.md").write_text(wanted_markdown(board, now))
        release = e.releasable(now)
        sha = e.one("select sha from commits where change_id=?", release["id"]) if release else None
        counts = {r["state"]: r["n"] for r in e.q("select state, count(*) n from changes where kind='pr' group by state")}
        lane = self.lane()
        queue = {"candidate": (lane["candidate"] or {}).get("sha"), "queued": sum(map(len, lane["groups"]))}
        (root / "status.json").write_text(json.dumps({"updated": int(now), "changes": counts, "queue": queue,
                                                      "releasable_commit": sha["sha"] if sha else None}, indent=1))
        self.seeds(now)
        done = e.one("select max(cast(data as integer)) n from events where what='ban_logged'")["n"] or 0
        bans = e.q("select * from events where what='ban' and id>? order by id", done)
        for ev in bans:
            entry = {"at": ev["at"], "event": "ban", "login": json.loads(ev["data"])["login"]}
            if entry not in self.public_log:
                self.public_log.append(entry)
        if bans:
            e.db.execute("insert into events(at, what, data) values(?,?,?)", (now, "ban_logged", str(bans[-1]["id"])))
        if self.public_log:
            with open(root / "log.jsonl", "a", encoding="utf-8") as f:
                for entry in e.shuffled(self.public_log, now, "log", key=lambda x: json.dumps(x, sort_keys=True)):
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


def wanted_markdown(board, now):
    """The block a project shows in its README: where to start, and which profile it is short of."""
    lines = ["| You can run | Open tasks | Waiting on others | Active members | Tasks per member |",
             "|---|---:|---:|---:|---:|"]
    for row in board:
        need = " + ".join(row["requires"]) or "nothing special (code, docs, artifacts)"
        flag = " **wanted**" if row["tasks_per_member"] >= 2 else ""
        lines.append(f'| {need}{flag} | {row["open"]} | {row["blocked"]} | {row["members"]} | {row["tasks_per_member"]} |')
    updated = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(now))
    return "\n".join(lines) + f"\n\nUpdated {updated}. Declare what you can run with `anyp5 caps windows gpu ...`.\n"


def local_path(path, base=None):
    """Resolve a path given on the command line (often by an agent) and refuse anything outside the working
    directory, so a crafted argument cannot read or write files elsewhere on the machine."""
    root = os.path.realpath(base or os.getcwd())
    full = os.path.realpath(path)
    if full != root and not full.startswith(root + os.sep):
        raise ValueError(f"{path} is outside the working directory {root}")
    return full


def update_readme(readme, block):
    """Replace the text between <!-- anyp5:wanted --> and <!-- /anyp5:wanted --> (run daily, not every pass)."""
    path = Path(readme)
    text = path.read_text(encoding="utf-8")
    start, end = "<!-- anyp5:wanted -->", "<!-- /anyp5:wanted -->"
    if start not in text or end not in text:
        return False
    head, rest = text.split(start, 1)
    new = head + start + "\n" + block + end + rest.split(end, 1)[1]
    if new == text:
        return False
    path.write_text(new, encoding="utf-8")
    return True


def init_ledger(path, coordinator_key, admins, project, branch="main", issue=None, config=None):
    root = Path(path)
    root.mkdir(parents=True, exist_ok=True)
    meta = {"coordinator": seal.public(coordinator_key), "admins": admins, "project": project, "branch": branch,
            "inbox_issue": issue, "state_branch": "anyp5-state",
            "config": {"canary_source": "pending", **(config or {})}}
    (root / "anyp5.json").write_text(json.dumps(meta, indent=1))
    # Rewritten every pass and unreadable to git's delta compression: kept on a single-commit branch instead.
    (root / ".gitignore").write_text("state.sealed\nboxes/\n")
    return meta


class Client:
    """A contributor's side of git mode: read your sealed box, post sealed commands. Uses the `gh` CLI."""

    def __init__(self, repo, branch, login, key):
        self.repo, self.branch, self.login, self.key = repo, branch, login, key
        self._meta = None

    def read(self, path, branch=None):
        from .forge import gh
        return gh("api", f"repos/{self.repo}/contents/{path}?ref={branch or self.branch}", "-H",
                  "Accept: application/vnd.github.raw")

    def meta(self):
        if self._meta is None:
            self._meta = json.loads(self.read("anyp5.json"))
        return self._meta

    def send(self, cmd, **args):
        meta = self.meta()
        body = seal.seal(meta["coordinator"]["box"], command(self.key, self.login, cmd, project=meta["project"], **args).encode())
        if len(body) > 65000:
            raise ValueError("command too large for one comment: split the change")
        try:
            issue = next(n for n, _, is_open in inbox_issues(self.repo, trusted=meta.get("admins")) if is_open)
        except (StopIteration, RuntimeError):
            issue = meta["inbox_issue"]
        subprocess.run(["gh", "issue", "comment", str(issue), "--repo", self.repo, "--body-file", "-"],
                       input=body, text=True, check=True, capture_output=True)

    def box(self):
        try:
            return json.loads(seal.unseal(self.key, self.read(f"boxes/{self.login}.sealed",
                                                              self.meta().get("state_branch"))))
        except RuntimeError:
            return {"login": self.login, "note": "no box yet: the coordinator has not run since you joined"}
