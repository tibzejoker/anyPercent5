# Does it all fit in one repository? Git mode benchmark

Question: can one GitHub repository hold everything, from a small project to a huge one? That means the
project (`main`), the ledger branch, one inbox issue and the coordinator Action.

`bench/gitmode_bench.py` drives the **real coordinator** (`anyp5/gitmode.py`) with real git, real sealing and
real `git am`. One bare repository holds every branch. Every 5 simulated minutes the bench does what the Action
does: one coordinator pass, then commit and push the ledger.
- **Members**: they post commands at the rates of [sim/eval/scaling.md](../sim/eval/scaling.md):
  - 0.237 merges/day per member, 15 % of submissions rejected;
  - a claim, a handover and a task per submission;
  - a heartbeat every 4 h per online member;
  - one verdict per assignment, about 2 h after the draw, approving 90 % of real changes and catching 60 % of
    canaries.
- **Patches**: real `git format-patch` text against the current `main` of a synthetic C codebase (2,000
  files). 20 % of patches go to 10 hot files, 15 % are amber and 3 % red.
- **Machine**: i9-13900K, Windows 11, git 2.47. 2-3 runs went concurrently. Windows process and file costs are
  several times Linux's, so the git and file I/O columns are pessimistic for a GitHub runner, while the Python
  columns are about 2x optimistic.

`v0.2.0` is the published code with everything on the ledger branch. `fixed` is this working tree with the state
and boxes on a single-commit branch.

```
python bench/gitmode_bench.py --members 10000 --hours 12 --layout split --out bench/results/fix_split_n10000.json
python bench/gitmode_bench.py --members 10000 --hours 12 --layout one --code <v0.2.0 checkout> --out ...
python bench/report.py
```

## Results

| Run | Members | Sim h | Commands/h | Merged/day | git am conflicts | Pass s (mean / p95 / last h) | inbox | tick | forge | write (views + seal boxes) | seal state | Ledger commit+push s | git calls/pass | state.sealed MB | Ledger growth MB/day | Repo pack MB | Boxes (MB) | Inbox comments/day (issue full in) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| fixed / split | 100 | 48 | 10 | 17 | 0.0 % | 0.14 / 0.50 / 0.12 | 0.02 | 0.02 | 0.03 | 0.06 (0.00 + 0.00) | 0.004 | 0.61 | 0.3 | 0.08 | 0 | 1 | 101 (0.0) | 245 (10.2 d) |
| fixed / split | 1,000 | 24 | 110 | 185 | 0.0 % | 0.85 / 1.65 / 0.81 | 0.22 | 0.02 | 0.25 | 0.34 (0.00 + 0.00) | 0.029 | 1.72 | 2.8 | 0.62 | 0 | 2 | 1,001 (0.3) | 2,647 (0.9 d) |
| fixed / split | 10,000 | 12 | 1008 | 1438 | 1.1 % | 3.99 / 5.07 / 4.42 | 0.03 | 0.30 | 1.31 | 2.31 (0.03 + 0.01) | 0.196 | 7.09 | 14.8 | 4.15 | 3 | 9 | 10,001 (2.4) | 24,194 (0.1 d) |
| v0.2.0 / one | 100 | 48 | 12 | 26 | 0.0 % | 0.27 / 0.99 / 0.20 | 0.04 | 0.02 | 0.08 | 0.12 (0.02 + 0.00) | 0.005 | 0.55 | 0.5 | 0.08 | 13 | 26 | 101 (0.0) | 278 (9.0 d) |
| v0.2.0 / one | 1,000 | 24 | 113 | 211 | 0.0 % | 1.50 / 2.89 / 1.29 | 0.26 | 0.02 | 0.54 | 0.65 (0.14 + 0.00) | 0.039 | 0.81 | 3.6 | 0.56 | 95 | 95 | 1,001 (0.3) | 2,702 (0.9 d) |
| v0.2.0 / one | 10,000 | 12 | 1032 | 1654 | 0.5 % | 10.79 / 17.24 / 10.55 | 2.06 | 0.31 | 3.10 | 5.26 (1.78 + 0.01) | 0.214 | 2.41 | 22.2 | 3.98 | 748 | 376 | 10,001 (2.4) | 24,762 (0.1 d) |

Columns are means over the run after its first quarter (ramp-up). "Pass" is one coordinator run. Ledger growth
is `git rev-list --disk-usage` of the ledger branch after `git gc`, per day. The issue column is how long one
inbox issue lasts before GitHub's 2,500-comment cap.

## What breaks first, and when

