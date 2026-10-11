"""Evidence bundles for agent-written pull requests.

collect: for a branch, build the PR's tests twice (on the base with only the test files taken from the
branch, and on the branch itself), run them, classify the diff by risk and write evidence.json + logs.
verify:  re-run the same collection independently and compare it with a submitted evidence.json.

The point: a maintainer trusts results their own machine reproduced, not the account that sent the PR.
"""
import argparse
import datetime
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = 1


def run(cmd, cwd=None, env=None, log=None, check=True, timeout=None):
    started = time.time()
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)
    out = proc.stdout + proc.stderr
    if log:
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        Path(log).write_text(f"$ {' '.join(map(str, cmd))}\n{out}\n[exit {proc.returncode}, {time.time() - started:.1f}s]\n",
                             encoding="utf-8")
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, cmd))} failed ({proc.returncode}):\n{out[-3000:]}")
    return proc.returncode, out


def git(repo, *args):
    return run(["git", "-C", str(repo), *args])[1].strip()


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_profile(name):
    path = Path(name)
    if not path.exists():
        path = HERE / "profiles" / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- diff analysis

def changed_files(repo, base, head):
    rows = []
    for line in git(repo, "diff", "--numstat", "-M", f"{base}..{head}").splitlines():
        added, deleted, path = line.split("\t", 2)
        if " => " in path:
            path = re.sub(r"\{[^}]* => ([^}]*)\}", r"\1", path).split(" => ")[-1]
        binary = added == "-"
        rows.append({"path": path, "added": 0 if binary else int(added), "deleted": 0 if binary else int(deleted),
                     "binary": binary})
    return rows


def added_lines(repo, base, head):
    lines, path, number = [], None, 0
    for raw in git(repo, "diff", "-U0", f"{base}..{head}").splitlines():
        if raw.startswith("+++ "):
            path = raw[6:] if raw.startswith("+++ b/") else None
        elif raw.startswith("@@"):
            number = int(re.search(r"\+(\d+)", raw).group(1))
        elif raw.startswith("+") and path:
            lines.append((path, number, raw[1:]))
            number += 1
    return lines


def matches(path, globs):
    return any(fnmatch.fnmatch(path, g) for g in globs)


def classify(profile, files, lines):
    risk = profile["risk"]
    findings = []
    for f in files:
        for level, rule in (("red", "red_paths"), ("amber", "amber_paths")):
            if matches(f["path"], risk.get(rule, [])):
                findings.append({"level": level, "kind": "path", "path": f["path"],
                                 "why": f"touches {rule.replace('_paths', '')} surface"})
        if f["binary"]:
            findings.append({"level": "red", "kind": "binary", "path": f["path"], "why": "binary file, not reviewable"})
    code = risk.get("code_globs", ["*"])
    for path, number, text in lines:
        if not matches(path, code):
            continue
        for pattern in risk.get("red_patterns", []):
            if re.search(pattern["regex"], text):
                findings.append({"level": "red", "kind": "pattern", "path": path, "line": number,
                                 "why": pattern["why"], "text": text.strip()[:160]})
        for pattern in risk.get("amber_patterns", []):
            if re.search(pattern["regex"], text):
                findings.append({"level": "amber", "kind": "pattern", "path": path, "line": number,
                                 "why": pattern["why"], "text": text.strip()[:160]})
    size = sum(f["added"] + f["deleted"] for f in files)
    if size > risk.get("max_lines", 400):
        findings.append({"level": "amber", "kind": "size", "why": f"{size} changed lines (> {risk.get('max_lines', 400)})"})
    return findings


# ---------------------------------------------------------------- tests

def test_files(profile, files):
    return [f["path"] for f in files if matches(f["path"], profile["tests"]["globs"])]


def test_targets(worktree, profile, paths):
    """Executable targets whose source list names one of the changed test files."""
    names = {Path(p).name for p in paths}
    targets = set()
    name = profile["tests"]["target_call"]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"target_call must be a CMake function name, got {name!r}")
    call = re.compile(re.escape(name) + r"\s*\(\s*([A-Za-z0-9_]+)([^)]*)\)", re.S)
    for cmake in Path(worktree).rglob("CMakeLists.txt"):
        if any(part in ("3rdparty", "build") for part in cmake.relative_to(worktree).parts[:1]):
            continue
        text = cmake.read_text(encoding="utf-8", errors="replace")
        for target, args in call.findall(text):
            if any(re.search(rf"(^|[\s/]){re.escape(n)}(\s|$)", args) for n in names):
                targets.add(target)
    return sorted(targets)


