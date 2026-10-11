import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from anyp5 import seal
from anyp5.gitmode import Coordinator, ListInbox, Project, command, init_ledger

H = 3600
SOURCE = "int put(char *b, int size, int i, char c)\n{\n    b[i] = c;\n    return 0;\n}\n"
FIXED = "int put(char *b, int size, int i, char c)\n{\n    if (i < 0 || i >= size)\n        return -1;\n    b[i] = c;\n    return 0;\n}\n"


def git(cwd, *args, stdin=None):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.org", *args], cwd=cwd,
                          input=stdin, capture_output=True, text=True, check=True).stdout


class FakeCI:
    """CI of the pushed candidate: red when its tree holds the word BROKEN, pending while `hold` is set."""

    def __init__(self, repo):
        self.repo, self.started, self.hold = repo, [], False

    def start(self, branch, sha):
        self.started.append((branch, sha))

    def status(self, sha):
        if self.hold:
            return "pending", None
        broken = subprocess.run(["git", "grep", "-q", "BROKEN", sha], cwd=self.repo).returncode == 0
        return ("failure" if broken else "success"), f"https://ci.example/{sha[:7]}"


class GitModeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anyp5-test-"))
        os.environ.update(GIT_COMMITTER_NAME="anyp5", GIT_COMMITTER_EMAIL="anyp5@example.org")
        seed = self.tmp / "seed"
        seed.mkdir()
        git(seed, "init", "-q", "-b", "main")
        (seed / "src").mkdir()
        (seed / "src" / "buf.c").write_text(SOURCE)
        git(seed, "add", ".")
        git(seed, "commit", "-q", "-m", "init")
        git(self.tmp, "clone", "-q", "--bare", str(seed), "project.git")
        git(self.tmp, "clone", "-q", "project.git", "coordinator")
        self.keys = {name: seal.keygen() for name in ("coord", "maint", "alice", "bob", "carol", "dave", "erin")}
        self.ledger = self.tmp / "ledger"
        init_ledger(self.ledger, self.keys["coord"], ["maint"], "local/project",
                    config={"canary_every": 1e9})
        self.inbox = ListInbox()
        self.now = 1_000_000.0
        for name in ("maint", "alice", "bob", "carol", "dave", "erin"):
            self.post(name, "join", keys=seal.public(self.keys[name]))
        self.run_coordinator()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def post(self, name, cmd, author=None, key=None, **args):
        meta = json.loads((self.ledger / "anyp5.json").read_text())
        body = command(key or self.keys[name], name, cmd, project=meta["project"], **args)
        self.inbox.post(author or name, seal.seal(meta["coordinator"]["box"], body.encode()), self.now)

    def run_coordinator(self, config=None, hours=1, project=None, ci=None):
        self.now += hours * H
        c = Coordinator(self.ledger, self.keys["coord"], self.inbox,
                        project or Project(str(self.tmp / "coordinator")), config=config, ci=ci)
        try:
            c.run(self.now)
            return c.e.q("select id, state, patch is null pruned from changes where kind='pr' order by id")
        finally:
            c.close()

    def state(self):
        import sqlite3
        path = self.tmp / "state.sqlite"
        path.write_bytes(seal.unseal(self.keys["coord"], (self.ledger / "state.sealed").read_text()))
        return sqlite3.connect(path)

    def test_a_forged_or_oversized_comment_never_stops_the_coordinator(self):
        import base64, os, zlib
        meta = json.loads((self.ledger / "anyp5.json").read_text())
        self.inbox.post("mallory", seal.PREFIX + base64.b64encode(os.urandom(200)).decode(), self.now)
        self.inbox.post("mallory", seal.seal(meta["coordinator"]["box"], b"{" + b" " * (20 << 20)), self.now)
        self.inbox.post("mallory", seal.seal(meta["coordinator"]["box"], b"not json"), self.now)
        self.post("alice", "task", title=["not", "a", "string"])
        self.post("alice", "submit", patch=self.patch())
        self.run_coordinator()
        self.assertEqual(len(self.box("alice")["changes"]), 1)

    def box(self, name):
        path = self.ledger / "boxes" / f"{name}.sealed"
        return json.loads(seal.unseal(self.keys[name], path.read_text()))

    def patch(self):
        work = self.tmp / "alice-work"
        if work.exists():
            return git(work, "format-patch", "-1", "--stdout")
        git(self.tmp, "clone", "-q", "project.git", "alice-work")
        (work / "src" / "buf.c").write_text(FIXED)
        git(work, "commit", "-qam", "fix: bound check in put")
        return git(work, "format-patch", "-1", "--stdout")

    def reviewers(self):
        return [n for n in ("alice", "bob", "carol", "dave", "erin", "maint") if self.box(n)["reviews"]]

    def test_private_patch_is_reviewed_blind_then_merged_and_pushed(self):
        self.post("alice", "submit", patch=self.patch())
        self.run_coordinator()
        drawn = self.reviewers()
        self.assertEqual(len(drawn), 1)
        self.assertNotIn("alice", drawn)
        job = self.box(drawn[0])["reviews"][0]
        self.assertIn("+    if (i < 0 || i >= size)", job["diff"])
        self.assertNotIn("alice", job["diff"].lower())
        self.assertNotIn("bound check", job["diff"])
        public = "".join(p.read_text() for p in self.ledger.rglob("*") if p.is_file() and p.suffix != ".sealed")
        self.assertNotIn("i >= size", public)

        self.post(drawn[0], "verdict", assignment=job["assignment"], verdict="approve")
        self.run_coordinator()
        log = git(self.tmp / "project.git", "log", "--format=%an %s", "main")
        self.assertIn("alice fix: bound check in put", log)
        self.assertEqual(self.box("alice")["changes"][0]["state"], "merged")
        self.assertIn('"merged"', (self.ledger / "log.jsonl").read_text())

    def test_forged_and_replayed_commands_are_refused(self):
        self.post("bob", "submit", key=self.keys["bob"], patch=self.patch())
        self.post("alice", "submit", author="bob", key=self.keys["bob"], patch=self.patch())
        mallory = seal.keygen()
        self.post("mallory", "join", author="bob", key=mallory, keys=seal.public(mallory))
        self.post("bob", "admit", member="mallory", keys=seal.public(seal.keygen()))
        self.run_coordinator()
        self.assertEqual(self.box("alice")["changes"], [])
        self.assertEqual(len(self.box("bob")["changes"]), 1)
        self.assertFalse((self.ledger / "members" / "mallory.json").exists())

        meta = json.loads((self.ledger / "anyp5.json").read_text())
        old = command(self.keys["maint"], "maint", "task", project=meta["project"], title="again")
        self.inbox.post("maint", seal.seal(meta["coordinator"]["box"], old.encode()), self.now)
        self.inbox.post("maint", seal.seal(meta["coordinator"]["box"], old.encode()), self.now)
        self.run_coordinator()
        tasks = json.loads((self.ledger / "tasks.json").read_text())
        self.assertEqual([t["title"] for t in tasks], ["again"])

    def test_pending_canary_goes_to_someone_who_never_sees_the_original(self):
        self.post("alice", "submit", patch=self.patch())
        self.run_coordinator(config={"canary_every": 0.5, "canary_source": "pending", "clean_canary_share": 0})
        drawn = self.reviewers()
        self.assertEqual(len(drawn), 2)
        self.assertNotIn("alice", drawn)
        diffs = [self.box(n)["reviews"][0]["diff"] for n in drawn]
        self.assertNotEqual(diffs[0], diffs[1])
        for n in drawn:
            self.assertEqual(set(self.box(n)["reviews"][0]), {"assignment", "diff"})
        for n in drawn:
            job = self.box(n)["reviews"][0]
            self.post(n, "verdict", assignment=job["assignment"], verdict="reject",
                      reason="src/buf.c:1-7 the bound or guard looks wrong")
        self.run_coordinator(config={"canary_every": 1e9, "canary_source": "pending", "clean_canary_share": 0})
        self.assertNotIn("calibration", self.box(drawn[0]))
        rows = self.state().execute("select cal_hits, cal_seen from members where login in (?, ?)", drawn).fetchall()
        self.assertEqual(sorted(rows), [(0, 0), (1, 1)])
        self.assertEqual(self.box("alice")["changes"][0]["state"], "review")

    def test_project_canaries_in_the_ledger_are_planted_first(self):
        (self.ledger / "canaries").mkdir()
        recipe = {"name": "bound off by one", "glob": "src/*.c", "match": r"i >= size", "replace": "i > size",
                  "defect": "lets i == size through"}
        (self.ledger / "canaries" / "bounds.sealed").write_text(
            seal.seal(seal.public(self.keys["coord"])["box"], json.dumps([recipe]).encode()))
        self.post("alice", "submit", patch=self.patch())
        self.run_coordinator(config={"canary_every": 0.5, "clean_canary_share": 0})
        defect = self.state().execute("select defect from changes where kind='canary'").fetchone()[0]
        self.assertEqual(defect, "bound off by one: lets i == size through (src/buf.c:3)")
        (self.ledger / "canaries" / "hook.json").write_text(json.dumps({"name": "h", "hook": "os:system"}))
        with self.assertRaises(ValueError):
            self.run_coordinator()

    def test_boxes_are_written_in_seeded_order_with_the_pass_time_only(self):
        from unittest import mock
        self.post("bob", "submit", patch=self.second_patch("bob"))
        self.now += 600
        self.post("alice", "submit", patch=self.patch())
        cards = {json.loads(p.read_text())["box"]: p.stem for p in (self.ledger / "members").glob("*.json")}
        written, real_seal = [], seal.seal

        def spy(box, data):
            written.append(cards.get(box))
            return real_seal(box, data)
        self.now += H
        c = Coordinator(self.ledger, self.keys["coord"], self.inbox, Project(str(self.tmp / "coordinator")))
        try:
            with mock.patch.object(seal, "seal", spy):
                c.run(self.now)
            keys = c.e.q("select login from keys")
            expected = [k["login"] for k in c.e.shuffled(keys, self.now, "boxes", key=lambda k: k["login"])]
        finally:
            c.close()
        boxes = [w for w in written if w is not None]
        self.assertEqual(boxes, [login for login in expected if login in boxes])
        for login in boxes:
            self.assertEqual(self.box(login)["updated"], int(self.now))
        self.assertNotIn("drawn_at", json.dumps([self.box(n) for n in boxes]))

    def second_patch(self, name, body="    return 1;"):
        work = self.tmp / f"{name}-work"
        git(self.tmp, "clone", "-q", "project.git", work.name)
        (work / "src" / f"{name}.c").write_text("int one(void)\n{\n" + body + "\n}\n")
        git(work, "add", ".")
        git(work, "commit", "-qm", f"feat: add {name}.c")
        return git(work, "format-patch", "-1", "--stdout")

    def approve_all(self):
        for n in ("alice", "bob", "carol", "dave", "erin", "maint"):
            for job in self.box(n)["reviews"]:
                self.post(n, "verdict", assignment=job["assignment"], verdict="approve")

    def test_approved_changes_are_pushed_once_per_pass_and_their_text_pruned(self):
        self.post("alice", "submit", patch=self.patch())
        self.post("bob", "submit", patch=self.second_patch("bob"))
        self.run_coordinator()
        self.approve_all()

        class Refused(Project):
            def publish(self):
                git(self.dir, "reset", "-q", "--hard", f"origin/{self.branch}")
                return False
        rows = self.run_coordinator(project=Refused(str(self.tmp / "coordinator")))
        self.assertEqual([r["state"] for r in rows], ["approved", "approved"])
        self.assertNotIn("add bob.c", git(self.tmp / "project.git", "log", "--format=%s", "main"))

        pushes = []

        class Counted(Project):
            def publish(self):
                pushes.append(1)
                return super().publish()
        rows = self.run_coordinator(project=Counted(str(self.tmp / "coordinator")))
        self.assertEqual([(r["state"], r["pruned"]) for r in rows], [("merged", 1), ("merged", 1)])
        self.assertEqual(len(pushes), 1)
        log = git(self.tmp / "project.git", "log", "--format=%s", "main")
        self.assertIn("add bob.c", log)
        self.assertIn("bound check in put", log)
        self.assertEqual([c["state"] for c in self.box("bob")["changes"]], ["merged"])

    def test_a_patch_with_several_commits_is_refused(self):
        work = self.tmp / "multi"
        git(self.tmp, "clone", "-q", "project.git", "multi")
        (work / "src" / "a.c").write_text("int a;\n")
        git(work, "add", ".")
        git(work, "commit", "-qm", "harmless")
        (work / "src" / "b.c").write_text("int b(void) { return system(\"x\"); }\n")
        git(work, "add", ".")
        git(work, "commit", "-qm", "hidden")
        self.post("alice", "submit", patch=git(work, "format-patch", "-2", "--stdout"))
        self.run_coordinator()
        self.assertEqual(self.box("alice")["changes"], [])

    def test_ci_changes_wait_for_a_maintainer(self):
        work = self.tmp / "ci"
        git(self.tmp, "clone", "-q", "project.git", "ci")
        (work / ".github" / "workflows").mkdir(parents=True)
        (work / ".github" / "workflows" / "ci.yml").write_text("on: push\n")
        git(work, "add", ".")
        git(work, "commit", "-qm", "ci")
        self.post("alice", "submit", patch=git(work, "format-patch", "-1", "--stdout"))
        self.run_coordinator()
        for _ in range(3):
            self.approve_all()
            self.run_coordinator()
        self.assertEqual(self.box("alice")["changes"][0]["state"], "maintainer")
        self.assertNotIn("ci", git(self.tmp / "project.git", "log", "--format=%s", "main").split())

    def queued(self, names, broken=(), config=None, ci=None):
        for n in names:
            self.post(n, "submit", patch=self.second_patch(n, "    return BROKEN;" if n in broken else "    return 1;"))
        self.run_coordinator(config=config, ci=ci)
        self.approve_all()
        return self.run_coordinator(config=config, ci=ci)

    def log_of(self, branch="main"):
        return git(self.tmp / "project.git", "log", "--format=%an %s", branch)

    def test_merge_queue_lands_a_green_batch_only_after_its_ci(self):
        work = self.tmp / "install-ci"
        git(self.tmp, "clone", "-q", "project.git", work.name)
        (work / ".github" / "workflows").mkdir(parents=True)
        (work / ".github" / "workflows" / "anyp5-ci.yml").write_text("on: workflow_dispatch\n")
        git(work, "add", ".")
        git(work, "commit", "-qm", "ci: any%5 merge queue")
        git(work, "push", "-q", "origin", "main")
        ci = FakeCI(self.tmp / "project.git")
        rows = self.queued(["alice", "bob", "carol"], ci=ci)
        self.assertEqual([r["state"] for r in rows], ["approved"] * 3)
        self.assertEqual(len(ci.started), 1)
        branch, candidate = ci.started[0]
        self.assertEqual(branch, "anyp5-queue")
        for n in ("alice", "bob", "carol"):
            self.assertIn(f"{n} feat: add {n}.c", self.log_of("anyp5-queue"))
            self.assertNotIn(f"add {n}.c", self.log_of())

        class Refused(Project):
            def publish_refs(self, refs):
                git(self.dir, "checkout", "-q", "-B", self.branch, f"origin/{self.branch}")
                return False
        rows = self.run_coordinator(ci=ci, project=Refused(str(self.tmp / "coordinator")))
        self.assertEqual([r["state"] for r in rows], ["approved"] * 3)
        self.assertNotIn("add alice.c", self.log_of())

        pushes = []

        class Counted(Project):
            def publish_refs(self, refs):
                pushes.append(refs)
                return super().publish_refs(refs)
        rows = self.run_coordinator(ci=ci, project=Counted(str(self.tmp / "coordinator")))
        self.assertEqual([r["state"] for r in rows], ["merged"] * 3)
        self.assertEqual(pushes, [{"main": candidate}])
        self.assertEqual(git(self.tmp / "project.git", "rev-parse", "main").strip(), candidate)
        self.assertEqual(len(ci.started), 1)
        status = json.loads((self.ledger / "status.json").read_text())
        self.assertEqual(status["queue"], {"candidate": None, "queued": 0})

    def test_red_batch_is_bisected_to_the_change_that_fails_alone(self):
        ci, config = FakeCI(self.tmp / "project.git"), {"ci": {"required": True}}
        self.queued(["alice", "bob", "carol", "dave"], broken=["carol"], config=config, ci=ci)
        for _ in range(8):
            rows = self.run_coordinator(config=config, ci=ci)
            if not any(r["state"] == "approved" for r in rows):
                break
        self.assertEqual([r["state"] for r in rows], ["merged", "merged", "ci_failed", "merged"])
        self.assertEqual(git(self.tmp / "project.git", "rev-list", "--count", ci.started[0][1]).strip(), "5")
        self.assertEqual(len(ci.started), 5)    # abcd red, ab green, cd red, c red alone, d green
        log = self.log_of()
        self.assertNotIn("add carol.c", log)
        for n in ("alice", "bob", "dave"):
            self.assertIn(f"{n} feat: add {n}.c", log)
        carol = self.box("carol")
        self.assertFalse(carol["banned"])
        self.assertEqual(carol["changes"][0]["state"], "ci_failed")
        self.assertIn("CI failed with this change alone", carol["changes"][0]["reasons"][0])
        self.assertEqual(self.state().execute("select patch from changes where author='carol'").fetchone(), (None,))

    def test_a_candidate_without_ci_result_times_out_and_is_retried(self):
        ci, config = FakeCI(self.tmp / "project.git"), {"ci": {"required": True, "timeout_minutes": 30, "batch": 1}}
        ci.hold = True
        self.queued(["alice", "bob"], config=config, ci=ci)
        self.assertEqual(len(ci.started), 1)
        self.run_coordinator(config=config, ci=ci, hours=0.1)
        self.assertEqual(len(ci.started), 1)
        self.run_coordinator(config=config, ci=ci)
        self.assertEqual(len(ci.started), 2)
        self.assertIn(("ci-retry",), self.state().execute("select what from events").fetchall())
        ci.hold = False
        rows = self.run_coordinator(config=config, ci=ci)
        self.assertEqual([r["state"] for r in rows], ["merged", "approved"])
        rows = self.run_coordinator(config=config, ci=ci)
        self.assertEqual([r["state"] for r in rows], ["merged", "merged"])
        self.assertEqual(len(ci.started), 3)

    def test_capability_board_and_member_task_proposals(self):
        self.post("maint", "task", title="capture shader dumps", requires=["game", "windows"])
        self.post("maint", "task", title="replay dumped shaders")
        self.post("bob", "task", title="split the recompiler pass")
        self.post("carol", "caps", caps=["windows", "game"])
        self.run_coordinator()
        titles = [t["title"] for t in json.loads((self.ledger / "tasks.json").read_text())]
        self.assertEqual(sorted(titles), ["capture shader dumps", "replay dumped shaders"])
        board = json.loads((self.ledger / "wanted.json").read_text())["capabilities"]
        self.assertIn({"requires": ["game", "windows"], "open": 1, "blocked": 0, "members": 1,
                       "tasks_per_member": 1.0}, board)
        self.assertIn("game + windows", (self.ledger / "wanted.md").read_text())
        drawn = [n for n in ("alice", "carol", "dave", "erin", "maint") if self.box(n)["reviews"]]
        self.assertEqual(len(drawn), 1)
        self.assertIn("question", self.box(drawn[0])["reviews"][0])

    def test_architects_decide_on_direction_through_their_box(self):
        architects = {"architects": ["carol"]}
        self.post("bob", "task", title="rewrite the recompiler in Rust")
        self.run_coordinator(config=architects)
        self.assertEqual([p["title"] for p in self.box("carol")["direction"]], ["rewrite the recompiler in Rust"])
        self.assertNotIn("direction", self.box("erin"))
        task = self.box("carol")["direction"][0]["task"]
        self.post("erin", "direct", task=task, decision="close", reason="not erin's call")
        self.post("carol", "direct", task=task, decision="close", reason="the recompiler stays C++ this year")
        self.run_coordinator(config=architects)
        self.assertEqual(self.box("bob")["proposals"], [{"task": task, "title": "rewrite the recompiler in Rust",
                                                         "state": "off-direction",
                                                         "reason": "the recompiler stays C++ this year"}])
        self.assertEqual(self.box("carol")["direction"], [])
        self.assertEqual(json.loads((self.ledger / "tasks.json").read_text()), [])

    def test_a_ban_is_logged_once(self):
        self.run_coordinator()
        c = Coordinator(self.ledger, self.keys["coord"], self.inbox, Project(str(self.tmp / "coordinator")))
        try:
            c.e.db.execute("insert into events(at, what, data) values(?,?,?)", (self.now, "ban", json.dumps({"login": "mallory"})))
            c.e.db.commit()
            for _ in range(3):
                self.now += H
                c.run(self.now)
        finally:
            c.close()
        log = (self.ledger / "log.jsonl").read_text().splitlines()
        self.assertEqual(sum('"ban"' in line for line in log), 1)

    def test_draw_seed_is_committed_then_revealed(self):
        import hashlib
        log = lambda: [json.loads(l) for l in (self.ledger / "log.jsonl").read_text().splitlines()]
        commit = [x for x in log() if x["event"] == "seed-commit"][0]
        self.run_coordinator(hours=24 * 7 * 6)
        reveal = [x for x in log() if x["event"] == "seed-reveal" and x["epoch"] == commit["epoch"]][0]
        self.assertEqual(hashlib.sha256(bytes.fromhex(reveal["seed"])).hexdigest(), commit["sha256"])



