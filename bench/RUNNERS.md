# Can the runners be the bottleneck?

There are three kinds of work to run: the coordinator, the CI of contributed changes, and checks no hosted runner
can do (a game on a GPU). Only the second one can saturate GitHub's runners, and above about 1,000
merges/day only if it is done naively. Numbers: `python bench/runners.py` (model) and
[RESULTS.md](RESULTS.md) (measured coordinator).

## 1. The coordinator Action: not a bottleneck

The coordinator is one serialized job every 5 minutes (plus one per comment while that stays cheap). It never
runs contributor code.

| Members | Merges/day | Pass, measured (i9-13900K, Windows) | Pass, estimated on a GitHub runner* | Budget |
|---:|---:|---:|---:|---:|
| 1,000 | ~240 | 0.8 s | ~2 s + ~25 s job overhead | 300 s |
| 10,000 | ~2,400 | 4.0 s (p95 5.1 s) | ~8-10 s + ~25 s | 300 s |

\* Python time x2 for a standard runner core. Job overhead (checkout, Python setup, pip) is taken from the
sandbox test, where whole runs took 14-30 s.

- **Minutes**: 288 jobs/day, each rounded up to a whole minute, is about 8,600 minutes/month. That is free on
  a public repository. On a private Free plan (2,000 minutes/month) the rest is billed per Linux minute.
- **Concurrency**: it holds 1 of the 20 job slots of a Free plan, 5 % of the time.
- **If one pass ever exceeds ~4 minutes**: shard the project into areas, each with its own ledger and
  coordinator, or run the coordinator on a maintainer's machine. Because it runs no contributor code, that is
  safe, but only from a separate private "ops" repository. A self-hosted runner attached to a public repository
  can be reached by workflows from fork pull requests, and GitHub advises against it.

## 2. CI for contributed changes: the real risk, and how to avoid it

Assumptions, AnyPS5-like C++ project:
- a run is a cold build of 10 min plus 3 min of tests, or 5 min with a warm cache (ccache) and only the
  affected targets;
- submissions are merges x 1.15 (rejected changes are built too);
- 3 % of approved changes break the build or the tests.

| Merges/day | Strategy | Runner-hours/day | Share of 20 concurrent jobs (480 h/day) |
|---:|---|---:|---:|
| 10 | CI on every submission, cold | 2.5 | 0.5 % |
| 100 | CI on every submission, cold | 24.9 | 5.2 % |
| 100 | warm cache + affected targets | 9.6 | 2.0 % |
| 2,400 | CI on every submission, cold | **598** | **125 %** (does not fit) |
| 2,400 | warm cache + affected targets | 230 | 48 % |
| 2,400 | merge queue, batches of 16, bisect on red | 58 | 12 % |
| 2,400 | evidence bundles + 10 % spot checks + merge queue | 81 | 17 % |
| 2,400 | volunteer runners, each job on 2 machines | 460 (free hardware) | 0 % of GitHub's |
| 24,000 | merge queue, batches of 32 | 600 | 125 %, and one queue lane is too slow |

**Capacity.** On standard GitHub-hosted runners a public repository gets unlimited minutes, with 20
concurrent jobs on Free (60 on Team, 500 on Enterprise). That is 480 / 1,440 / 12,000 runner-hours/day. Jobs
are capped at 6 h. A private Free plan gets 2,000 Linux minutes/month, about 1.1 h/day, or 0.55 h/day on
Windows, where minutes count double. Larger runners are always billed, even for public repositories. So:
- a **private** repository cannot even run per-submission CI at 10 merges/day for free;
- a **public** one can, up to about 1,000 merges/day.

### The bypasses, measured in the model

1. **Warm cache + affected targets only (2.6x)**. ccache keyed by branch, a build that rebuilds only what
   changed, tests selected by dependency. This is the cheapest win and needs no new process. Actions caches
   are capped per repository, so keep one shared base cache, refreshed from `main`.
