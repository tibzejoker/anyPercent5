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

any%5 matches the fastest design and is the safest under all three attacker profiles. It ships about 3x fewer
backdoors than the trust-level baseline, and merges 5x faster. Full table, method and how to reproduce: [docs/RESULTS.md](docs/RESULTS.md).

## The method in six rules

1. **Work is leased, never owned.** Claim a task and you get a lease that renews while you are active. If
   you go quiet, it returns to the pool, and the next person resumes your branch.
2. **Jury duty, not reputation.** No reputation is needed to contribute, and none buys lighter checks.
   Assigned reviews come before new work. There are no credits to run out of.
3. **Risk from the diff, not the author.** A bot tiers each change green, amber or red from what it touches
   (CI, build, dependencies, network, process execution, memory protection…). Riskier tiers get more
   reviewers and post-merge audits.
4. **Blind, calibrated reviewers.** Reviewers see a diff without its author or title. Mixed into their work
   are *canaries*: real merged diffs with one planted defect. Catching them ranks reviewers, and reviews go
   to the best-ranked people who are free. This is the biggest single lever measured.
5. **Merge fast, ship after a soak.** Changes merge on approval (2 h median). Releases include only changes
   that spent 14 days on main and finished their post-merge checks.
6. **Ban and revert, never panic.** A backdoor claim needs two independent auditors to confirm it. Then the
   author is banned, all their merged work is reverted, and what their approvers passed is re-checked.
   Honest bugs never cost anyone their membership.

Details and rationale: [docs/METHOD.md](docs/METHOD.md).

## How it runs

```
contributors + their agents ──(anyp5 CLI)──> any%5 server (ledger, leases, blind review queue, canaries)
                                                   │  syncs PRs, merges approved ones, labels, revert requests
                                                   ▼
                                          GitHub (code, PRs, releases)
```

Git and GitHub remain the source of truth for code. The small any%5 server exists for one reason: canaries
only calibrate if a reviewer cannot tell them from real changes, and a planted PR on a public forge is
visible to everyone. Reviews are therefore served blind by the server, and only outcomes go back to GitHub.

- `anyp5/`: engine (SQLite ledger), HTTP server, GitHub adapter (`gh` CLI) and CLI. Standard library only.
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
