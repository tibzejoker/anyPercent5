# any%5

**Open-source contribution at agent speed, without trusting anyone.**

AI agents made writing code cheap. Reviewing it did not get cheaper, so projects close their doors: "you need
10 merged PRs to open a PR". Newcomers then wait for weeks, insiders become the bottleneck, and a patient
attacker can still earn trust and then plant a backdoor (xz-utils).

any%5 is a way of working, plus the tooling for it, where **anyone contributes from day one, nothing waits on
a few maintainers, and nothing ships without independent checks**. It is named after speedrunning's *any%*
category (finish as fast as possible, by any route) and after [AnyPS5](https://github.com/boykopovar/AnyPS5),
whose PR intake pause made us look for something better.

## Results

Statistical simulation: 10,000 contributors over 90 days, 1 % lone attackers, 1 % colluders in rings of 5,
20 % careless reviewers. People come and go (about 17 % online at any time, about 20 days of membership).
Mean of 5 seeds. Attackers turn malicious after a delay since joining (eager 0, standard 3, patient 15 days)
that no rule can influence, so a design cannot win just by delaying them.

| System | Merged/day | Median hours to merge | Shipped backdoors per 10k merges, 14-day soak (eager / standard / patient attacker) |
|---|---:|---:|---|
| Status quo: only insiders submit and review | **0** by day 90 (insiders leave and cannot be replaced) | 15 | 0 |
| Baseline: trust levels + review credits | 2235 | 11 | 21.7 / 18.6 / 8.9 |
| Best of 6 independent designs (safest) | 1781 | 2 | 9.6 / 9.1 / 4.4 |
| Best of 6 independent designs (fastest) | 2356 | 2 | 12.4 / 11.8 / 4.8 |
| **any%5** | **2370** | **2** | **8.2 / 6.6 / 2.8** |

**What this shows, and what it does not** ([red team](docs/REDTEAM.md)):

- **Throughput is the solid result.** About 2,400 merges/day at 10,000 members, 2 h median, linear from 10
  to 100,000 members, with no maintainer in the loop. It comes from jury duty and spare reviewers, not from
  the attacker model.
- **Safety: a small gain per attempt, against noisy attackers only.** The shipped-per-10k-merges column
  flatters any%5, because well-calibrated attackers spend time reviewing and attempt fewer backdoors. The table
  uses the v0.2 review rules: per attempt, backdoors ship 13.6 % of the time against 27.5 % for the baseline.
  The v0.3 rules that stop reject-all rings bring that to about 21 % against 26 %
  ([false rejects](sim/eval/false_rejects.md)).
- **Rejecting honest work is modelled.** With honest reviewers rejecting 3-10 % of clean changes and a
  reject-all ring of 5 % of members, the baseline closes 18 % of honest changes. any%5 closes 0.8 %: a lone
  reject only asks for one more review.
- **No advantage against an xz-style attacker.** Against a one-shot attacker whose backdoor most reviewers
  miss for the same reason, every design ships about 43 % of attempts. Calibration helps catch careless
  reviewers. It does not help catch a backdoor nobody can see.
- **The status-quo row is a worst case.** It assumes insiders can never be replaced.

Full table, method and how to reproduce: [docs/RESULTS.md](docs/RESULTS.md).

## The method in six rules

1. **Work is leased, never owned.** Only pushed work renews a lease: first push within 24 h, 72 h per push,
   5 days at most unless a reviewer confirms real progress. Absences freeze it within limits; the blocker
   everyone waits on relays to whoever is online. If you stop, the next person resumes your branch. Members
   declare what they can run, claim matching tasks, and the README shows which profile the project lacks.
2. **Jury duty, not reputation.** No reputation is needed to contribute, and none buys lighter checks.
   Assigned reviews come before new work. There are no credits to run out of.
3. **Risk from the diff, not the author.** A bot tiers each change green, amber or red from what it touches
   (CI, build, dependencies, network, process execution, memory protection…). Riskier tiers get more
   reviewers and post-merge audits.
4. **Blind, calibrated reviewers.** Reviewers see a diff without its author or title. Mixed into their work
   are *canaries*: copies of pending changes, never anything public. Some have one planted defect, which
   counts as caught only when the reject names its line. Others are left clean, and rejecting them counts
   against the reviewer. Reviews are drawn at random among the better-calibrated half, so rejecting
   everything, or ranking first, buys no control. Catching them ranks reviewers, and reviews go
   to the best-ranked people who are free. This is the biggest single lever measured.
5. **Merge fast, ship after a soak.** Changes merge on approval (2 h median). Releases include only changes
   that spent 14 days on main and finished their post-merge checks.
6. **Ban and revert, never panic.** A backdoor claim needs two independent auditors to confirm it. Then the
   author is banned, all their merged work is reverted, and what their approvers passed is re-checked.
   Honest bugs never cost anyone their membership.

Details and rationale: [docs/METHOD.md](docs/METHOD.md).

## How it runs

**Git mode (default, nothing to host).** A ledger branch, one inbox issue and a GitHub Action replace the
server. Contributors send sealed, signed commands as issue comments. Changes are sent privately as
`git format-patch` output, so canaries can be copies of pending changes that nothing public tells apart. The
Action applies approved patches and pushes them. Tested end to end on GitHub. See [docs/GITMODE.md](docs/GITMODE.md).

```
contributor + agent ──(anyp5 CLI: sealed comment)──> inbox issue ──> Action "coordinator" ──> main
        ▲                                                                │
        └──────────── anyp5-ledger branch: your sealed box ──────────────┘
```

**Server mode.** The same engine behind a small HTTP server that syncs public PRs (`anyp5 serve`). It is
simpler for contributors, but a public PR can be compared with its canary copy. Use it with synthetic
canaries, or prefer git mode.

- `anyp5/`: engine (SQLite ledger), git mode (`gitmode.py`, `seal.py`), HTTP server, GitHub adapter (`gh` CLI)
  and CLI. The only dependency is `cryptography`.
- `templates/anyp5-coordinator.yml`: the git mode coordinator Action.
- `skills/`: agent skills ([contributor](skills/contributor/SKILL.md), [reviewer](skills/reviewer/SKILL.md))
  that teach an agent the loop.
- `evidence/`: evidence bundles for a PR. The tests fail before the change and pass after it, the bundle
  is signed, and a maintainer can re-run it.
- `.github/workflows/anyp5-tier.yml`: labels every PR with its risk tier, without running PR code.
- `sim/`: the simulator, the six independent designs, the synthesis, and the evaluation harness.

### Quick start

```sh
pip install -e .
# maintainer: run the server with a bot account logged into gh
export ANYP5_ADMIN_TOKEN=$(python -c "import secrets; print(secrets.token_hex(16))")
anyp5 serve --db ledger.sqlite --repo owner/project --host 0.0.0.0 --port 8455 [--dry-run]
anyp5 admin join alice            # prints alice's token
anyp5 admin task "Fix overflow in parser" green

# contributor (or their agent, with the skills installed)
anyp5 login --server https://anyp5.example.org --token <token>
anyp5 reviews                     # jury duty first
anyp5 verdict 12 reject --reason "src/parse.c:88 bound should be < len"
anyp5 tasks && anyp5 claim 3 && anyp5 alive
git diff origin/main... | anyp5 tier
```

Run the tests with `python -m unittest discover -s tests -t .`.

## How the design was found

1. A first design (trust levels plus review credits) was simulated. Bootstrapping deadlocks, a credit
   drain and starved audits showed up.
2. **Six fresh AI agents designed a system independently and blind**, from the bare problem statement. Each
   then implemented their design in a copy of the simulator, with the environment frozen. All six converged
   on the same core: random or ranked reviewers, no trust-based bypass, leases, post-merge checks, ban and
   revert.
3. The designs were checked for environment tampering, then **re-evaluated fairly**: a design-independent
   attacker trigger, 90 days, three attacker profiles, and a new metric for backdoors that reach a release.
4. A synthesis agent combined the best rules: calibration ranking, jury duty and post-merge checks during
   the soak. Rules that did not beat noise were dropped. The result was confirmed on fresh seeds.

Every design document and simulator is in [`sim/designs/`](sim/designs).

## Scale, stalled work and runners

- **From 10 to 100,000 members**, throughput is linear, at about 1.4 merges per day per person online, with no
  bottleneck. The median time to merge is 2 h from about 300 members, and 12.6 h at 10. Small projects are
  about 1.5x less safe per merge, so below about 100 members maintainers stay in the reviewer pool.
  [sim/eval/scaling.md](sim/eval/scaling.md)
- **When people holding tasks others depend on stop answering**, throughput barely moves: other work is
  always available. Dependent features are delayed, though, and "zombie" holders who send heartbeats without
  pushing anything were the worst case. Leases renewed only by pushed work, with a 7-day cap, bring the p95
  feature delay from 81 to 39 days. [sim/eval/dependencies.md](sim/eval/dependencies.md)
- **Canaries** built from merged diffs can be gamed, and gaming them doubles the backdoor leak. Copies of
  pending changes cannot. [sim/eval/canary_gaming.md](sim/eval/canary_gaming.md)
- **One repository holds it** (project, ledger branch, inbox issue), measured with the real coordinator up to
  10,000 members: a 4 s pass every 5 minutes, about 3 MB of ledger growth per day, inbox issues rotated before
  GitHub's 2,500-comment limit. [bench/RESULTS.md](bench/RESULTS.md)
- **Runners**: the coordinator is cheap; CI for contributed changes is the real limit (about 600 runner-hours
  a day at 2,400 merges with cold builds). A merge queue with batches of 16 brings it to about 60.
  [bench/RUNNERS.md](bench/RUNNERS.md)

## Prior art

- **Closing the door**: GitHub's collaborator-only PRs and PR caps (2026), "N merged PRs first" gates. These
  are simple, but they do not scale (see the status-quo row).
- **[Vouch](https://github.com/mitchellh/vouch)** (Mitchell Hashimoto, 2026): vouched and denounced lists,
  shareable across projects, with an Action that closes PRs from unvouched users. It works well against spam.
  It is still "trust the author", and vouching is a human bottleneck. The two combine: Vouch at the door,
  any%5 for throughput and checks. A Vouch denounce list can feed any%5 bans.
- **cargo-crev / cargo-vet**: signed, shared code reviews for dependencies. Voluntary review stayed scarce,
  which is why any%5 assigns reviews (jury duty).
- **Stack Overflow review audits, crowdsourcing "gold questions"**: the canary idea, proven at scale. The
  known failure mode, recognisable audits, is the one measured above.

## Limits (read these)

- **It is a model.** Parameters such as detection rates, presence and attacker share are assumptions. Trust
  the comparisons and orders of magnitude, not the absolute numbers.
- **A subtle backdoor can still ship.** One review catches a subtle one about 15 % of the time. any%5 gets
  more and better looks, but does not make each look smarter. Smaller diffs, capability tiers, sandboxed
  builds and evidence bundles are the next levers; the simulator cannot credit them.
- **Canaries must look real.** The built-in mutations (bounds, inverted checks, removed guards, hidden
  exec) are a default. Add project-specific ones.
- **The server is trusted infrastructure.** Run it with backups, publish its event log, and keep its admin
  token off contributor machines.
- **Not battle-tested yet.** This is v0.1, built and measured in one session. Pilot it on a small project
  first.

## Origin

Built while contributing to AnyPS5, when the project's PR intake was paused to work through a backlog of
agent-written PRs. Written with Claude Code (AI-assisted: design, simulations, code and docs).
