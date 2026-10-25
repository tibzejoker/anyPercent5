# any%5

I contribute to AnyPS5, an open-source tool that ports PS5 executables to Linux and Windows, and some of my pull
requests wait days before anyone looks at them.
That isn't the maintainers' fault. Since coding agents arrived they get far more PRs than a small team can read,
so they did what many projects do: they capped intake. Newcomers wait, a few people carry all the review, and the
cap still doesn't stop a patient attacker who earns trust first (xz-utils, 2024).

any%5 is an attempt at a different setup. Everyone who sends code also reviews code. Reviewers don't choose what
they review, and their accuracy is measured with planted bugs. It all runs inside a normal GitHub repository with
one Action, so there is no server to host.

It's an experiment. I've simulated it and tested it end to end on GitHub, and this repository runs on it, but no
project with real outside contributors has tried it yet. I'd like to hear where it breaks:
[issues](../../issues), [discussions](../../discussions).

![How a change moves through any%5: join, review, claim, submit; then risk level, drawn reviewers, merge queue, main and release](docs/img/workflow.png)

https://github.com/user-attachments/assets/7324ee94-aff9-45b5-b625-42cddab37499

## The rules

1. You review before you submit. It works like jury duty: everyone does it, and nobody earns their way out of it.
   There's no reputation to build before you can contribute, and no reputation that gets you lighter checks.
2. Reviews are blind: you see a diff, not its author or title. Some diffs are tests, copies of real pending
   changes that are either clean or have one bug planted in them. Finding the bug and naming its line raises your
   score. Rejecting a clean copy lowers it. Reviewers are drawn at random from the better-scoring members.
3. The number of reviewers depends on what a change touches (CI, build scripts, dependencies, network, process
   execution), not on who wrote it.
4. Tasks are leased. You tell the project what your machine can run (`windows`, `gpu`...) and claim a matching
   task. If you stop pushing, the task goes back, and the next person picks up from your branch.
5. Approved changes go through a merge queue that runs the project's own tests, then land on `main` without
   waiting for a maintainer. A release only includes changes that have been on `main` for 14 days.
6. If two randomly drawn auditors confirm a backdoor, the author is banned, their merged work is reverted, and
   what their reviewers approved is checked again. An honest bug never gets anyone banned.

The full rules, and why each one is there, are in [docs/METHOD.md](docs/METHOD.md). Here is a 4-minute
walkthrough for contributors and maintainers:

https://github.com/user-attachments/assets/5fed5c7e-0953-41d7-8da8-f761f5d0b4a3

## Does it work?

I simulated 10,000 contributors over 90 days. Among them: lone attackers (1 %), colluding rings of 5 (1 %), a
ring that rejects everything (5 %), careless reviewers (20 %), and honest reviewers who wrongly reject 3 to 10 %
of clean changes. Each run is averaged over 5 seeds, and about 17 % of people are online at any time.

| | Trust levels + review credits | any%5 |
|---|---:|---:|
| Changes merged per day | 2,032 | 2,339 |
| Median time to merge | 17.6 h | 3.0 h |
| Honest changes closed by wrong rejects | 17.7 % | 0.4 % |
| Backdoor attempts that reach `main` and survive the 14-day soak | 23-26 % | 18-19 % |

If only insiders may submit and review, merges drop to zero by day 90: insiders leave and nobody replaces them.

I'm confident about throughput. It grows linearly from 10 to 100,000 members with no maintainer in the loop. The
safety gain is small: about a quarter fewer backdoors get through than with trust levels. Against a backdoor
subtle enough that most reviewers miss it, xz-style, nothing I tested beats anything else, and about 43 % ship
whatever the design. Checking reviewers catches careless ones, but it doesn't help anyone see what they can't
see.

These are model numbers, so compare the rows rather than trusting the absolute values. The method and the
commands to reproduce them are in [docs/RESULTS.md](docs/RESULTS.md). I also gave this repository to five AI
agents and asked them to break the rules. What they broke is fixed and replayed in tests, and what's left is
listed in [docs/REDTEAM.md](docs/REDTEAM.md).

## How it runs

```
contributor + agent ──(anyp5 CLI: sealed, signed comment)──> "anyp5 inbox" issue
                                                                    │
                                     GitHub Action "coordinator", every 5 minutes
                                                                    │
            anyp5-ledger branch (public)  <─────────────────────────┤
            anyp5-state branch (sealed)   <─────────────────────────┤
            anyp5-queue branch ──> project CI ──> main  <───────────┘
```

Contributors send commands as encrypted, signed comments on one issue. Patches stay private, so you can't tell a
test copy from the real change by looking at public PRs. The coordinator Action never runs contributor code. It
applies approved patches with `git am`, lets the project's CI test them on `anyp5-queue`, and only moves `main` to
a commit that passed. When a batch fails, it splits the batch until it finds the change that broke it.