2. **Merge-queue batching (about 3.4x more)**. Test approved changes together on top of `main` and bisect a
   red batch. The Monte Carlo of bisection gives **0.29-0.37 CI runs per landed change** for batches of 4-32
   at 3 % breakage (1.0 without batching).
   - One queue lane runs batches back to back: at 5 min per run it lands a day of merges only if
     `merges x 5 min / batch < 24 h`. So batches of at least 9 at 2,400/day.
   - At 24,000/day even batches of 32 need several lanes. Shard the queue by area (directory), like monorepos
     do.
   - Git mode has no pull requests, so GitHub's own merge queue does not apply. The coordinator would do it
     itself: apply the approved batch to a temporary branch, wait for its CI, push if green, bisect if red.
     **Not implemented yet.** Today the coordinator pushes approved patches without a CI gate.
3. **Evidence bundles + spot checks**. The contributor runs fail-before/pass-after locally and submits a
   signed bundle ([evidence/](../evidence)). CI re-runs a random sample.
   - A lie does not pay when the spot-check rate q > G / (G + L), where G is the gain from lying and L the loss
     when caught (ban + revert of all merged work):

     | Who lies | G | L | q needed |
     |---|---|---|---|
     | Lazy member, skips 15 min of tests | 0.25 h | 20 h of merged work | > 1.2 % |
     | Newcomer | 0.25 h | 1 h of work | > 20 % |
     | Attacker, wants a change CI would reject, throwaway account | 10 h | 1 h | > 91 % |

   - In any%5 membership is cheap: no reputation to lose, and about 20 days of membership with about
     5 merges per member. So spot checks cannot deter an attacker. **Bundles cannot replace CI on `main`.**
   - What they buy: reviewers never spend time on a red change, the submitter's machine pays for pre-review
     builds, and a false bundle caught in a spot check is a ban, like a missed canary.
   - Keep the merge queue as the gate. 10 % spot checks cost 23 runner-h/day at 2,400 merges/day.
4. **Volunteer runners, BOINC style**. The same job goes to 2 random volunteers and the coordinator compares
   result hashes; on a mismatch, a third breaks the tie.
   - **Hashes**: compare the hash of the test outcome list. Binary hashes only match with reproducible builds.
   - **Collusion**: with a share c of colluding volunteers, both copies of a job go to colluders with
     probability c² (0.01 % at c = 1 %). Trusted hosted runners still re-run a sample, as for bundles.
   - **Cost**: 2x compute, on free hardware.
   - **Security**: volunteers run untrusted code, so jobs must run in a disposable VM or container, with no
     network, no secrets and no access to the host. Volunteers pull sealed jobs from the coordinator and return
     signed results. They must **never** be registered as self-hosted runners of the public repository, where
     a fork pull request can run anything on them.

## 3. Checks GitHub runners cannot do (GPU, the game itself)

Example: does GTA V still reach the menu on Windows with a real GPU? No hosted runner has the game or the GPU.

- **Capability jobs**: members declare a capability (Windows + GPU + owns the game). The coordinator assigns
  "run this build" jobs to them like reviews (jury duty), **to 2 holders at random**. Frames are not
  deterministic, so they compare outcome categories, not hashes: reaches the menu, no crash in N minutes, FPS
  band. A mismatch goes to a third holder. Logs and screenshots come back sealed.
- **Volume**: per change it does not fit. At 2,400 merges/day and about 10 min per boot test, that is 400
  h/day. Run it **once per batch of `main`**, nightly or every N merges, and bisect on regression:
  - about 1 run plus log2(batch) runs, so about 12 for 2,400 changes;
  - about 2 x 13 x 10 min, or **4-5 h/day across holders** in the worst case, and 20 min on a quiet day.
- **Safety for holders**: they run builds on their own gaming PCs. Only ever send them builds of `main`, which
  have been reviewed, merged and through CI, never unreviewed patches. Their risk is then the same as any
  nightly-build user's. GPU passthrough VMs would be safer but are rarely practical.

## Recommendations

| Project size | Coordinator | CI |
|---|---|---|
| < 100 merges/day | Action on comment + 5-min schedule | CI per submission (warm cache) on hosted runners. Public repo: free. |
| 100-2,000 merges/day | 5-min schedule only | Warm cache + affected targets. Evidence bundles before review. Coordinator-run merge queue (batches of 8-16) as the gate. |
| 2,000-25,000 merges/day | 5-min schedule; shard by area if a pass nears 4 min | Merge queue sharded by area. Volunteer runners (2x, sandboxed) or larger runners for the remainder. Capability holders for GPU/game checks, per batch. |