| Limit | GitHub's number | v0.2.0 hits it at | After the fixes |
|---|---|---|---|
| **One issue as inbox** | Commenting is disabled at 2,500 comments (user reports, not in the docs) | 100 members: 9 days. 1,000: 22 h. 10,000: **2.4 h** | Rotates to a new issue at 2,000 comments. The CLI posts to the newest open one, and the coordinator reads every inbox issue |
| **Repository size** | Under 1 GB recommended, under 5 GB strongly recommended | Ledger +13 / 95 / **748 MB per day** at 100 / 1,000 / 10,000 members, so 5 GB in 13 months / 53 days / **7 days** | Ledger +0.1 / 0.3 / 3.2 MB per day (about 1.2 GB a year at 10,000). State and boxes sit on a single-commit branch: 0.1 / 0.7 / 5.6 MB |
| **API rate** (GITHUB_TOKEN, 1,000 requests/h per repository) | 1,000/h | Each run re-reads the whole issue: up to 25 calls a run, 300/h | About 3 calls a run (one search, one page per 100 new comments), 36/h |
| **Pass time** (one run every 5 min) | Budget 300 s | 10.8 s at 10,000 (p95 17 s), growing with history | 4.0 s at 10,000 (p95 5.1 s). See 100,000 below |
| **Pushes to `main`** | No published limit. A push that races another one is rejected | One push per merged change, 1,650/day at 10,000, and a raced push crashed the run | One push per pass. A rejected push changes nothing; the batch is retried next pass |
| **Commits on `main`** | No limit (Linux: ~80k/year) | ~2,400/day at 10,000 members, ~870k/year: big but fine for git | Same |
| **git am conflicts** | — | 0 % / 0 % / 0.5 % | 0 % / 0 % / 1.1 % (resubmit) |

So with v0.2.0, one repository was not viable beyond a small project: within days the inbox issue locks
and the repository passes GitHub's recommended size. After the fixes, it holds at 10,000 members on every
measured axis. The remaining limits are:

1. **Force-pushed state branch.** Each pass orphans the previous 5.6 MB state commit (10,000 members). GitHub
   collects unreachable objects on its own schedule, and its size accounting between collections is not
   documented. If that becomes a problem, keep `anyp5-state` in a **separate private repository**: the
   "second git", which needs no code change, only the Action's push target. An Actions cache or artifact also
   works.
2. **`write()` still rewrites every `members/<login>.json` and checks every box each pass.** That is 2.3 s of
   the 4.0 s at 10,000 members, and it grows linearly. The cheap next fixes are to write `members/` only on
   join, and to shard boxes into 256 bucket files, which also makes the state commit cheaper (7 s on Windows
   at 10,000 boxes, mostly file I/O). Not done here because that function was being edited elsewhere.
3. **CI on the merged batch.** The coordinator pushes approved patches without running the project's tests.
   See [RUNNERS.md](RUNNERS.md): a coordinator-run merge queue is the next step.

## Beyond one repository

- **100,000 members (about 24,000 merges/day):** not measured. The run was stopped during setup (100,000 key registrations and the first 100,000 boxes on Windows took more than 20 minutes). The fixed parts scale with the number of commands; `write()` scales with the number of members, so expect about 25 s for it per pass (10x the 10,000 figure). That fits the 300 s budget, but leaves the inbox at an issue every 12 minutes and about 80 patches per pass. Shard by area before this size.
- **Separate ledger repository**: the ledger and state branches move to `project-anyp5`, and `main` stays
  clean. It costs one more token, for the Action to push to the project.
- **Sharding by area** (directory), each with its own inbox issue, coordinator and merge lane: for monorepo
  scale or when a pass nears 4 minutes. Members can belong to several areas.
- **Merge queue lanes**: at 24,000 merges/day one serial lane cannot even test batches of 32 in 24 h.

## Fixes made (anyp5/, all tests pass)

- `engine.py`: indexes on assignments, changes, tasks and events.
- `gitmode.py`:
  - `GitHubInbox.fetch` reads the repository comment stream newest first and stops at the last processed
    comment, so it no longer paginates the whole issue every run;
  - inbox rotation at 2,000 comments (`rotate`, called by `anyp5 coordinate`), with the client posting to the
    newest open inbox issue;
  - `forge`: one push per pass, and a rejected push records nothing, so the batch is retried next pass. Merged
    patches drop their `patch` text, and settled rejected changes and done canaries drop their text (`prune`);
  - `view`: all boxes come from a few grouped queries per pass, each with the member's 50 most recent changes;
  - `run`: `synchronous=off` on the scratch SQLite copy, which was most of the inbox time;
  - `init_ledger`: a `state_branch` entry and a `.gitignore` for `state.sealed` and `boxes/`. The client reads
    its box from that branch.
- `templates/anyp5-coordinator.yml`:
  - state and boxes on the force-pushed `anyp5-state` branch;
  - partial clone (`filter: blob:none`) of the project;
  - `issues: write` for rotation;
  - a 5-minute schedule.
- Tests: batched push and push refusal, pruning, inbox pagination and rotation filter.

## Sources

- GitHub repository limits (1 GB recommended, 5 GB strongly recommended, 100 MB per file, 2 GB per push):
  https://docs.github.com/en/enterprise-server@3.20/repositories/creating-and-managing-repositories/repository-limits
- Actions limits (GITHUB_TOKEN 1,000 requests/h per repository, concurrency, 6 h jobs):
  https://docs.github.com/en/actions/reference/limits
- Secondary rate limits (80 content-creating requests/min, 500/h): https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api
- 2,500 comments per issue (user reports): https://github.com/2AMLogic/sg13g2-bandgap/issues/248,
  https://github.com/flash-tex/flashtex/issues/1315
- Scheduled workflows can be delayed or dropped under load: https://docs.github.com/es/enterprise-server@3.14/actions/how-tos/troubleshooting-workflows
- Merge queue groups of 1-100 pull requests: https://docs.github.com/en/enterprise-server@3.19/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue
