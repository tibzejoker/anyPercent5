"""any%5 command line.

Contributors and their agents:
  anyp5 login --server URL --token TOKEN     store credentials in ~/.anyp5.json
  anyp5 reviews                              your assigned reviews (jury duty: do these first)
  anyp5 verdict ID approve|reject [--backdoor] --reason "..."
  anyp5 tasks | claim ID | handover ID BRANCH | alive | me | status
  anyp5 tier [DIFF_FILE]                     risk tier of a diff (stdin by default)

Git mode (no server: a ledger branch, an inbox issue and a GitHub Action, see docs/GITMODE.md):
  anyp5 join --repo owner/name [--branch anyp5-ledger]   create your key and ask to join
  anyp5 submit [PATCH_FILE]                  send `git format-patch` output privately for review
  anyp5 task TITLE [TIER] [--requires game windows] [--deps 3 4]   members propose, maintainers open
  anyp5 caps windows gpu game                what you can run (decides which tasks you may claim)
  anyp5 away TASK DAYS | extend TASK [DIFF]  absence (lease frozen, cap not) | progress check at the cap
  anyp5 admit LOGIN KEYS_JSON
  anyp5 direct TASK approve|close [--reason "..."]   architects only: a member's task proposal
  (reviews, verdict, tasks, claim, handover, alive, me and status work the same in both modes)

Maintainers:
  anyp5 setup [--test "CMD"] [--push] [--dry-run]   install git mode on this checkout's repository
  anyp5 serve --db ledger.sqlite [--repo owner/name] [--port 8455] [--dry-run]
  anyp5 admin join LOGIN | task TITLE [TIER] | reverted CHANGE_ID
  anyp5 seal-canaries RECIPES_JSON --ledger DIR   seal project canary recipes into DIR/canaries/
"""
import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

from .tiers import classify, findings, parse_diff

CONFIG = Path(os.environ.get("ANYP5_CONFIG", Path.home() / ".anyp5.json"))


def creds():
    data = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    return os.environ.get("ANYP5_SERVER", data.get("server", "http://127.0.0.1:8455")), \
        os.environ.get("ANYP5_TOKEN", data.get("token"))


