# The any%5 method

Writing code is cheap now. Checking it is not. any%5 is a way to organise a project with thousands of
contributors and their agents so that **anyone can contribute from day one, nothing waits on a few
maintainers, and nothing ships without independent checks**.

It replaces "trust the author" with "check every change, blindly, with reviewers whose reliability is
measured, and ship only what survived a soak period".

## 1. Work is leased, never owned

- Work is split into **tasks** small enough for one session. Tasks can depend on other tasks; a task opens
  for claiming once its prerequisites are done, and tasks that others wait on are listed first.
- **Who creates tasks**: maintainers open them directly; any member can propose or split one, and a proposal
  is reviewed by one drawn member before it opens. Measured ([capabilities](../sim/eval/capabilities.md)):
  maintainers-only task creation costs 31 % of throughput. A failing run should create the next task from its
  log automatically.
- **Architects (optional)**: a project that needs someone to own its direction lists them in the config
  (`architects`, empty by default). A member's task proposal then also needs one architect's approval
  (`anyp5 direct TASK approve`), and an architect can close a proposal as off-direction, with a reason the
  proposer sees. Architects see proposals without their proposer. They get no power over code: their reviews
  are drawn and scored like anyone's, and they cannot approve, block or merge a change. This is not measured;
  a slow architect slows member-proposed work the way maintainers-only task creation did.
- `claim` gives a **lease** ([lease sweep](../sim/eval/lease_sweep.md), 16,704 runs in six worlds):
  - the first push (`handover` with a branch) is due within **24 h**;
  - **only pushed work renews it**, for **72 h**; a heartbeat does not, so "zombie" holders cannot sit on a
    task others wait on;
  - a **cap of 5 days**, extended by 5 days each time **one drawn reviewer confirms real progress**
    (`extend`, the branch diff). Without the extension a cap takes big tasks from honest people (55 % never
    finished); with it, big-task worlds merge 154/day instead of 89 and the cap still stops fake pushers.
  - When it expires, the task returns to the pool with its branch, and the next holder resumes it.
- **Absence** (`away`, [measured](../sim/eval/away.md)): after a first push, a holder may freeze the lease for
  up to 5 days, 21 days per 30. It never moves the cap. On a task others wait on, the freeze lasts 48 h at most.
  A naive away (no first push, no cap) gave abusers 15-19 days of free hold.
- **Critical tasks relay**: a task marked critical (the blocker everything waits on) returns to the pool, with
  its branch, as soon as its holder has been offline 12 h. On a serial chain of blockers, relaying doubled the
  pace ([blockers](../sim/eval/capabilities.md)); temporary bypass hacks that reveal the next blocker doubled
  it again (never shipped).

## 1b. Capabilities: not everyone can do every task

- Members declare what they can run (`anyp5 caps windows gpu game`); tasks state what they need. One can only
  claim matching tasks. Declarations are unverified: ask for run evidence in the first push.
- **Reviews stay open to everyone.** Restricting reviews to capability holders shrinks the pool: careless
  reviews triple, and attackers who claim a rare capability double shipped backdoors (24 to 46 per 10k).
- **Convert "needs the hardware" into "needs an artifact"**: a capable member captures logs, shader dumps or
  memory dumps (a short task), anyone works from them, and a capable member runs the final check. Measured:
  +65 % throughput when 60 % of tasks need a capability only 15 % of members have.
- The ledger publishes the **wanted board** (`wanted.md`): open tasks and active members per capability set.
  A daily Action copies it into the README, so newcomers see where to start and which profile is missing.

## 2. Jury duty instead of reputation gates

- **Nobody needs reputation to contribute.** Every change is checked the same way, whoever wrote it. A long
  good history buys an attacker nothing: there is no "trusted author" fast path.
- Reviews are **assigned**, not chosen. A member with assigned reviews must do them before claiming new
  work. There are no review credits to run out of. Measured: credit economies left most people idle.

## 3. Risk tier from the diff, not the author

A bot classifies each change from what it touches (`anyp5 tier`):

| Tier | Triggers | Reviewers before merge | Checks after merge (during soak) |
|------|----------|------------------------|----------------------------------|
| green | everything else | 1 | second independent review on 60 % |
| amber | build/tool paths, memory protection, fault handlers, dynamic loading, host files, env vars, unsafe copies | 3 | second review on 100 % |
| red | CI, build scripts, dependencies, binaries, network, process execution, registry, URLs, opaque blobs | 2 | mandatory audit on 100 % |

A spare reviewer is drawn when one is slow (more than 6 h). The change merges on the first approvals.

