import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from anyp5 import __version__, seal
from anyp5.install import Installer, SetupError, templates_dir

ROOT = Path(__file__).resolve().parent.parent
SHA = "0123456789abcdef0123456789abcdef01234567"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


class FakeGh:
    """Records every gh call (argv and stdin) and answers like GitHub would."""

    def __init__(self, permission="ADMIN", inbox=(), tag="commit"):
        self.calls, self.permission, self.inbox, self.tag = [], permission, list(inbox), tag

    def __call__(self, *args, stdin=None):
        self.calls.append((args, stdin))
        if args[:2] == ("repo", "view"):
            return json.dumps({"nameWithOwner": "owner/proj", "defaultBranchRef": {"name": "main"},
                               "viewerPermission": self.permission})
        if args[:2] == ("api", "user"):
            return "maint\n"
        if "search/issues" in args:
            return json.dumps({"items": self.inbox})
        if args[:2] == ("issue", "create"):
            return "https://github.com/owner/proj/issues/7\n"
        if args[:2] == ("secret", "set"):
            return ""
        if args[0] == "api" and "/git/ref/tags/" in args[1]:
            if self.tag is None:
                raise RuntimeError("gh: Not Found (HTTP 404)")
            obj = {"type": self.tag, "sha": SHA if self.tag == "commit" else "f" * 40}
            return json.dumps({"ref": "refs/tags/v" + __version__, "object": obj})
        if args[0] == "api" and "/git/tags/" in args[1]:
            return json.dumps({"object": {"type": "commit", "sha": SHA}})
        raise AssertionError(f"unexpected gh call {args}")

    def made(self, *prefix):
        return [c for c in self.calls if c[0][:len(prefix)] == prefix]


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anyp5-setup-"))
        git(self.tmp, "init", "-q", "--bare", "-b", "main", "origin.git")
        self.work = self.tmp / "work"
        git(self.tmp, "clone", "-q", "origin.git", "work")
        for k, v in (("user.name", "maint"), ("user.email", "maint@example.org")):
            git(self.work, "config", k, v)
        git(self.work, "symbolic-ref", "HEAD", "refs/heads/main")
        (self.work / "README.md").write_text("# proj\n")
        (self.work / "notes.txt").write_text("work in progress\n")
        git(self.work, "add", "README.md")
        git(self.work, "commit", "-q", "-m", "init")
        git(self.work, "push", "-q", "origin", "main")
        self.out = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def installer(self, gh, dry_run=False):
        return Installer(self.work, gh=gh, dry_run=dry_run, out=self.out.append)

    def remote_branches(self):
        return git(self.tmp / "origin.git", "branch", "--format=%(refname:short)").split()

    def test_dry_run_changes_nothing(self):
        gh = FakeGh()
        head = git(self.work, "rev-parse", "HEAD")
        status = git(self.work, "status", "--porcelain")
        self.installer(gh, dry_run=True).setup(test="pytest -q", push=True)
        self.assertEqual(gh.made("issue", "create"), [])
        self.assertEqual(gh.made("secret", "set"), [])
        self.assertEqual(self.remote_branches(), ["main"])
        self.assertEqual(git(self.work, "rev-parse", "HEAD"), head)
        self.assertEqual(git(self.work, "status", "--porcelain"), status)
        self.assertFalse((self.work / ".github").exists())
        printed = "\n".join(self.out)
        for step in ("would run: gh issue create", "would run: gh secret set ANYP5_COORDINATOR_KEY",
                     "would push", "would write .github/workflows/anyp5-ci.yml", "would append to README.md",
                     "would run: git commit", "would run: git push origin HEAD:main"):
            self.assertIn(step, printed)

    def test_full_setup(self):
        gh = FakeGh()
        result = self.installer(gh).setup(test="make check\npython -m pytest -q", architects=["arch"])
        self.assertEqual(result["issue"], 7)
        self.assertEqual(len(gh.made("issue", "create")), 1)
        (secret_args, stdin), = gh.made("secret", "set")
        key = json.loads(stdin)
        self.assertNotIn(stdin, " ".join(secret_args))
        self.assertNotIn(stdin, "\n".join(self.out))

        self.assertIn("anyp5-ledger", self.remote_branches())
        files = git(self.tmp / "origin.git", "ls-tree", "--name-only", "anyp5-ledger").split()
        self.assertEqual(sorted(files), [".gitignore", "anyp5.json"])
        meta = json.loads(git(self.tmp / "origin.git", "show", "anyp5-ledger:anyp5.json"))
        self.assertEqual((meta["project"], meta["inbox_issue"], meta["admins"]), ("owner/proj", 7, ["maint"]))
        self.assertEqual(meta["config"]["architects"], ["arch"])
        self.assertEqual(meta["coordinator"], seal.public(key))
        self.assertEqual(git(self.tmp / "origin.git", "rev-list", "--count", "anyp5-ledger"), "1")
        raw = subprocess.run(["git", "show", "anyp5-ledger:.gitignore"], cwd=self.tmp / "origin.git",
                             capture_output=True, check=True).stdout
        self.assertEqual(raw, b"state.sealed\nboxes/\n")

        wf = self.work / ".github" / "workflows"
        for name in ("anyp5-coordinator.yml", "anyp5-wanted.yml"):
            text = (wf / name).read_text()
            self.assertIn(f"anyPercent5@{SHA}", text)
            self.assertNotIn("anyPercent5@v", text)
        ci = (wf / "anyp5-ci.yml").read_text()
        self.assertIn("        run: |\n          make check\n          python -m pytest -q\n", ci)
        self.assertNotIn("ci/test.sh", ci)
        readme = (self.work / "README.md").read_text()
        self.assertTrue(readme.startswith("# proj\n"))
        self.assertIn("<!-- anyp5:wanted -->\n<!-- /anyp5:wanted -->", readme)

        self.assertEqual(git(self.work, "rev-parse", "--abbrev-ref", "HEAD"), "main")
        self.assertEqual(git(self.work, "log", "-1", "--format=%s"), "ci: run this repository on any%5")
        self.assertEqual(git(self.work, "status", "--porcelain"), "?? notes.txt")
        self.assertNotEqual(git(self.work, "rev-parse", "HEAD"), git(self.tmp / "origin.git", "rev-parse", "main"))

    def test_push_moves_the_project_branch(self):
        self.installer(FakeGh()).setup(push=True)
        self.assertEqual(git(self.work, "rev-parse", "HEAD"), git(self.tmp / "origin.git", "rev-parse", "main"))
        self.assertFalse((self.work / ".github" / "workflows" / "anyp5-ci.yml").exists())

    def test_refuses_when_the_ledger_exists(self):
        self.installer(FakeGh()).setup()
        gh = FakeGh()
        with self.assertRaisesRegex(SetupError, "already exists"):
            self.installer(gh).setup()
        self.assertEqual(gh.made("secret", "set"), [])
        self.assertEqual(gh.made("issue", "create"), [])

    def test_force_new_key_replaces_the_ledger_and_drops_the_state(self):
        first = FakeGh()
        self.installer(first).setup()
        git(self.work, "push", "-q", "origin", "main:anyp5-state")
        gh = FakeGh(inbox=[{"number": 7, "comments": 3, "state": "open", "title": "anyp5 inbox",
                            "user": {"login": "maint"}}])
        self.installer(gh).setup(force_new_key=True)
        self.assertEqual(gh.made("issue", "create"), [])
        key = json.loads(gh.made("secret", "set")[0][1])
        meta = json.loads(git(self.tmp / "origin.git", "show", "anyp5-ledger:anyp5.json"))
        self.assertEqual(meta["coordinator"], seal.public(key))
        self.assertNotIn("anyp5-state", self.remote_branches())

    def test_reuses_an_inbox_opened_by_the_admin_only(self):
        gh = FakeGh(inbox=[{"number": 9, "comments": 0, "state": "open", "title": "anyp5 inbox",
                            "user": {"login": "stranger"}},
                           {"number": 4, "comments": 0, "state": "open", "title": "anyp5 inbox",
                            "user": {"login": "maint"}}])
        self.assertEqual(self.installer(gh).setup()["issue"], 4)

    def test_refuses_without_admin_rights_and_off_the_project_branch(self):
        with self.assertRaisesRegex(SetupError, "ADMIN"):
            self.installer(FakeGh(permission="WRITE")).setup()
        git(self.work, "checkout", "-q", "-b", "topic")
        with self.assertRaisesRegex(SetupError, "check out main"):
            self.installer(FakeGh()).setup()
        self.assertEqual(self.remote_branches(), ["main"])

    def test_annotated_tag_and_unresolved_tag(self):
        self.assertEqual(self.installer(FakeGh(tag="tag")).release_ref(__version__), SHA)
        self.assertEqual(self.installer(FakeGh(tag=None)).release_ref(__version__), "v" + __version__)
        self.assertIn("warning", self.out[-1])


class TemplatesTest(unittest.TestCase):
    def test_templates_have_one_source(self):
        self.assertEqual(templates_dir().resolve(), (ROOT / "templates").resolve())
        for name in ("anyp5-coordinator.yml", "anyp5-wanted.yml", "anyp5-ci.yml"):
            self.assertTrue((templates_dir() / name).is_file())

    def test_pyproject_ships_the_templates(self):
        text = (ROOT / "pyproject.toml").read_text()
        self.assertIn('"anyp5.templates" = "templates"', text)
        self.assertIn('"anyp5.templates" = ["*.yml"', text)


if __name__ == "__main__":
    unittest.main()