def ctest_names(build, targets):
    """Map executable targets to their registered ctest names."""
    found = {}
    for file in Path(build).rglob("CTestTestfile.cmake"):
        for line in file.read_text(errors="replace").splitlines():
            m = re.match(r'\s*add_test\(\[=\[(.+?)\]=\]\s+"([^"]+)"', line)
            if m:
                exe = Path(m.group(2)).stem
                if exe in targets:
                    found[exe] = m.group(1)
    return found


def prepare_worktree(repo, root, name, commit):
    path = Path(root) / name
    if path.exists():
        run(["git", "-C", str(repo), "worktree", "remove", "--force", str(path)], check=False)
        shutil.rmtree(path, ignore_errors=True)
    run(["git", "-C", str(repo), "worktree", "add", "--detach", "-q", str(path), commit])
    submodules(repo, path)
    return path


def submodules(repo, tree):
    """Check out submodules from the local clones of the main repository: offline and fast."""
    if not (tree / ".gitmodules").exists():
        return
    code, listing = run(["git", "-C", str(tree), "config", "-f", ".gitmodules", "--get-regexp", r"submodule\..*\.path"],
                        check=False)
    if code != 0:
        return
    run(["git", "-C", str(tree), "submodule", "init", "-q"])
    for line in listing.splitlines():
        key, sub = line.split(" ", 1)
        name = key[len("submodule."):-len(".path")]
        local = Path(repo) / sub
        if (local / ".git").exists():
            run(["git", "-C", str(tree), "config", f"submodule.{name}.url", local.as_posix()])
    run(["git", "-C", str(tree), "-c", "protocol.file.allow=always", "submodule", "update", "-q"])


def overlay_tests(worktree, head, paths):
    """Base tree + the branch's version of the test files: the 'before' side of fail-before/pass-after."""
    if paths:
        run(["git", "-C", str(worktree), "checkout", head, "--", *paths])


def build_and_test(profile, worktree, build, targets, logs, tag):
    env = dict(os.environ)
    for key, value in profile["build"].get("env", {}).items():
        env[key] = os.path.expandvars(value)
    env["CCACHE_BASEDIR"] = str(worktree)
    if profile["build"].get("path_prepend"):
        env["PATH"] = os.pathsep.join(profile["build"]["path_prepend"] + [env.get("PATH", "")])
    configure = [a.format(src=worktree, build=build) for a in profile["build"]["configure"]]
    code, _ = run(configure, env=env, log=logs / f"{tag}-configure.log", check=False)
    results = {}
    if code != 0:
        return {t: {"status": "configure-failed", "log": f"{tag}-configure.log"} for t in targets}
    names = None
    for target in targets:
        blog = logs / f"{tag}-build-{target}.log"
        code, _ = run(["cmake", "--build", str(build), "--target", target, "--parallel"], env=env, log=blog, check=False)
        if code != 0:
            results[target] = {"status": "build-failed", "log": blog.name}
            continue
        if names is None:
            names = ctest_names(build, targets)
        tlog = logs / f"{tag}-test-{target}.log"
        name = names.get(target)
        cmd = (["ctest", "--test-dir", str(build), "-R", f"^{re.escape(name)}$", "--output-on-failure"] if name
               else [str(Path(build) / profile["tests"]["output_dir"] / target)])
        try:
            code, _ = run(cmd, env=env, log=tlog, check=False, timeout=profile["tests"].get("timeout", 600))
            results[target] = {"status": "pass" if code == 0 else "fail", "exit": code, "ctest": name, "log": tlog.name}
        except subprocess.TimeoutExpired:
            results[target] = {"status": "timeout", "log": tlog.name}
    return results


def conventions(profile, worktree, base, logs):
    cmd = profile.get("conventions")
    if not cmd:
        return None
    cmd = [a.format(src=worktree, base=base) for a in cmd]
    code, _ = run(cmd, cwd=worktree, log=logs / "conventions.log", check=False)
    return {"status": "pass" if code == 0 else "fail", "log": "conventions.log"}


# ---------------------------------------------------------------- verdict