## 4. Blind, calibrated reviewers

- Reviewers see **an id and a diff**, never the author or the title.
- **Calibration canaries**: for every 2.5 real changes, one canary is mixed in: a **copy of a change still
  under review** with one planted defect, served only through the blind channel. A canary must never come
  from anything public: canaries built from merged diffs are found with `git log`, and then gamers rise in the
  ranking and the share of backdoor attempts that ship doubles (16 % to 36 %, [canary
  gaming](../sim/eval/canary_gaming.md)). Nobody receives two versions of one change, and authors never
  receive copies of their own change.
- **Two-sided score.** 40 % of canaries are left clean. A planted canary counts as caught only when the reject
  cites its line (`file:line`, ±3 lines). Clean canaries count when approved. The score is
  `(catches + 1) / (planted seen + 2) × (clean approved + 1) / (clean seen + 2)`. The red team showed that
  with a catch-only score, rejecting everything ranked first: 5 accounts took 68 % of the reviews and
  rejected half the honest changes. With the two-sided score they got 3.4 %, and 1 % of honest changes were
  rejected.
- A canary still open when its original leaves review is withdrawn without scoring, so waiting for the
  original to land and then comparing tells nothing. An expired canary counts as missed. Members never see
  their own score, since watching it change would reveal which reviews were canaries.
- New members get canaries first (up to 5) until their score is established.
- Reviews and audits are drawn **at random among the better-calibrated half of idle members**, not
  strictly from the top. Calibration keeps careless reviewers out, and no account, however well it scores,
  can hold most reviews. The draw, and the coin that picks which changes get a post-merge review, use a
  **hidden weekly seed** known only to the coordinator. With a public hash, an
  author could retouch their patch (a blank line, a comment) until the draw picked an accomplice or skipped
  the post-merge review. `sha256(seed)` is published when the week starts and the seed is revealed 4 weeks
  later, so every draw can be checked once it no longer matters.
- Canaries are why reviews are served from the any%5 server and not from the forge: a planted PR on a
  public forge is visible to everyone, so it would calibrate nothing.

Measured: ranking by calibration is the single biggest lever. It raises the average catch rate of a review
from 0.53 to 0.65, and cuts the share of reviews done by careless reviewers from 19 % to 4 %.

## 5. Merge fast, ship after the soak

- Approved changes merge immediately (median 2 h from submission in simulation).
- A release is cut only from a point where **every** change merged before it has spent **14 days** on main,
  has finished its post-merge checks, and is not awaiting a revert.

Measured: the soak window is where post-merge checks pay off. With 14 days, shipped backdoors fall 2-3x
compared with shipping on merge, for every design tested.

## 6. When something is found

- **A lone reject asks a second opinion.** Each reject adds one required approval, and two rejects close the
  change. One reviewer can no longer veto a change.
- A defect found after merge becomes a fix task. Honest bugs never cost anyone their membership.
- A **backdoor claim** (`--backdoor`) never acts alone. The change is held, and two auditors are drawn at
  random from the better-calibrated half. While the project has fewer than 100 active members, a maintainer
  must also confirm the ban (`confirm-ban`). If they all confirm:
  - the author is banned and **all their merged changes are reverted** before the next release;
  - every change their approvers approved is re-audited;
  - nobody else is banned. Measured: banning approvers too ("full contagion") banned honest people with no
    security gain.

## 7. Scale

Measured from 10 to 100,000 members ([scaling](../sim/eval/scaling.md)): throughput is linear (about 1.4
merges/day per person online) with no bottleneck, and the median time to merge is 2 h from about 300 members.
Small projects are slower (12.6 h at 10 members) and about 1.5x less safe per merge: calibration has few people
to choose from, and one attacker is a large share. Below about 100 members, maintainers stay in the reviewer
pool and act as backdoor auditors. Past about 2,000 merges/day into one branch, use a merge queue.

## 8. What it does not solve

- A subtle backdoor that passes every check can still ship. One review catches a subtle one about 15 % of
  the time and an audit about 26 %, so safety comes from the number and quality of independent looks, plus
  the soak. Raising the per-look catch rate (smaller diffs, risk tiers by capability, sandboxed builds,
  evidence bundles) is the next lever. The simulator cannot credit it.
- Canary quality matters: mutated diffs are a zero-effort default. Projects should add hand-made or
  agent-made canaries that look like their real defects, as sealed recipes in the ledger
  ([project canaries](GITMODE.md#project-canaries)). How much they raise the catch rate is not measured.
- The ledger server is a trusted component: run it with audit logs, back it up, and publish its event log.
