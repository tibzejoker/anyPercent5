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
        body = command(key or self.keys[name], name, cmd, **args)
        self.inbox.post(author or name, seal.seal(meta["coordinator"]["box"], body.encode()), self.now)

    def run_coordinator(self, config=None, hours=1):
        self.now += hours * H
        c = Coordinator(self.ledger, self.keys["coord"], self.inbox,
                        Project(str(self.tmp / "coordinator")), config=config)
        try:
            c.run(self.now)
        finally:
            c.close()

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
        self.assertIn("t fix: bound check in put", log)
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
        old = command(self.keys["bob"], "bob", "task", title="again")
        self.inbox.post("bob", seal.seal(meta["coordinator"]["box"], old.encode()), self.now)
        self.inbox.post("bob", seal.seal(meta["coordinator"]["box"], old.encode()), self.now)
        self.run_coordinator()
        tasks = json.loads((self.ledger / "tasks.json").read_text())
        self.assertEqual([t["title"] for t in tasks], ["again"])

    def test_pending_canary_goes_to_someone_who_never_sees_the_original(self):
        self.post("alice", "submit", patch=self.patch())
        self.run_coordinator(config={"canary_every": 0.5, "canary_source": "pending"})
        drawn = self.reviewers()
        self.assertEqual(len(drawn), 2)
        self.assertNotIn("alice", drawn)
        diffs = [self.box(n)["reviews"][0]["diff"] for n in drawn]
        self.assertNotEqual(diffs[0], diffs[1])
        for n in drawn:
            self.assertEqual(set(self.box(n)["reviews"][0]), {"assignment", "diff"})
        for n in drawn:
            job = self.box(n)["reviews"][0]
            self.post(n, "verdict", assignment=job["assignment"], verdict="reject", reason="looks wrong")
        self.run_coordinator(config={"canary_every": 1e9, "canary_source": "pending"})
        calibration = {n: self.box(n)["calibration"] for n in drawn}
        self.assertEqual(sorted(calibration.values()), [[0, 0], [1, 1]])
        self.assertEqual(self.box("alice")["changes"][0]["state"], "rejected")


if __name__ == "__main__":
    unittest.main()
