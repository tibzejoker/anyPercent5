# The any%5 method

Writing code is cheap now. Checking it is not. any%5 is a way to organise a project with thousands of
contributors and their agents so that **anyone can contribute from day one, nothing waits on a few
maintainers, and nothing ships without independent checks**.

It replaces "trust the author" with "check every change, blindly, with reviewers whose reliability is
measured, and ship only what survived a soak period".

## 1. Work is leased, never owned

- Work is split into **tasks** small enough for one session. Anyone can split a task into sub-tasks.
- `claim` gives a **lease** (72 h). Any activity renews it (heartbeat). If the holder goes quiet, the lease
  expires and the task returns to the pool. Joining or leaving never blocks anyone.
- Partial work lives on a branch recorded with the task (`handover`). The next holder resumes it.

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
- **Calibration canaries**: for every 2.5 real changes, one canary is mixed in: a real, already merged diff with one planted defect.
  Rejecting it counts as a catch. A reviewer's score is `(catches + 1) / (canaries seen + 2)`.
- New members get canaries first (up to 5) until their score is established.
- Reviews and audits go to **idle members with the best score**. Ties are broken by a hash of the change,
  which anyone can recompute.
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

- A defect found after merge becomes a fix task. Honest bugs never cost anyone their membership.
- A **backdoor claim** (`--backdoor`) never acts alone: two independent top-calibrated auditors are drawn.
  If both confirm:
  - the author is banned and **all their merged changes are reverted** before the next release;
  - every change their approvers approved is re-audited;
  - nobody else is banned. Measured: banning approvers too ("full contagion") banned honest people with no
    security gain.

## 7. What it does not solve

- A subtle backdoor that passes every check can still ship. One review catches a subtle one about 15 % of
  the time and an audit about 26 %, so safety comes from the number and quality of independent looks, plus
  the soak. Raising the per-look catch rate (smaller diffs, risk tiers by capability, sandboxed builds,
  evidence bundles) is the next lever. The simulator cannot credit it.
- Canary quality matters: mutated diffs are a zero-effort default. Projects should add hand-made or
  agent-made canaries that look like their real defects.
- The ledger server is a trusted component: run it with audit logs, back it up, and publish its event log.
