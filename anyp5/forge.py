"""GitHub adapter (through the `gh` CLI): pull PRs into the ledger, push outcomes back.

The forge stays the source of truth for code. The ledger only decides who checks what, and when a change
may merge or ship. Run it with a bot account that can merge, comment and label.
"""
import json
import subprocess

from .tiers import parse_diff


def gh(*args, check=True):
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


class GitHub:
    def __init__(self, repo, apply=True, merge_method="squash"):
        self.repo, self.apply, self.merge_method = repo, apply, merge_method
        self.actions = []

    def act(self, *args):
        self.actions.append(args)
        if self.apply:
            gh(*args, check=False)

    def sync(self, engine, now):
        prs = json.loads(gh("pr", "list", "--repo", self.repo, "--state", "open", "--limit", "500",
                            "--json", "number,headRefOid,author,isDraft"))
        for pr in prs:
            if pr["isDraft"]:
                continue
            known = engine.one("select id from changes where kind='pr' and pr=? and head=?", pr["number"], pr["headRefOid"])
            if known:
                continue
            diff = gh("pr", "diff", str(pr["number"]), "--repo", self.repo)
            paths, added = parse_diff(diff)
            cid = engine.submit(pr["number"], pr["headRefOid"], pr["author"]["login"], paths, added, diff, now)
            tier = engine.one("select tier from changes where id=?", cid)["tier"]
            self.act("pr", "edit", str(pr["number"]), "--repo", self.repo, "--add-label", f"anyp5:{tier}")

        for c in engine.q("select * from changes where kind='pr' and state='approved'"):
            self.act("pr", "merge", str(c["pr"]), "--repo", self.repo, f"--{self.merge_method}",
                     "--match-head-commit", c["head"])
            state = json.loads(gh("pr", "view", str(c["pr"]), "--repo", self.repo, "--json", "state",
                                  check=False) or '{"state": "UNKNOWN"}')["state"]
            if state == "MERGED" or not self.apply:
                engine.merged(c["id"], now)

        for c in engine.q("select * from changes where kind='pr' and state='rejected' and id not in "
                          "(select cast(json_extract(data, '$.change') as int) from events where what='reported')"):
            reasons = [r["reason"] for r in engine.q("select reason from assignments where change_id=? and verdict='reject'",
                                                     c["id"]) if r["reason"]]
            body = "any%5 review: changes requested.\n\n" + "\n".join(f"- {r}" for r in reasons or ["(no reason given)"])
            self.act("pr", "comment", str(c["pr"]), "--repo", self.repo, "--body", body)
            engine.log(now, "reported", change=c["id"])

        for c in engine.q("select * from changes where kind='pr' and state='revert' and id not in "
                          "(select cast(json_extract(data, '$.change') as int) from events where what='revert-requested')"):
            self.act("pr", "comment", str(c["pr"]), "--repo", self.repo, "--body",
                     "any%5: the author of this merged change was banned after a confirmed backdoor. "
                     "It must be reverted before the next release; the release gate is blocked until then.")
            self.act("pr", "edit", str(c["pr"]), "--repo", self.repo, "--add-label", "anyp5:revert")
            engine.log(now, "revert-requested", change=c["id"])
        engine.db.commit()
