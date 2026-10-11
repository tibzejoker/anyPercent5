# DESIGN — "Sortition + Bonded Review + Taint Rollback"

(Phase 1 written blind, before opening `sim/`. Phase 2 notes are at the end.)

## Premises

1. Writing code is free; **reviewer attention is the scarce resource**. Every rule below is about spending
   that attention where risk is, and never letting the author decide who spends it.
2. Reputation by *count* ("10 merged PRs") is farmable. Reputation must be **at stake**: whoever vouches
   for a change loses standing if it turns out bad, even years later.
3. No single review is reliable. Safety comes from **independent, randomly chosen** eyes plus **post-merge
   sampling** that keeps going after merge, so that patience does not pay for an attacker.
4. Anyone can leave at any moment. Nothing may block on a named person.

## 1. Work: a public task board with leases

- Work is cut into tasks of ≤ 1 day of effort by a planner (any trusted member may split; bots propose
  splits for tasks whose leases expired twice).
- **Claim = lease.** A claim lasts 2× the task's estimate (min 4 h). The agent heartbeats by pushing to
  its task branch; no push for one lease period → lease expires, task returns to the board with the
  partial branch attached (abandoned work is recovered, not lost). Max 2 simultaneous leases per person
  (+1 per trust level) so nobody hoards.
- Priority: board ordered by (priority, age). Tasks touching sensitive areas (crypto, build system,
  CI, release scripts, parsers of untrusted input, dependency bumps) are tagged `sensitive` by path rules.

## 2. Review: sortition, never author-chosen

- When a change is submitted, the **review bot draws reviewers at random** from the pool of online
  members, weighted by trust, excluding: the author, anyone who reviewed the author's last 5 changes,
  anyone who co-authored with the author recently (anti-ring), and members younger than 7 days.