def verdict(findings, before, after, conv, tests_changed):
    reasons = []
    level = "green"
    if any(f["level"] == "red" for f in findings):
        level = "red"
        reasons.append("security-sensitive surface: a human must read the flagged lines")
    proven = [t for t in after if after[t]["status"] == "pass" and before.get(t, {}).get("status") == "fail"]
    weak = [t for t in after if after[t]["status"] == "pass" and before.get(t, {}).get("status") == "build-failed"]
    broken = [t for t in after if after[t]["status"] != "pass"]
    if broken:
        level = "red"
        reasons.append(f"tests do not pass on the branch: {', '.join(broken)}")
    if not tests_changed:
        level = max(level, "amber", key=["green", "amber", "red"].index)
        reasons.append("no test changed: nothing shows the change does what it claims")
    elif not proven:
        level = max(level, "amber", key=["green", "amber", "red"].index)
        if weak:
            reasons.append(f"weak proof: {', '.join(weak)} does not compile without the change, so it cannot show the old "
                           "behaviour was wrong (test through an interface that exists on the base)")
        else:
            reasons.append("no test fails before the change and passes after it")
    if conv and conv["status"] != "pass":
        level = max(level, "amber", key=["green", "amber", "red"].index)
        reasons.append("project conventions check fails")
    if any(f["level"] == "amber" for f in findings):
        level = max(level, "amber", key=["green", "amber", "red"].index)
        reasons.append("sensitive APIs or large diff: flagged lines listed")
    return {"level": level, "proven_by": proven, "weak_proof": weak, "reasons": reasons}


def toolchain(profile):
    info = {}
    for name, cmd in profile.get("toolchain", {}).items():
        try:
            info[name] = run(cmd, check=False)[1].splitlines()[0].strip()
        except (OSError, IndexError):
            info[name] = None
    return info


# ---------------------------------------------------------------- commands

def collect(args, profile):
    repo = Path(args.repo).resolve()
    head = git(repo, "rev-parse", args.head)
    base = git(repo, "merge-base", args.base, head)
    out = Path(args.out).resolve()
    logs = out / "logs"
    shutil.rmtree(out, ignore_errors=True)
    logs.mkdir(parents=True)
    work = Path(args.work).resolve() / head[:12]
    work.mkdir(parents=True, exist_ok=True)

    files = changed_files(repo, base, head)
    findings = classify(profile, files, added_lines(repo, base, head))
    tests = test_files(profile, files)
    started = time.time()

    after_tree = prepare_worktree(repo, work, "after", head)
    targets = test_targets(after_tree, profile, tests)
    before_tree = prepare_worktree(repo, work, "before", base)
    overlay_tests(before_tree, head, [t for t in tests if git(repo, "cat-file", "-t", f"{head}:{t}") == "blob"])

    print(f"[evidence] {len(files)} files, tests {tests or '-'} -> targets {targets or '-'}", flush=True)
    before = build_and_test(profile, before_tree, work / "build-before", targets, logs, "before") if targets else {}
    print(f"[evidence] before: {json.dumps({t: r['status'] for t, r in before.items()})}", flush=True)
    after = build_and_test(profile, after_tree, work / "build-after", targets, logs, "after") if targets else {}
    print(f"[evidence] after:  {json.dumps({t: r['status'] for t, r in after.items()})}", flush=True)
    conv = conventions(profile, after_tree, base, logs)

    if not args.keep:
        for tree in (before_tree, after_tree):
            run(["git", "-C", str(repo), "worktree", "remove", "--force", str(tree)], check=False)

    bundle = {
        "schema": SCHEMA,
        "profile": profile["name"],
        "created": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "base": base, "head": head, "head_ref": args.head,
        "tree": git(repo, "rev-parse", f"{head}^{{tree}}"),
        "files": files,
        "findings": findings,
        "tests": {"files": tests, "targets": targets, "before": before, "after": after},
        "conventions": conv,
        "toolchain": toolchain(profile),
        "seconds": round(time.time() - started),
        "logs": {p.name: sha256(p) for p in sorted(logs.iterdir())},
    }
    bundle["verdict"] = verdict(findings, before, after, conv, bool(tests))
    (out / "evidence.json").write_text(json.dumps(bundle, indent=2), encoding="utf-8")
    (out / "SUMMARY.md").write_text(summary(bundle), encoding="utf-8")
    if args.sign:
        run(["ssh-keygen", "-Y", "sign", "-f", args.sign, "-n", "evidence", str(out / "evidence.json")])
    print(summary(bundle))
    return bundle


def outcome(bundle):
    t = bundle["tests"]
    return {"base": bundle["base"], "head": bundle["head"], "tree": bundle["tree"],
            "targets": t["targets"],
            "before": {k: v["status"] for k, v in t["before"].items()},
            "after": {k: v["status"] for k, v in t["after"].items()},
            "conventions": (bundle["conventions"] or {}).get("status"),
            "level": bundle["verdict"]["level"]}