def call(path, body=None, token=None):
    server, saved = creds()
    request = urllib.request.Request(server + path, data=json.dumps(body).encode() if body is not None else None,
                                     method="POST" if body is not None else "GET")
    request.add_header("Content-Type", "application/json")
    if token or saved:
        request.add_header("Authorization", f"Bearer {token or saved}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as err:
        sys.exit(f"anyp5: {err.code} {json.loads(err.read() or b'{}').get('error', '')}")


def git_client():
    data = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    if data.get("mode") != "git":
        return None
    from .gitmode import Client
    return Client(data["repo"], data["branch"], data["login"], data["key"])


def git_main(a, client):
    if a.cmd == "reviews":
        for job in client.box().get("reviews", []):
            print(f"=== assignment {job['assignment']}\n{job['diff']}")
    elif a.cmd == "me":
        box = client.box()
        box.pop("reviews", None)
        print(json.dumps(box, indent=2))
    elif a.cmd == "tasks":
        print(client.read("tasks.json"))
    elif a.cmd == "status":
        print(client.read("status.json"))
    elif a.cmd == "verdict":
        client.send("verdict", assignment=a.assignment, verdict=a.verdict,
                    severity="backdoor" if a.backdoor else "none", reason=a.reason)
    elif a.cmd == "submit":
        client.send("submit", task=a.task, patch=Path(a.patch).read_text(encoding="utf-8") if a.patch else sys.stdin.read())
    elif a.cmd == "claim":
        client.send("claim", task=a.task)
    elif a.cmd == "handover":
        client.send("handover", task=a.task, branch=a.branch)
    elif a.cmd == "alive":
        client.send("alive")
    elif a.cmd == "task":
        client.send("task", title=a.title, tier=a.tier, requires=a.requires, deps=a.deps, critical=a.critical)
    elif a.cmd == "caps":
        client.send("caps", caps=a.caps)
    elif a.cmd == "away":
        client.send("away", task=a.task, days=a.days)
    elif a.cmd == "extend":
        client.send("extend", task=a.task, diff=Path(a.diff).read_text(encoding="utf-8") if a.diff else sys.stdin.read())
    elif a.cmd == "admit":
        client.send("admit", member=a.member, keys=json.loads(a.keys))
    elif a.cmd == "direct":
        client.send("direct", task=a.task, decision=a.decision, reason=a.reason)
    else:
        return False
    if a.cmd in ("verdict", "submit", "claim", "handover", "alive", "task", "admit", "caps", "away", "extend",
                 "direct"):
        print("sent; the coordinator applies it on its next run (usually within a minute)")
    return True


def main(argv=None):
    p = argparse.ArgumentParser(prog="anyp5", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("login"); s.add_argument("--server", required=True); s.add_argument("--token", required=True)
    sub.add_parser("reviews"); sub.add_parser("tasks"); sub.add_parser("alive"); sub.add_parser("me"); sub.add_parser("status")
    s = sub.add_parser("verdict"); s.add_argument("assignment", type=int); s.add_argument("verdict", choices=["approve", "reject"])
    s.add_argument("--backdoor", action="store_true"); s.add_argument("--reason", default="")
    s = sub.add_parser("claim"); s.add_argument("task", type=int)
    s = sub.add_parser("handover"); s.add_argument("task", type=int); s.add_argument("branch")
    s = sub.add_parser("tier"); s.add_argument("diff", nargs="?")
    s = sub.add_parser("join"); s.add_argument("--repo", required=True); s.add_argument("--branch", default="anyp5-ledger")
    s.add_argument("--login", help="your GitHub login (default: gh's)")
    s = sub.add_parser("submit"); s.add_argument("patch", nargs="?"); s.add_argument("--task", type=int)
    s = sub.add_parser("task"); s.add_argument("title"); s.add_argument("tier", nargs="?", default="green")
    s.add_argument("--requires", nargs="*", default=[]); s.add_argument("--deps", nargs="*", type=int, default=[])
    s.add_argument("--critical", action="store_true", help="maintainers: relays to whoever is online")
    s = sub.add_parser("caps", help="declare what you can run, e.g. windows gpu game"); s.add_argument("caps", nargs="*")
    s = sub.add_parser("away", help="freeze your lease while absent (after a first push)")
    s.add_argument("task", type=int); s.add_argument("days", type=float)
    s = sub.add_parser("extend", help="at the cap: ask a reviewer to confirm progress (branch diff on stdin)")
    s.add_argument("task", type=int); s.add_argument("diff", nargs="?")
    s = sub.add_parser("direct", help="architects: approve a member's task proposal or close it as off-direction")
    s.add_argument("task", type=int); s.add_argument("decision", choices=["approve", "close"])
    s.add_argument("--reason", default="")
    s = sub.add_parser("seal-canaries", help="check project canary recipes and seal them into the ledger")
    s.add_argument("recipes"); s.add_argument("--ledger", required=True)
    s = sub.add_parser("wanted", help="print the capability board, or update a README block with --readme")
    s.add_argument("--ledger", required=True); s.add_argument("--readme")
    s = sub.add_parser("admit"); s.add_argument("member"); s.add_argument("keys")
    s = sub.add_parser("coordinate", help="run one coordinator pass (in the GitHub Action)")
    s.add_argument("--ledger", required=True); s.add_argument("--project", required=True)
    s.add_argument("--repo", required=True); s.add_argument("--no-push", action="store_true")
    s = sub.add_parser("init-ledger", help="create anyp5.json and the coordinator key")
    s.add_argument("--ledger", required=True); s.add_argument("--project", required=True)
    s.add_argument("--branch", default="main"); s.add_argument("--issue", type=int, required=True)
    s.add_argument("--admin", action="append", default=[])
    s = sub.add_parser("setup", help="maintainers: install git mode on this checkout's repository (inbox issue, "
                                     "ledger branch, coordinator key secret, workflows)")
    s.add_argument("--admin", action="append", default=[], help="maintainer login (repeatable; default: gh's)")
    s.add_argument("--test", help="test command run by the merge queue (writes anyp5-ci.yml)")
    s.add_argument("--push", action="store_true", help="also push the workflow commit to the project branch")
    s.add_argument("--architects", nargs="*", default=[], help="logins that approve member task proposals")
    s.add_argument("--dry-run", action="store_true", help="print every step, change nothing")
    s.add_argument("--force-new-key", action="store_true",
                   help="start over when anyp5-ledger exists: new key, new ledger, no members")
    s.add_argument("--repo", help="owner/name (default: the checkout's GitHub repository)")
    s.add_argument("--branch", help="project branch (default: the repository's default branch)")
    s = sub.add_parser("serve"); s.add_argument("--db", default="ledger.sqlite"); s.add_argument("--repo")
    s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port", type=int, default=8455)
    s.add_argument("--dry-run", action="store_true", help="log forge actions without running them")
    s.add_argument("--config", help="JSON file overriding engine defaults")
    s = sub.add_parser("admin"); s.add_argument("action", choices=["join", "task", "reverted"]); s.add_argument("args", nargs="+")
    a = p.parse_args(argv)
    from .gitmode import local_path
    for name in ("patch", "diff", "ledger", "recipes", "readme", "config", "db", "project"):
        value = getattr(a, name, None)
        if isinstance(value, str) and value not in ("-", ":memory:") and (name != "project" or a.cmd == "coordinate"):
            try:
                setattr(a, name, local_path(value))
            except ValueError as error:
                p.error(str(error))
    client = git_client() if a.cmd not in ("join", "coordinate", "init-ledger", "tier", "serve", "admin", "wanted",
                                                 "seal-canaries", "setup") else None
    if client and git_main(a, client):
        return

    if a.cmd == "login":
        CONFIG.write_text(json.dumps({"server": a.server, "token": a.token}))
        print(f"saved to {CONFIG}")
    elif a.cmd == "reviews":
        for job in call("/reviews")["reviews"]:
            print(f"=== assignment {job['assignment']}\n{job['diff']}")
    elif a.cmd == "verdict":
        call("/verdict", {"assignment": a.assignment, "verdict": a.verdict,
                          "severity": "backdoor" if a.backdoor else "none", "reason": a.reason})
        print("recorded")
    elif a.cmd in ("tasks", "me", "status"):
        print(json.dumps(call("/" + a.cmd), indent=2))
    elif a.cmd == "alive":
        call("/alive", {})
    elif a.cmd == "claim":
        call("/claim", {"task": a.task})
        print(f"task {a.task} leased to you; run `anyp5 alive` while you work")
    elif a.cmd == "handover":
        call("/handover", {"task": a.task, "branch": a.branch})
    elif a.cmd == "direct":
        call("/direct", {"task": a.task, "decision": a.decision, "reason": a.reason})
    elif a.cmd == "tier":
        diff = Path(a.diff).read_text(encoding="utf-8") if a.diff else sys.stdin.read()
        paths, added = parse_diff(diff)
        print(classify(paths, added))
        for level, path, line, why in findings(paths, added):
            print(f"  {level:5} {path}{':' + str(line) if line else ''}  {why}")
    elif a.cmd == "join":
        from . import seal
        from .forge import gh
        from .gitmode import Client
        login = a.login or gh("api", "user", "--jq", ".login").strip()
        key = seal.keygen()
        CONFIG.write_text(json.dumps({"mode": "git", "repo": a.repo, "branch": a.branch, "login": login, "key": key}))
        Client(a.repo, a.branch, login, key).send("join", keys=seal.public(key))
        print(f"key saved to {CONFIG} (keep it private); join request sent as {login}")
    elif a.cmd == "seal-canaries":
        from . import seal
        from .canary import check_recipe
        data = json.loads(Path(a.recipes).read_text(encoding="utf-8"))
        recipes = [check_recipe(r, hooks=False) for r in (data if isinstance(data, list) else [data])]
        box = json.loads((Path(a.ledger) / "anyp5.json").read_text())["coordinator"]["box"]
        out = Path(a.ledger) / "canaries" / (Path(a.recipes).stem + ".sealed")
        out.parent.mkdir(exist_ok=True)
        out.write_text(seal.seal(box, json.dumps(recipes).encode()))
        print(f"{len(recipes)} recipes sealed to the coordinator in {out}")
    elif a.cmd == "wanted":
        from .gitmode import update_readme
        block = (Path(a.ledger) / "wanted.md").read_text(encoding="utf-8")
        if a.readme:
            print("updated" if update_readme(a.readme, block) else "unchanged (no markers or same content)")
        else:
            print(block)
    elif a.cmd == "init-ledger":
        from . import seal
        from .gitmode import init_ledger
        key = seal.keygen()
        init_ledger(a.ledger, key, a.admin, a.project, a.branch, a.issue)
        print("store this as the ANYP5_COORDINATOR_KEY secret, nowhere else:")
        print(json.dumps(key))
    elif a.cmd == "setup":
        from .install import Installer, SetupError
        try:
            Installer(dry_run=a.dry_run).setup(a.admin, a.test, a.push, a.architects, a.force_new_key, a.repo,
                                               a.branch)
        except (SetupError, RuntimeError) as error:
            sys.exit(f"anyp5 setup: {error}")
    elif a.cmd == "coordinate":
        from .gitmode import CI, Coordinator, GitHubCI, GitHubInbox, Project
        key = json.loads(os.environ["ANYP5_COORDINATOR_KEY"])
        meta = json.loads((Path(a.ledger) / "anyp5.json").read_text())
        inbox = GitHubInbox(a.repo, meta["inbox_issue"], trusted=meta.get("admins", []))
        ci = {**CI, **(meta.get("config", {}).get("ci") or {})}
        c = Coordinator(a.ledger, key, inbox, Project(a.project, meta.get("branch", "main"), push=not a.no_push),
                        ci=GitHubCI(a.repo, ci["workflow"], ci["check"]))
        try:
            c.run()
        finally:
            c.close()
        if not a.no_push:
            inbox.rotate()
    elif a.cmd == "serve":
        from .forge import GitHub
        from .server import serve
        config = json.loads(Path(a.config).read_text()) if a.config else None
        serve(a.db, a.host, a.port, GitHub(a.repo, apply=not a.dry_run) if a.repo else None, config)
    elif a.cmd == "admin":
        admin = os.environ.get("ANYP5_ADMIN_TOKEN")
        if a.action == "join":
            print(call("/admin/join", {"login": a.args[0]}, token=admin)["token"])
        elif a.action == "task":
            print(call("/admin/task", {"title": a.args[0], "tier": (a.args[1:] or ["green"])[0]}, token=admin))
        else:
            call("/admin/reverted", {"change": int(a.args[0])}, token=admin)


if __name__ == "__main__":
    main()