With 10,000 simulated members, a coordinator pass takes 4 s and the ledger grows by about 3 MB a day
([bench/RESULTS.md](bench/RESULTS.md)). The real cost is CI: about 60 runner-hours a day for 2,400 merges in
batches of 16 ([bench/RUNNERS.md](bench/RUNNERS.md)).

## Try it

As a contributor, or through your agent with the [contributor](skills/contributor/SKILL.md) and
[reviewer](skills/reviewer/SKILL.md) skills:

```sh
pip install "anyp5 @ git+https://github.com/tibzejoker/anyPercent5@v0.4.2"
anyp5 join --repo owner/name        # creates your key, posts the join request
anyp5 caps windows gpu              # what your machine can run
anyp5 reviews                       # jury duty first: diffs only, no author, no title
anyp5 verdict 12 reject --reason "src/parse.c:88 bound should be < len"
anyp5 tasks && anyp5 claim 3
git format-patch origin/main --stdout | anyp5 submit
anyp5 me                            # your changes, their state, your calibration
```

As a maintainer, from your project's checkout, with `gh` logged in as an admin:

```sh
pip install "anyp5 @ git+https://github.com/tibzejoker/anyPercent5@v0.4.2"
anyp5 setup --test "python -m pytest -q" --dry-run   # prints every step, changes nothing
anyp5 setup --test "python -m pytest -q"             # inbox issue, ledger branch, secret, workflows, local commit
git push origin main
```

Setup creates the inbox issue, the `anyp5-ledger` branch and the coordinator key secret. It commits the workflow
files but doesn't push them. [docs/GITMODE.md](docs/GITMODE.md#set-up-maintainer) explains each step. If you work
alone, you keep pushing to `main` as before, and any%5 only kicks in with your first outside contributor
([small projects](docs/METHOD.md#small-projects-one-maintainer-then-the-first-newcomers)). You can also give
[skills/maintainer/SKILL.md](skills/maintainer/SKILL.md) to an agent and ask it to do the install.

## This repository runs on any%5

Outside changes to any%5 go through any%5: join, review, claim a task from the board below, submit a patch. The
coordinator is [`.github/workflows/anyp5-coordinator.yml`](.github/workflows/anyp5-coordinator.yml). Until there
are 100 members, a ban also needs a maintainer's confirmation. See [CONTRIBUTING.md](CONTRIBUTING.md).

<!-- anyp5:wanted -->
| You can run | Open tasks | Waiting on others | Active members | Tasks per member |
|---|---:|---:|---:|---:|
| linux **wanted** | 6 | 0 | 0 | 6.0 |

Updated 2026-10-25 09:37 UTC. Declare what you can run with `anyp5 caps windows gpu ...`.
<!-- /anyp5:wanted -->

## Prior art

Collaborator-only PRs, PR caps and "N merged PRs first" gates are simple, but they don't scale, as the
insiders-only result above shows.

[Vouch](https://github.com/mitchellh/vouch) keeps shared lists of vouched and denounced users and closes PRs from
unknown ones. It's good against spam, but it still comes down to trusting the author, and someone has to do the
vouching. The two can work together: Vouch at the door, any%5 for throughput and checks.

cargo-crev and cargo-vet share signed reviews of dependencies. Voluntary review stayed scarce there, which is
why any%5 assigns reviews instead of waiting for volunteers.

Stack Overflow's review audits and the "gold questions" used in crowdsourcing showed at scale that planted tests
work. They also showed the main failure: audits that look different from real work get spotted. That's why the
test copies here are made from real pending changes.

## Known limits

A subtle backdoor can still ship. any%5 gives each change more reviewers and better-checked ones, but it doesn't
make any single reviewer sharper. Smaller diffs, sandboxed builds and evidence bundles
([evidence/](evidence/)) are the next things to try.

The built-in planted bugs are generic. A project should add its own, taken from its past bug fixes
([how](docs/GITMODE.md#project-canaries)).

A ring can mark its own patches and recognise them in review. The random draw keeps the ring near its fair share
of reviews, but blind review can't hide a diff from the people who wrote it.

Direction is weak. Maintainers open tasks and can name architects who approve task proposals, but nothing checks
a change against an overall design.

Whoever holds the coordinator key can see which reviews are tests.

## Background

I first simulated a design based on trust levels and review credits. It deadlocked, and audits starved. I then
gave the bare problem to six AI agents separately, and each designed a system and implemented it in its own copy
of the simulator. All six landed on the same core: random or ranked reviewers, no bypass for trusted people,
leases, checks after merge, ban and revert. I compared the six under the same conditions and combined them.
Every design and simulator is in [sim/](sim/).

I started this while contributing to [AnyPS5](https://github.com/boykopovar/AnyPS5), when its PR intake was
paused to work through a backlog of agent-written pull requests. The name comes from speedrunning's *any%*
category (finish by any route) and from AnyPS5. I built it with Claude Code: design, simulations, code, docs
and videos.

MIT licence.
