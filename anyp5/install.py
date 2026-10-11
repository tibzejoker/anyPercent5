"""`anyp5 setup`: install git mode on a project from its own checkout, in one command.

What it does, in order (docs/GITMODE.md lists the same steps done by hand):
1. checks the checkout: on the project branch, `gh` logged in with admin rights, no `anyp5-ledger` branch yet;
2. reuses the open "anyp5 inbox" issue opened by an admin, or creates one;
3. generates the coordinator key and pipes it to `gh secret set ANYP5_COORDINATOR_KEY` (stdin, never argv,
   never printed, never written to disk);
4. builds the `anyp5-ledger` branch (anyp5.json, .gitignore) with git plumbing in the object store and pushes
   it to origin, so the working tree, the index and the current branch are never touched;
5. writes the coordinator, wanted-board and (with `--test`) merge-queue workflows, with `pip install` pinned to
   the commit of the installed anyp5 release, and the wanted-board markers in README.md;
6. commits those files on the project branch, and pushes only with `--push`.

Every external command goes through `Installer.run`, so `--dry-run` prints them all and changes nothing, and
tests replace `gh` with a fake.
"""
import json
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

from . import __version__, seal
from .gitmode import INBOX_TITLE, init_ledger, inbox_issues

UPSTREAM = "tibzejoker/anyPercent5"
LEDGER = "anyp5-ledger"
STATE = "anyp5-state"
SECRET = "ANYP5_COORDINATOR_KEY"
WORKFLOWS = ".github/workflows"
MARKERS = "<!-- anyp5:wanted -->\n<!-- /anyp5:wanted -->\n"
PIN = re.compile(r"(git\+https://github\.com/tibzejoker/anyPercent5)@[^\"'\s]+")


class SetupError(Exception):
    pass


def templates_dir():
    """The workflow templates: anyp5/templates/ once installed (packaged from the repository's templates/
    directory, see pyproject.toml), templates/ in a source checkout."""
    here = Path(__file__).resolve().parent
    for candidate in (here / "templates", here.parent / "templates"):
        if (candidate / "anyp5-coordinator.yml").is_file():
            return candidate
    raise SetupError(f"workflow templates not found next to {here}")


def template(name):
    return (templates_dir() / name).read_text(encoding="utf-8")


def pin(text, ref):
    return PIN.sub(lambda m: f"{m.group(1)}@{ref}", text)


def ci_workflow(text, test):
    lines = "\n".join("          " + line for line in test.strip().splitlines())
    return text.replace("        run: ./ci/test.sh\n", "        run: |\n" + lines + "\n")


def default_gh(*args, stdin=None, check=True):
    proc = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    if check and proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