- Number of reviewers by risk tier (risk = path sensitivity × size × author trust):
  - **T0 low** (docs, tests, non-sensitive with author trust ≥ L2): 1 reviewer.
  - **T1 normal**: 2 independent reviewers (must not see each other's verdict before submitting — blind).
  - **T2 sensitive** or author trust L0/L1: 2 reviewers, at least one at L3, plus mandatory post-merge audit.
- A review is a lease too (8 h). Expired → redrawn. Reviews are **give-to-get**: each submitted change costs
  the author 1 review credit; a credit is earned by completing a review. New members start with 2 credits.
  This makes review capacity scale with submission volume automatically.
- Automated checks run first (tests, static analysis, diff-of-binaries, dependency provenance); they catch
  some fraction of problems before humans spend time.

## 3. Trust: a bonded score, not a counter

Levels L0 (new) … L4. Score `s` in [0, 1].
- **Earned slowly**: +0.01 per change that survives a 14-day probation window *and* was not flagged;
  +0.005 per review whose verdict agreed with an audit. Caps per week (+0.05) so farming is time-bound.
- **Lost fast**: a change of yours found buggy: −0.05. Found malicious: → 0 and ban.
- **Reviewer bond**: approving a change later found malicious costs the approver −0.3 (and L3 status);
  approving a buggy one −0.03. Reviewers sign with their reputation, so collusion/laziness is expensive.
- **Canaries**: a bot occasionally submits a change with a known planted defect (≈2% of review
  assignments). Missing a canary costs −0.05; catching it gives +0.02. This measures diligence
  continuously and identifies lazy rubber-stampers. Canaries cost review time (that is the price).
- High trust does **not** exempt from review — it only lowers the reviewer count for non-sensitive code.
  Sensitive code is always T2 regardless of author trust (xz lesson: the trusted maintainer is the threat).

## 4. Post-merge: probation, sampled audits, taint rollback

- Merges go to `main` immediately (speed), but releases only ship changes past a **14-day probation**.
- **Random audits**: an audit queue samples merged changes: 100% of T2, 10% of T1, 3% of T0, plus
  extra weight for authors in their first 30 days and for changes whose reviewers had low canary scores.
  Auditors are drawn by sortition from L2+ and paid in review credits ×2.
- **When a backdoor is found**: (a) author banned, (b) **every** change the author merged is reverted
  or forced into audit (taint propagation), (c) all approvers of the backdoor lose their bond, and their
  own recent approvals get re-audited, (d) the co-review graph around the author is examined: members who
  repeatedly reviewed each other get their pairings disallowed and audit rate raised.
- When a bug is found, the fixer gets credit; the author/reviewers take the small penalty.

## 5. Joining / leaving

- Joining: L0, can claim T0 tasks and review T0/T1 (as second reviewer only). No gate on contributing,
  only on what is reviewed by whom.
- Leaving: all leases expire on their own; trust decays 1%/week when inactive, so dormant high-trust
  accounts (account takeover target) do not keep L3/L4 forever.

## Why fast

- No central maintainer queue: reviewers are drawn from the whole pool; give-to-get keeps supply = demand.
- Low-risk changes need one review; ~70 % of work should be T0/T1.
- Merges are immediate; safety work (audits) happens in parallel, off the critical path.

## Why safe

- Attackers cannot choose reviewers (sortition) → rings rarely review each other.
- Patience does not help: sensitive changes are always 2-reviewed + audited regardless of trust.
- Approval is bonded → lazy reviewers and accomplices lose trust; canaries find lazy reviewers early.
- A caught backdoor drags the attacker's whole history back through audit.

## Phase 2 — measurement (sim/trustsim.py, scenarios `mine` and `mine-robust`)

### What was implemented (all opt-in config keys; `proposal` output is bit-identical to the original)

| Rule | Key | `mine` | `mine-robust` |
|------|-----|--------|---------------|
| Reviewers per tier (green/amber/red) | `tier_reviewers` | 1 / 3 / 2 | 2 / 3 / 2 |
| Spare reviewer: first N of N+1 decide, the straggler's lease is cancelled | `spare_reviewers` | 0 | 1 |
| Post-merge audit sampling per tier | `audit_tiers` | 50 % / 50 % / 100 % | 35 % / 35 % / 100 % |
| Audits jump the queue, any L1+ may audit, audits pay 2 credits | `audit_front`, `audit_level=1`, `audit_credit=2` | yes | yes |
| Give-to-get: a PR costs 1 credit (≈ reviews it consumes) | `pr_cost` | 1 | 1 |
| Canary gate: a reviewer who missed a canary only gets canaries until they catch one | `canary_gate`, `canary_every=20` | yes | yes |
| Bond taint: approvers of a found backdoor get all their approvals re-audited | `approver_taint` | yes | yes |
| Abandoned implementation is resumed by the next taker (remaining hours + 1 h handover) | `recover_impl` | yes | yes |

Also implemented and **rejected** after measurement: `pick_by_trust` (reviews go to highest trust),
`canary_taint` (re-audit approvals of canary missers), `probation_audits` (audit the first k merges after
promotion; it works only because the simulated attacker never adapts), short review leases.

### Results, 5 seeds, frozen environment

| metric | proposal | mine | mine-robust |
|---|---|---|---|
| merged/day (last 7 d) | 2268 | **2405** | 2028 |
| median hours to merge | 11 | **2** | **2** |
| backdoor attempts | 139 | 91 | 95 |
| stopped in review | 41 | 41 | 43 |
| merged | 67 | 47 | 51 |
| found later by audit | 0 | 12 | 10 |
| reverted with author | 12 | 6 | 10 |
| still live at end | 55 | **29** | 31 |
| backdoor-days live | 471 | **221** | 231 |
| canaries caught % | 69 | 67 | 67 |
| bad actors banned | 39 | 53 | 53 |
| honest banned | 0 | 0 | 0 |
| abandoned leases | 41002 | 41691 | 29745 |
| reviews waiting at end | 1350 | 0 | 0 |
| *per attempt: live % / backdoor-days* | *40 % / 3.4* | *32 % / 2.4* | *33 % / 2.4* |

Off-protocol robustness check (attacker attacks from day 1, `attack_trust=0`, 5 seeds):
proposal 2261/day, 163 live, 2682 backdoor-days; mine 2377/day, 122 live, **2900** backdoor-days;
mine-robust 2009/day, 91 live, **1945** backdoor-days.

### What the simulation taught me

1. **Single-review detection of a subtle backdoor is weak (≈0.15 per review, ≈0.26 per audit).** Adding a 2nd
   reviewer to green buys little; per-attempt safety is set mostly by total checking hours, not by
   clever rules. Canary gating and taint propagation helped only marginally (within noise).
2. **The proposal's audits never ran** (0 found): they queue behind reviews and need L2, the same tiny pool
   that 3-reviewer red PRs saturate (1350 reviews waiting). Putting audits at the front and opening them to L1
   is what makes post-merge sampling actually catch things.
3. **The credit economy is a throughput throttle.** With pr_cost 2 and ~1.4 reviews per PR, people sat idle
   for lack of credits; pr_cost must match the review load each PR really creates.
4. **Median latency is dominated by reviewers going offline mid-review** (24 h leases). Short leases
   fix latency but throw away partial work (−10 % throughput). A spare reviewer (first 2 of 3) gives 2 h
   latency for ~6 % throughput; with one green reviewer latency is 2 h for free.
5. **Attempt counts are confounded by how fast trust accrues** (fewer review jobs -> attackers hit trust 5
   later), so compare per attempt. Per attempt, mine cuts live backdoors 40 % -> 32 % and backdoor-days by ~30 %.
6. **One green reviewer is a liability against an attacker who doesn't wait**: with day-1 attacks `mine`
   lets more merge and loses on backdoor-days. Since my design deliberately gives trust *no* scrutiny
   discount, a rational attacker has no reason to wait, so `mine-robust` (2 reviewers + spare + 35 % audits)
   is the configuration I would actually deploy; `mine` is the speed-optimal point for the frozen attacker.
7. Concentrating reviews on high-trust people (`pick_by_trust`) collapsed throughput (−45 %): the trusted
   core became the central bottleneck the design is meant to avoid.

### Not expressible in the simulator

Deterrence from bonds (diligence is fixed, so "reviewers sign with their reputation" cannot change behaviour);
release-after-probation (the metric counts days on main, not days shipped); path-based sensitivity tags
(tier is assigned by the attacker); blind reviews; co-review graph analysis for rings (random assignment
already makes ring overlap negligible); trust decay of dormant accounts and account takeover; partial-branch
quality of recovered work (modelled only as remaining hours + 1 h handover); task splitting.
