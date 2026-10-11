"""Benchmark of git mode on one repository: the real Coordinator, real git, real sealing.

One bare repository holds the project branch (main) and the ledger branch (anyp5-ledger), like a project that
keeps everything in one repo. Every 5 simulated minutes the bench does what the GitHub Action does: run one
coordinator pass, then commit and push the ledger. Simulated members post commands at the rates measured in
sim/eval/scaling.md (submits from merges/day, verdicts for every assignment, claims, heartbeats).

    python bench/gitmode_bench.py --members 1000 --hours 72 --out bench/results/n1000.json [--layout split]

--layout one:   everything on anyp5-ledger (v0.2.0 as published)
--layout split: state.sealed and boxes/ on an orphan branch anyp5-state that is force-pushed (no history)
"""
import argparse
import hashlib
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# --code DIR benchmarks another checkout of anyp5 (e.g. a worktree of the v0.2.0 tag) with the same driver.
CODE = sys.argv[sys.argv.index("--code") + 1] if "--code" in sys.argv else str(ROOT)
sys.path.insert(0, CODE)
# Windows: keep the bench out of %TEMP%, which the antivirus scans file by file.
tempfile.tempdir = os.environ.get("ANYP5_BENCH_TMP", str(ROOT.parent / "tmp" / "bench"))
os.makedirs(tempfile.tempdir, exist_ok=True)

from anyp5 import gitmode, seal  # noqa: E402

PASS = 300
FILES = 2000
HOT_FILES = 10
MERGES_PER_MEMBER_DAY = 0.237      # scaling.md: 2,372 merges/day at 10,000 members, linear
SUBMIT_FACTOR = 1.15               # some changes are rejected
ONLINE = 0.17
HEARTBEAT_HOURS = 4


def git(cwd, *args, stdin=None, check=True):
    proc = subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=cwd, input=stdin,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and proc.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout.strip()


def blob_id(text):
    data = text.encode()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def source(j):
    lines = [f"/* module {j} */", "#include <stddef.h>", ""]
    for k in range(20):
        lines += [f"int f{j}_{k}(const int *v, int n)", "{", "    int total = 0;",
                  f"    for (int i = 0; i < n; i++) total += v[i] * {k + 1};", "    return total;", "}", ""]
    return "\n".join(lines) + "\n"


class Inbox:
    """Hands each pass only the new comments, and counts what reading them costs on GitHub."""

    def __init__(self):
        self.items, self.next_id, self.total = [], 1, 0

    def post(self, login, body, at):
        self.items.append((self.next_id, login, body, at))
        self.next_id += 1
        self.total += 1

    def fetch(self, after):
        out = [i for i in self.items if i[0] > after]
        self.items = []
        return out


class Timer:
    def __init__(self):
        self.t = {}

    def add(self, key, dt):
        self.t[key] = self.t.get(key, 0.0) + dt

    def wrap(self, obj, name, key):
        fn = getattr(obj, name)

        def timed(*a, **k):
            t0 = time.perf_counter()
            try:
                return fn(*a, **k)
            finally:
                self.add(key, time.perf_counter() - t0)
        setattr(obj, name, timed)


def make_patch(work, author, rng, at):
    hot = rng.random() < 0.2
    j = rng.randrange(HOT_FILES) if hot else rng.randrange(FILES)
    path = f"src/m{j:04d}.c"
    old = (work / path).read_text()
    lines = old.split("\n")[:-1]
    k = rng.randrange(20)
    at_line = 3 + k * 7 + 4           # just before "return total;" of function k
    roll = rng.random()
    if roll < 0.03:
        added = ['    if (total < 0) system("true");']                    # red
    elif roll < 0.18:
        added = ["    if (n < 0) return 0;", "    memcpy(&total, v, 0);"]   # amber
    else:
        added = [f"    if (total < {rng.randrange(10, 99999)}) total += {rng.randrange(1, 9)};"]
    new_lines = lines[:at_line] + added + lines[at_line:]
    new = "\n".join(new_lines) + "\n"
    ctx_before = lines[at_line - 3:at_line]
    ctx_after = lines[at_line:at_line + 3]
    hunk = [f"@@ -{at_line - 2},{len(ctx_before) + len(ctx_after)} +{at_line - 2},"
            f"{len(ctx_before) + len(ctx_after) + len(added)} @@"]
    hunk += [" " + l for l in ctx_before] + ["+" + l for l in added] + [" " + l for l in ctx_after]
    date = time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime(at))
    patch = "\n".join([
        "From 0000000000000000000000000000000000000000 Mon Sep 17 00:00:00 2001",
        f"From: {author} <{author}@example.org>",
        f"Date: {date}",
        f"Subject: [PATCH] fix: guard in f{j}_{k} ({rng.randrange(1 << 30):x})",
        "",
        "---",
        f"diff --git a/{path} b/{path}",
        f"index {blob_id(old)}..{blob_id(new)} 100644",
        f"--- a/{path}",
        f"+++ b/{path}",
        *hunk,
        "-- ",
        "2.47.1",
        "",
    ])
    return patch