class GitHubInboxTest(unittest.TestCase):
    def test_reads_only_new_comments_of_every_inbox_issue(self):
        from anyp5.gitmode import GitHubInbox
        comments = [{"id": i, "issue_url": f"https://api.github.com/repos/o/r/issues/{1 if i < 150 else 7 if i % 5 else 3}",
                     "user": {"login": f"u{i}"}, "body": f"b{i}", "created_at": "2026-10-11T06:00:00Z"}
                    for i in range(1, 260)]
        comments += [{"id": i, "issue_url": "https://api.github.com/repos/o/r/issues/9", "user": {"login": "mallory"},
                      "body": "decoy", "created_at": "2026-10-11T06:00:00Z"} for i in range(260, 270)]
        calls = []

        def gh(*args):
            calls.append(args)
            if "search/issues" in args:
                bot = {"login": "github-actions[bot]"}
                return json.dumps({"items": [
                    {"number": 9, "title": "anyp5 inbox", "comments": 10, "state": "open", "user": {"login": "mallory"}},
                    {"number": 7, "title": "anyp5 inbox", "comments": 90, "state": "open", "user": bot},
                    {"number": 1, "title": "anyp5 inbox", "comments": 2000, "state": "closed", "user": bot},
                    {"number": 3, "title": "anyp5 inbox please", "comments": 1, "state": "open", "user": bot}]})
            page = int(args[1].split("page=")[-1])
            newest = sorted(comments, key=lambda c: -c["id"])
            return json.dumps(newest[(page - 1) * 100:page * 100])

        items = GitHubInbox("o/r", 1, gh=gh).fetch(after=120)
        ids = [i[0] for i in items]
        self.assertEqual(ids, [i for i in range(121, 260) if i < 150 or i % 5])
        self.assertEqual(sum(1 for c in calls if "search/issues" not in c), 2)