class Installer:
    def __init__(self, cwd=".", gh=None, dry_run=False, out=print):
        self.cwd, self.gh_call, self.dry_run, self.out = Path(cwd), gh or default_gh, dry_run, out

    def say(self, text):
        self.out(text)

    def run(self, tool, *args, stdin=None, change=False, check=True, shown=None):
        """Run a command. Commands that change something are only printed in a dry run. `shown` replaces the
        printed stdin, so a secret never reaches the output."""
        line = " ".join(shlex.quote(a) for a in (tool, *args))
        if shown:
            line += f"  < {shown}"
        if change:
            self.say(("would run: " if self.dry_run else "+ ") + line)
            if self.dry_run:
                return ""
        if tool == "gh":
            return self.gh_call(*args, stdin=stdin) if stdin is not None else self.gh_call(*args)
        proc = subprocess.run([tool, *args], cwd=self.cwd, input=stdin, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        if check and proc.returncode != 0:
            raise SetupError(f"{line}: {proc.stderr.strip()}")
        return proc.stdout.strip() if check else proc

    def git(self, *args, **kw):
        return self.run("git", *args, **kw)

    def gh(self, *args, **kw):
        return self.run("gh", *args, **kw)

    def write(self, rel, text, append=False):
        path = self.cwd / rel
        self.say(("would " if self.dry_run else "+ ") + ("append to " if append else "write ") + rel)
        if not self.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a" if append else "w", encoding="utf-8", newline="\n") as f:
                f.write(text)

    def release_ref(self, version):
        """The commit of tag v<version> on the any%5 repository: a tag can be moved, a commit hash cannot."""
        tag = f"v{version}"
        try:
            ref = json.loads(self.gh("api", f"repos/{UPSTREAM}/git/ref/tags/{tag}"))["object"]
            if ref["type"] == "tag":
                ref = json.loads(self.gh("api", f"repos/{UPSTREAM}/git/tags/{ref['sha']}"))["object"]
            return ref["sha"]
        except (RuntimeError, KeyError, ValueError, TypeError) as error:
            self.say(f"warning: could not resolve {tag} to a commit ({error}); the workflows are pinned to the "
                     f"tag {tag}. Replace it with the release commit hash before relying on them.")
            return tag

    def ledger_commit(self, meta_args):
        """Build the ledger's first commit in the object store: no checkout, no index, no working tree change."""
        with tempfile.TemporaryDirectory(prefix="anyp5-ledger-") as tmp:
            init_ledger(tmp, *meta_args)
            entries = []
            for path in sorted(Path(tmp).iterdir()):
                path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
                blob = self.git("hash-object", "-w", str(path))
                entries.append(f"100644 blob {blob}\t{path.name}\0")
            tree = self.git("mktree", "-z", stdin="".join(entries))
            return self.git("commit-tree", tree, "-m", "anyp5 ledger")

    def setup(self, admins=(), test=None, push=False, architects=(), force_new_key=False, repo=None, branch=None):
        say = self.say
        top = self.git("rev-parse", "--show-toplevel")
        self.cwd = Path(top)
        view = json.loads(self.gh("repo", "view", *([repo] if repo else []),
                                  "--json", "nameWithOwner,defaultBranchRef,viewerPermission"))
        repo = view["nameWithOwner"]
        branch = branch or view["defaultBranchRef"]["name"]
        if view.get("viewerPermission") != "ADMIN":
            message = f"your gh login has {view.get('viewerPermission')} rights on {repo}; setup needs ADMIN " \
                      f"(to set the secret)"
            if not self.dry_run:
                raise SetupError(message)
            say("warning: " + message)
        current = self.git("rev-parse", "--abbrev-ref", "HEAD")
        if current != branch:
            raise SetupError(f"check out {branch} first (now on {current}): the workflows are committed there")
        admins = list(admins) or [self.gh("api", "user", "--jq", ".login").strip()]
        say(f"any%5 setup for {repo} (branch {branch}, admins {', '.join(admins)})")

        exists = self.git("ls-remote", "--exit-code", "origin", f"refs/heads/{LEDGER}", check=False)
        if exists.returncode == 0:
            if not force_new_key:
                raise SetupError(f"{LEDGER} already exists on origin: any%5 is already set up here. "
                                 f"--force-new-key starts over with a new key, a new ledger and no members.")
            say(f"warning: --force-new-key: {LEDGER} is replaced and {STATE} deleted (it is sealed to the old "
                f"key). Members, tasks and calibration start from zero.")
        elif exists.returncode != 2:
            raise SetupError(f"cannot read origin: {exists.stderr.strip()}")

        issues = [n for n, _, is_open in inbox_issues(repo, gh=self.gh, trusted=admins) if is_open]
        if issues:
            issue = issues[0]
            say(f"inbox: reusing issue #{issue}")
        else:
            url = self.gh("issue", "create", "--repo", repo, "--title", INBOX_TITLE, "--body",
                          "Sealed any%5 commands. Post them with the anyp5 CLI.", change=True)
            issue = int(url.strip().rstrip("/").rsplit("/", 1)[-1]) if url.strip() else None
            say(f"inbox: created issue #{issue}" if issue else "inbox: the new issue's number goes in anyp5.json")

        key = seal.keygen()
        config = {"architects": list(architects)} if architects else None
        self.gh("secret", "set", SECRET, "--repo", repo, stdin=json.dumps(key), change=True,
                shown="the new coordinator key (stdin)")
        if self.dry_run:
            say(f"would push: a new orphan {LEDGER} branch holding anyp5.json and .gitignore")
        else:
            commit = self.ledger_commit((key, admins, repo, branch, issue, config))
            self.git("push", *(["--force"] if force_new_key else []), "origin", f"{commit}:refs/heads/{LEDGER}",
                     change=True)
        if force_new_key and exists.returncode == 0:
            state = self.git("ls-remote", "--exit-code", "origin", f"refs/heads/{STATE}", check=False)
            if state.returncode == 0:
                self.git("push", "origin", "--delete", STATE, change=True)

        ref = self.release_ref(__version__)
        files = {f"{WORKFLOWS}/anyp5-coordinator.yml": pin(template("anyp5-coordinator.yml"), ref),
                 f"{WORKFLOWS}/anyp5-wanted.yml": pin(template("anyp5-wanted.yml"), ref)}
        if test:
            files[f"{WORKFLOWS}/anyp5-ci.yml"] = ci_workflow(template("anyp5-ci.yml"), test)
        changed = []
        for rel, text in files.items():
            if (self.cwd / rel).exists():
                say(f"kept the existing {rel}")
            else:
                self.write(rel, text)
                changed.append(rel)
        readme = self.cwd / "README.md"
        text = readme.read_text(encoding="utf-8") if readme.exists() else ""
        if "<!-- anyp5:wanted -->" not in text:
            sep = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
            self.write("README.md", sep + "## Where to start\n\n" + MARKERS, append=True)
            changed.append("README.md")

        if changed:
            self.git("add", "--", *changed, change=True)
            self.git("commit", "-q", "-m", "ci: run this repository on any%5", "--", *changed, change=True)
        if push:
            try:
                self.git("push", "origin", f"HEAD:{branch}", change=True)
            except SetupError as error:
                lines = str(error).strip().splitlines()
                say("push refused: " + next((l for l in lines if "rejected" in l), lines[-1]).strip())
                say("Everything else is in place and committed locally. GitHub only lets a token change workflow "
                    "files with the `workflow` scope: run `gh auth refresh -h github.com -s workflow`, then "
                    f"`git push origin {branch}` (or push over SSH, which needs no scope).")
                push = False
        say("")
        say("done (dry run: nothing was changed)" if self.dry_run else "done")
        say(next_steps(repo, branch, pushed=push, has_ci=bool(test)))
        return {"repo": repo, "branch": branch, "issue": issue, "ref": ref, "files": changed}


def next_steps(repo, branch, pushed, has_ci):
    steps = []
    if not pushed:
        steps.append(f"Review the commit, then push it: git push origin {branch} "
                     f"(gh needs the `workflow` scope: gh auth refresh -s workflow)")
    if has_ci:
        steps.append("Add the project's own setup steps (toolchain, dependencies) before the Test step of "
                     f"{WORKFLOWS}/anyp5-ci.yml if it needs any.")
    else:
        steps.append("No --test given: approved changes go straight to main. For a merge queue, copy "
                     f"templates/anyp5-ci.yml to {WORKFLOWS}/ and set its test command.")
    steps += [f"If {branch} is protected, give the coordinator a bot token that can push to it.",
              f"Join as a member yourself: anyp5 join --repo {repo}",
              'Open starter tasks: anyp5 task "title" --requires linux',
              f"Invite contributors: pip install \"anyp5 @ git+https://github.com/{UPSTREAM}@v{__version__}\", "
              f"then anyp5 join --repo {repo}. Agents use skills/contributor and skills/reviewer.",
              "Optional: name architects in anyp5.json (config.architects) and add project canaries "
              "(docs/GITMODE.md#project-canaries)."]
    return "Next steps:\n" + "\n".join(f"  {i}. {s}" for i, s in enumerate(steps, 1))