def rmtree(path):
    """Git marks its objects read-only, which stops shutil.rmtree on Windows."""
    def unlock(fn, target, _):
        os.chmod(target, 0o700)
        fn(target)
    shutil.rmtree(path, onerror=unlock) if Path(path).exists() else None


def disk_usage(bare, ref):
    out = git(bare, "rev-list", "--disk-usage", "--objects", ref, check=False)
    return int(out) if out.isdigit() else 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--members", type=int, required=True)
    p.add_argument("--hours", type=float, default=24)
    p.add_argument("--layout", choices=["one", "split"], default="one")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--out", required=True)
    p.add_argument("--keep", action="store_true")
    p.add_argument("--code", default=str(ROOT))
    a = p.parse_args()

    rng = random.Random(a.seed)
    tmp = Path(tempfile.mkdtemp(prefix="anyp5-bench-"))
    os.environ.update(GIT_COMMITTER_NAME="anyp5", GIT_COMMITTER_EMAIL="anyp5@example.org",
                      GIT_AUTHOR_NAME="anyp5", GIT_AUTHOR_EMAIL="anyp5@example.org")
    result = {"members": a.members, "hours": a.hours, "layout": a.layout, "passes": []}
    try:
        # ---------------------------------------------------------------- one repo: main + ledger branches
        seed_dir = tmp / "seed"
        (seed_dir / "src").mkdir(parents=True)
        for j in range(FILES):
            (seed_dir / "src" / f"m{j:04d}.c").write_text(source(j), newline="\n")
        git(seed_dir, "init", "-q", "-b", "main")
        git(seed_dir, "add", ".")
        git(seed_dir, "commit", "-q", "-m", "init")
        git(tmp, "clone", "-q", "--bare", str(seed_dir), "repo.git")
        bare = tmp / "repo.git"
        for d in ("project", "author", "ledger"):
            git(tmp, "clone", "-q", "repo.git", d)
            git(tmp / d, "config", "core.autocrlf", "false")
        ledger = tmp / "ledger"
        git(ledger, "checkout", "-q", "--orphan", "anyp5-ledger")
        git(ledger, "rm", "-rq", "--cached", ".")
        git(ledger, "clean", "-fdq")
        state_dir = None
        if a.layout == "split":
            git(tmp, "clone", "-q", "repo.git", "state")
            state_dir = tmp / "state"
            git(state_dir, "checkout", "-q", "--orphan", "anyp5-state")
            git(state_dir, "rm", "-rq", "--cached", ".")
            git(state_dir, "clean", "-fdq")

        coord_key = seal.keygen()
        gitmode.init_ledger(ledger, coord_key, ["maint"], "bench/project", issue=1)
        if a.layout == "split":
            (ledger / ".gitignore").write_text("state.sealed\nboxes/\n")
        elif (ledger / ".gitignore").exists():
            (ledger / ".gitignore").unlink()
        result["code"] = CODE
        coord_box = seal.public(coord_key)["box"]

        t0 = time.perf_counter()
        logins = ["maint"] + [f"m{i:06d}" for i in range(a.members)]
        keys = {login: seal.keygen() for login in logins}
        start = 1_800_000_000.0
        setup = gitmode.Coordinator(ledger, coord_key, Inbox(), gitmode.Project(str(tmp / "project")))
        db = setup.e.db
        setup.e.db = type("NoCommit", (), {"__getattr__": lambda self, k: getattr(db, k), "commit": lambda self: None})()
        for login in logins:                       # one transaction instead of one per member
            setup.add_member(login, seal.public(keys[login]), None, start)
        setup.e.db = db
        db.commit()
        setup.write(start)
        setup.close()
        result["setup_seconds"] = time.perf_counter() - t0

        inbox = Inbox()
        submits_per_pass = a.members * MERGES_PER_MEMBER_DAY * SUBMIT_FACTOR * PASS / 86400
        heartbeats_per_pass = a.members * ONLINE * PASS / (HEARTBEAT_HOURS * 3600)
        due = []               # (time, login, assignment, kind)
        open_tasks = []
        seen_assignment = 0
        online = []
        counters = {"commands": 0, "submits": 0}
        passes = int(a.hours * 3600 / PASS)
        totals = {}

        def post(login, cmd, at, **args):
            body = seal.seal(coord_box, gitmode.command(keys[login], login, cmd, **args).encode())
            inbox.post(login, body, at)
            counters["commands"] += 1

        for n in range(passes):
            now = start + (n + 1) * PASS
            if n % 12 == 0:
                online = rng.sample(logins[1:], max(1, int(ONLINE * a.members)))
            # ------------------------------------------------------------ what members do in these 5 minutes
            gen0 = time.perf_counter()
            if n % 6 == 0 or n == 0:
                git(tmp / "author", "fetch", "-q", "origin", "main")
                git(tmp / "author", "checkout", "-q", "-f", "--detach", "origin/main")
            k = int(submits_per_pass) + (1 if rng.random() < submits_per_pass - int(submits_per_pass) else 0)
            for _ in range(k):
                author = rng.choice(online)
                at = now - rng.random() * PASS
                post("maint", "task", at, title=f"task {counters['submits']}")
                if open_tasks:
                    task = open_tasks.pop()
                    post(author, "claim", at, task=task)
                    post(author, "handover", at, task=task, branch=f"task/{task}")
                post(author, "submit", at, patch=make_patch(tmp / "author", author, rng, at))
                counters["submits"] += 1
            hb = int(heartbeats_per_pass) + (1 if rng.random() < heartbeats_per_pass % 1 else 0)
            for login in rng.sample(online, min(hb, len(online))):
                post(login, "alive", now - rng.random() * PASS)
            due.sort()
            while due and due[0][0] <= now:
                at, login, assignment, kind = due.pop(0)
                if kind == "canary":
                    verdict = "reject" if rng.random() < 0.6 else "approve"
                else:
                    verdict = "approve" if rng.random() < 0.9 else "reject"
                post(login, "verdict", at, assignment=assignment, verdict=verdict,
                     reason="" if verdict == "approve" else "bound looks wrong")
            gen = time.perf_counter() - gen0

            # ------------------------------------------------------------ one coordinator pass (the Action)
            timer = Timer()
            git_calls = {"n": 0, "t": 0.0}
            real_run = gitmode.subprocess.run

            def counted(*args, **kw):
                t1 = time.perf_counter()
                try:
                    return real_run(*args, **kw)
                finally:
                    git_calls["n"] += 1
                    git_calls["t"] += time.perf_counter() - t1
            gitmode.subprocess.run = counted
            real_seal = gitmode.seal.seal

            def timed_seal(box_pub, data):
                t1 = time.perf_counter()
                try:
                    return real_seal(box_pub, data)
                finally:
                    timer.add("seal_state" if box_pub == coord_box else "seal_boxes", time.perf_counter() - t1)
            gitmode.seal.seal = timed_seal
            if state_dir is not None:
                for name in ("state.sealed",):
                    if (state_dir / name).exists():
                        shutil.copy2(state_dir / name, ledger / name)
                if (state_dir / "boxes").exists():
                    shutil.rmtree(ledger / "boxes", ignore_errors=True)
                    shutil.copytree(state_dir / "boxes", ledger / "boxes")
            n_cmds = len(inbox.items)
            p0 = time.perf_counter()
            c = gitmode.Coordinator(ledger, coord_key, inbox, gitmode.Project(str(tmp / "project")))
            timer.add("init_unseal", time.perf_counter() - p0)
            timer.wrap(c, "handle", "inbox")
            timer.wrap(c.e, "tick", "tick")
            timer.wrap(c, "forge", "forge")
            timer.wrap(c, "write", "write")
            timer.wrap(c, "view", "views")
            c.run(now)
            for r in c.e.q("select a.id, a.reviewer, a.drawn_at, c.kind from assignments a "
                           "join changes c on c.id=a.change_id where a.id>? and a.verdict is null", seen_assignment):
                seen_assignment = max(seen_assignment, r["id"])
                delay = min(rng.expovariate(1 / 7200), 20 * 3600)
                due.append((now + delay, r["reviewer"], r["id"], r["kind"]))
            open_tasks = [r["id"] for r in c.e.q("select id from tasks where state='open' order by id desc limit 200")]
            stats = {r["state"]: r["n"] for r in c.e.q("select state, count(*) n from changes group by state")}
            state_size = (ledger / "state.sealed").stat().st_size
            c.close()
            gitmode.subprocess.run = real_run
            gitmode.seal.seal = real_seal
            run_time = time.perf_counter() - p0

            # ------------------------------------------------------------ commit and push like the Action
            c0 = time.perf_counter()
            if state_dir is not None:
                shutil.copy2(ledger / "state.sealed", state_dir / "state.sealed")
                shutil.rmtree(state_dir / "boxes", ignore_errors=True)
                shutil.copytree(ledger / "boxes", state_dir / "boxes")
                git(state_dir, "add", "-A")
                git(state_dir, "commit", "-q", "--amend", "-m", "anyp5 state", check=False) \
                    if git(state_dir, "rev-parse", "-q", "--verify", "HEAD", check=False) else \
                    git(state_dir, "commit", "-q", "-m", "anyp5 state")
                git(state_dir, "push", "-q", "-f", "origin", "HEAD:anyp5-state")
            git(ledger, "add", "-A")
            if git(ledger, "status", "--porcelain"):
                git(ledger, "commit", "-q", "-m", "anyp5: coordinator pass")
                git(ledger, "push", "-q", "origin", "HEAD:anyp5-ledger")
            commit_time = time.perf_counter() - c0

            row = {"pass": n, "hours": (n + 1) * PASS / 3600, "commands": n_cmds, "run_s": run_time,
                   "commit_s": commit_time, "gen_s": gen, "git_calls": git_calls["n"], "git_s": git_calls["t"],
                   "state_bytes": state_size, "changes": stats, **{k: round(v, 4) for k, v in timer.t.items()}}
            result["passes"].append(row)
            for key, v in row.items():
                if isinstance(v, (int, float)):
                    totals[key] = totals.get(key, 0) + v
            if (n + 1) % 288 == 0 or n + 1 == passes:
                t1 = time.perf_counter()
                git(bare, "gc", "-q", "--prune=now")
                size = {
                    "hours": (n + 1) * PASS / 3600,
                    "pack_bytes": int(next(l.split()[1] for l in git(bare, "count-objects", "-v").splitlines()
                                           if l.startswith("size-pack"))) * 1024,
                    "main_bytes": disk_usage(bare, "main"),
                    "ledger_bytes": disk_usage(bare, "anyp5-ledger"),
                    "state_branch_bytes": disk_usage(bare, "anyp5-state") if state_dir else 0,
                    "main_commits": int(git(bare, "rev-list", "--count", "main")),
                    "ledger_commits": int(git(bare, "rev-list", "--count", "anyp5-ledger")),
                    "gc_s": time.perf_counter() - t1,
                    "inbox_comments": inbox.total,
                }
                c1 = time.perf_counter()
                git(tmp, "clone", "-q", "--bare", "--no-local", "repo.git", "probe.git")
                size["full_clone_s"] = time.perf_counter() - c1
                rmtree(tmp / "probe.git")
                boxes = list((state_dir or ledger).glob("boxes/*.sealed"))
                size["boxes"] = len(boxes)
                size["boxes_bytes"] = sum(b.stat().st_size for b in boxes)
                result.setdefault("sizes", []).append(size)
                print(json.dumps(size), flush=True)
            if (n + 1) % 12 == 0:
                last = result["passes"][-12:]
                print(f"h={row['hours']:.0f} cmds/pass={sum(r['commands'] for r in last) / 12:.0f} "
                      f"run={sum(r['run_s'] for r in last) / 12:.2f}s commit={sum(r['commit_s'] for r in last) / 12:.2f}s "
                      f"state={state_size / 1e6:.2f}MB changes={stats}", flush=True)
        result["totals"] = totals
        result["commands"] = counters["commands"]
        result["submits"] = counters["submits"]
    finally:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(result))
        if not a.keep:
            rmtree(tmp)


if __name__ == "__main__":
    main()