class GitHubCITest(unittest.TestCase):
    def test_reads_the_latest_actions_check_run_of_a_commit(self):
        from anyp5.gitmode import GitHubCI
        runs, calls = [], []

        def gh(*args):
            calls.append(args)
            return json.dumps({"check_runs": runs})
        ci = GitHubCI("o/r", gh=gh)
        self.assertEqual(ci.status("abc"), ("pending", None))
        runs.append({"id": 1, "status": "completed", "conclusion": "success", "html_url": "u1",
                     "app": {"slug": "someone-else"}})
        self.assertEqual(ci.status("abc"), ("pending", None))
        runs.append({"id": 2, "status": "completed", "conclusion": "failure", "html_url": "u2",
                     "app": {"slug": "github-actions"}})
        self.assertEqual(ci.status("abc"), ("failure", "u2"))
        runs.append({"id": 3, "status": "completed", "conclusion": "cancelled", "html_url": "u3",
                     "app": {"slug": "github-actions"}})
        self.assertEqual(ci.status("abc")[0], "error")
        self.assertIn("repos/o/r/commits/abc/check-runs?check_name=anyp5-ci&filter=latest", calls[0])
        ci.start("anyp5-queue", "abc")
        self.assertEqual(calls[-1], ("workflow", "run", "anyp5-ci.yml", "--repo", "o/r", "--ref", "anyp5-queue"))


if __name__ == "__main__":
    unittest.main()
