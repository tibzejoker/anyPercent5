"""Turn bench/results/*.json into the tables of bench/RESULTS.md."""
import json
import math
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def mean(xs):
    return statistics.fmean(xs) if xs else 0.0


def p95(xs):
    return sorted(xs)[int(0.95 * (len(xs) - 1))] if xs else 0.0


def summary(path):
    r = json.loads(path.read_text())
    passes = r["passes"]
    hours = passes[-1]["hours"]
    warm = passes[len(passes) // 4:] or passes          # skip the ramp-up for timings
    last = passes[-1]["changes"]
    merged, conflicts = last.get("merged", 0), last.get("conflict", 0)
    sizes = r.get("sizes", [])
    end = sizes[-1] if sizes else {}
    per_day = 24 / hours
    comments_day = end.get("inbox_comments", 0) * per_day
    runs_hour = 12
    new_per_run = comments_day / 24 / runs_hour
    old_calls_hour = runs_hour * math.ceil(min(2500, comments_day) / 2 / 100)      # whole issue, half full on average
    new_calls_hour = runs_hour * (1 + math.ceil(new_per_run / 100) + 1)            # search + new pages
    keys = ["init_unseal", "inbox", "tick", "forge", "views", "seal_boxes", "seal_state", "write"]
    return {
        "name": path.stem, "members": r["members"], "layout": r["layout"],
        "code": "v0.2.0" if "v020" in r.get("code", "") else "fixed", "hours": hours,
        "commands_h": sum(p["commands"] for p in passes) / hours,
        "merged_day": merged * per_day, "conflict_rate": conflicts / max(1, merged + conflicts),
        "run_mean": mean([p["run_s"] for p in warm]), "run_p95": p95([p["run_s"] for p in warm]),
        "run_last": mean([p["run_s"] for p in passes[-12:]]),
        "commit_mean": mean([p["commit_s"] for p in warm]),
        "git_calls": mean([p["git_calls"] for p in warm]),
        **{k: mean([p.get(k, 0.0) for p in warm]) for k in keys},
        "state_mb": passes[-1]["state_bytes"] / 1e6,
        "ledger_mb_day": end.get("ledger_bytes", 0) / 1e6 * per_day,
        "state_branch_mb": end.get("state_branch_bytes", 0) / 1e6,
        "main_mb_day": max(0, end.get("main_bytes", 0) - 336_000) / 1e6 * per_day,
        "pack_mb": end.get("pack_bytes", 0) / 1e6,
        "ledger_commits_day": end.get("ledger_commits", 0) * per_day,
        "main_commits_day": max(0, end.get("main_commits", 1) - 1) * per_day,
        "boxes": end.get("boxes", 0), "boxes_mb": end.get("boxes_bytes", 0) / 1e6,
        "comments_day": comments_day,
        "issue_full_days": 2500 / comments_day if comments_day else float("inf"),
        "api_old_h": old_calls_hour, "api_new_h": new_calls_hour,
        "clone_s": end.get("full_clone_s", 0.0),
    }


def main():
    rows = [summary(p) for p in sorted((HERE / "results").glob("*_n*.json"))]
    rows.sort(key=lambda s: (s["code"], s["layout"], s["members"]))
    out = []
    out.append("| Run | Members | Sim h | Commands/h | Merged/day | git am conflicts | Pass s (mean / p95 / last h) "
               "| inbox | tick | forge | write (views + seal boxes) | seal state | Ledger commit+push s | git calls/pass "
               "| state.sealed MB | Ledger growth MB/day | Repo pack MB | Boxes (MB) | Inbox comments/day (issue full in) |")
    out.append("|" + "---|" * 19)
    for s in rows:
        out.append(
            f"| {s['code']} / {s['layout']} | {s['members']:,} | {s['hours']:.0f} | {s['commands_h']:.0f} "
            f"| {s['merged_day']:.0f} | {100 * s['conflict_rate']:.1f} % "
            f"| {s['run_mean']:.2f} / {s['run_p95']:.2f} / {s['run_last']:.2f} | {s['inbox']:.2f} | {s['tick']:.2f} "
            f"| {s['forge']:.2f} | {s['write']:.2f} ({s['views']:.2f} + {s['seal_boxes']:.2f}) | {s['seal_state']:.3f} "
            f"| {s['commit_mean']:.2f} | {s['git_calls']:.1f} | {s['state_mb']:.2f} | {s['ledger_mb_day']:.0f} "
            f"| {s['pack_mb']:.0f} | {s['boxes']:,} ({s['boxes_mb']:.1f}) "
            f"| {s['comments_day']:,.0f} ({s['issue_full_days']:.1f} d) |")
    text = "\n".join(out)
    print(text)
    (HERE / "results" / "table.md").write_text(text + "\n")
    json.dump(rows, open(HERE / "results" / "summary.json", "w"), indent=1)


if __name__ == "__main__":
    sys.exit(main())