def verify(args, profile):
    claimed_path = Path(args.evidence).resolve()
    claimed = json.loads(claimed_path.read_text(encoding="utf-8"))
    sig = claimed_path.with_name(claimed_path.name + ".sig")
    signature = None
    if args.allowed_signers:
        if not sig.exists():
            signature = "missing"
        else:
            with open(claimed_path, "rb") as data:
                proc = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", args.allowed_signers, "-I", args.identity,
                                       "-n", "evidence", "-s", str(sig)], stdin=data, capture_output=True)
            signature = "valid" if proc.returncode == 0 else "INVALID"
    args.head = claimed["head"]
    args.base = claimed["base"]
    args.sign = None
    mine = collect(args, profile)
    a, b = outcome(claimed), outcome(mine)
    diffs = {k: {"claimed": a[k], "reproduced": b[k]} for k in a if a[k] != b[k]}
    report = {"signature": signature, "reproduced": not diffs, "differences": diffs, "level": mine["verdict"]["level"]}
    print(json.dumps(report, indent=2))
    (Path(args.out) / "verify.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if not diffs and signature != "INVALID" else 1


def scan(args, profile):
    repo = Path(args.repo).resolve()
    worst = "green"
    for ref in args.refs:
        head = git(repo, "rev-parse", ref)
        base = git(repo, "merge-base", args.base, head)
        files = changed_files(repo, base, head)
        findings = classify(profile, files, added_lines(repo, base, head))
        levels = [f["level"] for f in findings]
        level = "red" if "red" in levels else "amber" if "amber" in levels else "green"
        worst = max(worst, level, key=["green", "amber", "red"].index)
        print(f"{level.upper():5} {ref}: {len(files)} files, +{sum(f['added'] for f in files)}/-{sum(f['deleted'] for f in files)}")
        for f in findings:
            where = f"{f.get('path', '')}:{f['line']}" if "line" in f else f.get("path", "")
            print(f"      {f['level']:5} {where} {f['why']}" + (f": {f['text']}" if "text" in f else ""))
    return 2 if worst == "red" else 0


def summary(b):
    icon = {"green": "GREEN", "amber": "AMBER", "red": "RED"}[b["verdict"]["level"]]
    t = b["tests"]
    lines = [f"## Evidence: {icon}", "",
             f"`{b['head'][:12]}` on base `{b['base'][:12]}`, {len(b['files'])} files, "
             f"+{sum(f['added'] for f in b['files'])}/-{sum(f['deleted'] for f in b['files'])}, collected in {b['seconds']} s", ""]
    if t["targets"]:
        lines += ["| Test target | before (base + new tests) | after (branch) |", "|---|---|---|"]
        for target in t["targets"]:
            lines.append(f"| `{target}` | {t['before'].get(target, {}).get('status', '-')} | {t['after'].get(target, {}).get('status', '-')} |")
        lines.append("")
    if b["conventions"]:
        lines.append(f"Conventions check: {b['conventions']['status']}")
    lines += ["", "**Why this level**"] + [f"- {r}" for r in b["verdict"]["reasons"] or ["tests prove the change; no sensitive surface"]]
    flagged = [f for f in b["findings"] if f["level"] in ("red", "amber")]
    if flagged:
        lines += ["", "**Lines a reviewer must read**"]
        for f in flagged[:40]:
            where = f"{f.get('path', '')}:{f['line']}" if "line" in f else f.get("path", "")
            lines.append(f"- {f['level']} `{where}` {f['why']}" + (f": `{f['text']}`" if "text" in f else ""))
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("scan", help="risk classification of the diff only, no build")
    p.add_argument("refs", nargs="+")
    p.add_argument("--base", default="upstream/main")
    p.add_argument("--repo", default=".")
    p.add_argument("--profile", default="example-cmake")
    for name in ("collect", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--profile", default="example-cmake")
        p.add_argument("--repo", default=".")
        p.add_argument("--out", default="evidence-out")
        p.add_argument("--work", default=os.environ.get("EVIDENCE_WORK", "evidence-work"))
        p.add_argument("--keep", action="store_true", help="keep the worktrees")
        if name == "collect":
            p.add_argument("head")
            p.add_argument("--base", default="upstream/main")
            p.add_argument("--sign", help="ssh private key to sign evidence.json with")
        else:
            p.add_argument("evidence")
            p.add_argument("--allowed-signers")
            p.add_argument("--identity", default="contributor")
    args = parser.parse_args()
    profile = load_profile(args.profile)
    if args.command == "scan":
        return scan(args, profile)
    if args.command == "collect":
        bundle = collect(args, profile)
        return 0 if bundle["verdict"]["level"] != "red" else 2
    return verify(args, profile)


if __name__ == "__main__":
    sys.exit(main())
