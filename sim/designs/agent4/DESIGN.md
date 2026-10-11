# "Trust buys speed, never blindness" — a design for 10,000 contributors

Phase 1 (written blind, before reading the simulator).

## 0. Principles

1. **Reputation is not a pass.** The xz lesson: the attacker is *most* dangerous right after earning trust.
   So trust may buy *priority, bigger tasks and lighter tooling friction*, but it never removes the
   independent random review of a change. Every merge gets >= 1 reviewer the author did not pick.
2. **Nobody chooses who reviews them.** Reviewers are drawn at random by a bot from an eligible pool;
   colluding rings cannot route their own PRs to each other except by luck.
3. **Give to get.** Review capacity is the scarce resource, so it is the currency. You earn review credit
   by reviewing; opening a PR costs credit. Throughput self-balances and farming costs real hours.
4. **Trust is earned from *verified outcomes*, with delay.** Credit for a merge vests only after its
   quarantine window passes without a defect being found. Trust from reviews comes mostly from catching
   planted canaries and from agreement with later audits, not from volume.
5. **No central bottleneck.** Every role (review, audit, triage, revert) is a job in a shared queue that
   any eligible participant can lease. Maintainers set policy, not throughput.

## 1. Work split and assignment

- Every task is an **atom** of <= 1 day of work (bots and leads split larger issues into atoms with
  explicit interfaces). Atoms carry a **risk tier**: R0 (docs/tests), R1 (normal code), R2 (security
  sensitive: crypto, auth, build/release, parsers of untrusted input, CI config).
- A participant **leases** an atom: lease = 2x estimated duration, renewed by heartbeat (any push or an
  explicit ping). A lapsed lease returns the atom to the queue; the partial branch is kept and the next
  holder starts from it ("abandoned work is just a lapsed lease"). No one ever "owns" a component.
- Pull, not push: each participant's agent pulls the highest-priority job it is eligible for, with
  review/audit jobs interleaved so that **~40% of everyone's hours go to verification** (enforced by
  the credit system, not by moral appeal).
- Joining: you start at T0 with 3 credits. Leaving: your leases lapse, your vesting rewards keep vesting,
  nothing blocks.

## 2. Trust levels (per contributor, continuous score s in [0,1], banded)

| Tier | Score | May implement | May review | May audit |
|---|---|---|---|---|
| T0 | < 0.3 | R0, R1 | R0 | no |
| T1 | 0.3-0.6 | R0-R1 | R0-R1 | no |
| T2 | 0.6-0.85 | R0-R2 | R0-R2 | R0-R1 |
| T3 | > 0.85 | all | all | all |

- +0.02 per vested merge (after 7-day quarantine, no defect found), +0.01 per review whose verdict
  matches a later audit, +0.05 per caught canary or real bug. Score decays 1%/week when idle.
- -0.10 per honest bug traced to you (author) or that you approved (reviewer); -1 (ban) for a backdoor.
- Approving a canary: -0.15 and 1-week review ban. Canaries are how laziness gets measured.

## 3. Review rules

- Number of independent random reviewers per change: **R0: 1, R1: 2, R2: 3**, and +1 if the author is
  T0. Trust of the author does *not* reduce this below the tier minimum.
- At least one reviewer per R1/R2 change must be T2+.
- Reviewer sampling excludes: the author, anyone who co-reviewed with the author in >2 of their last 10
  changes (anti-ring), and anyone who joined in the same week as the author (sybil bursts).
- Any single "reject" sends the change back; a reject that a later audit proves wrong costs the
  rejecter a little trust (to prevent griefing).
- Automated checks (CI, fuzzers, static analysis, dependency diff) run first; they are cheap filters,
  never a substitute for a human/agent reviewer.

## 4. Audits, canaries, quarantine

- **Quarantine**: merged code sits 7 days before release. During it, a bot samples changes for
  **post-merge audit**: R2 10%, R1 3%, R0 1%, plus 100% of changes from authors whose trust just
  crossed a tier boundary upward (the "first big change after promotion" is exactly when an xz-style
  attacker strikes) and 25% of changes approved by a single reviewer pair that appears often together.
- Audits are deep (several times review effort), done by T2/T3 auditors as leased jobs that pay
  double credit.
- **Canaries**: ~2% of review jobs are synthetic changes with a known planted flaw. Missing one is the
  cheapest signal of a lazy or colluding reviewer.

## 5. When a backdoor is caught

1. Revert immediately (bot), block release of anything in quarantine from the same author.
2. Author banned (trust -> 0, identity flagged, credits burned).
3. **Taint propagation**: every change by that author in the last 90 days is queued for audit;
   every reviewer who approved the backdoor loses 0.3 trust and has their own recent approvals sampled at
   25% for audit. Colluders surface because their approvals cluster on the attacker's work.
4. The co-review graph is recomputed; anyone in a dense cluster with the attacker drops one tier pending
   audit.

## 6. Why fast

- Small atoms + leases + pull queues => no idle waiting for an owner; abandoned work recovers in
  <= 2x task duration.
- Review cost scales with risk, not with trust; most changes are R0/R1 so the average is ~1.8 reviews.
- Credit economy keeps the review queue short: if review backlog grows, the credit price of a PR rises
  and agents shift to reviewing.

## 7. Why safe

- Attacker must pass >= 2 random independent reviewers (one T2+) for R1, 3 for R2, plus a 3-10% audit
  lottery that rises to 100% right after promotion, plus taint propagation that undoes the whole
  campaign once one plant is caught. Expected escape probability per backdoor is the product of
  independent miss probabilities.
- Colluding rings mostly can't review each other (random sampling, anti-ring exclusion) and get burned
  together once one is caught.
- Farming reputation costs hours of genuine verified work (vested merges + audited reviews); an agent
  farm produces useful work while doing it, and still never escapes random review.

---

# Phase 2 — what the simulation taught me (added after reading `sim/`)

`sim/trustsim.py` scenario `mine` (rules only, via `mine_*` keys; `proposal` and all other
scenarios produce byte-identical output to the original, checked by diff against
`sim/trustsim_orig.py`). Exploration harness: `explore.py`.

## Results (`--seeds 5`)

| metric | proposal | **mine** | mine-no-vesting | mine-no-probation | mine-hedged |
|---|---|---|---|---|---|
| merged/day (last 7 d) | 2268 | **2294** | 2241 | 2379 | 2037 |
| median hours to merge | 11 | **11** | 11 | 11 | 6 |
| backdoor attempts | 139 | **42** | 85 | 50 | 38 |
| stopped in review | 41 | **20** | 37 | 26 | 18 |
| merged | 67 | **19** | 44 | 23 | 20 |
| found later by audit | 0 | **7** | 16 | 1 | 8 |
| reverted with author | 12 | **3** | 6 | 5 | 3 |
| still live at end | 55 | **9** | 22 | 17 | 8 |
| backdoor-days live | 471 | **59** | 203 | 95 | 58 |
| canaries caught % | 69 | **69** | 69 | 69 | 69 |
| bad actors banned | 39 | **27** | 52 | 26 | 26 |
| honest banned | 0 | **0** | 0 | 0 | 0 |
| abandoned leases | 41002 | **24127** | 24518 | 23917 | 19158 |
| reviews waiting at end | 1350 | **0** | 1 | 0 | 5 |

## Lessons

1. **Absolute trust tiers starve as people leave.** With 20-day churn the 200 seeded seniors shrink to
   ~45 by day 30 and nobody else reaches trust 20 in a month, so every level-2 job (red reviews,
   audits) queues forever: in `proposal` audits find **0** backdoors and 1350 jobs are stuck. Fix:
   T2/T3 are *relative* (top 15 % / 3 % of active members by trust, capped at the absolute thresholds),
   recomputed daily. This alone turned audits from dead weight into the second catch channel. This is
   the clearest confirmation of "join and leave without disrupting" needing relative roles.
2. **Random audits are nearly worthless against subtle backdoors** (a careful audit of a green plant
   finds it ~26 % of the time; 5 % sampling => ~1 % of plants). **Targeted** audits are what work:
   the first 6 merges after an author reaches a new tier are audited 100 % (`mine-no-probation`:
   backdoor-days 95 vs 59, at a ~4 % throughput cost). Audits must also jump the queue.
3. **Catching each attacker once is what matters**: ban + revert-all removes their whole campaign.
4. **Vested trust** (author credit for a merge only after 7 days without a finding) halves attempts
   in a 30-day window. Caveat: much of that is the horizon — attackers churn out before acting. In
   steady state it delays rather than prevents; I report `mine-no-vesting` so this is visible: still
   4.4x fewer live backdoors and 2.3x fewer backdoor-days than `proposal`.
5. **Leases: shorter is not faster.** Offline spells are long (mean 20 h) and memoryless, so a 2 h
   review lease cut median latency 11 h -> 5 h but cost 15 % throughput (reviews redone, credits lost).
   Hedged backup reviews (draft a second reviewer after 4 h of silence, cancel the loser) give 6 h
   latency for -11 % throughput (`mine-hedged`). I kept a *longer* 48 h review lease instead: half the
   abandoned leases, +2-3 % throughput, same median. Resuming lapsed implementation leases from the
   partial branch (+1 h handoff) instead of restarting adds ~3 % throughput.
6. Canary bans (7-day review ban after approving a canary) helped a little; more canaries (1/5 PRs)
   cost more than they caught. Fewer reviewers on green (1 instead of 2) bought +4 % throughput but
   +35 % backdoor-days; kept 2.

## Not expressible in the simulator

Risk tiers set by touched paths (tiers are random here), anti-ring exclusion via the co-review graph
(rings almost never meet under random assignment anyway), the credit price that floats with backlog,
reject-griefing penalties, quarantine before *release* (the metric counts live time from merge),
per-area trust, and adaptive attackers (who would wait out probation; the defence is a random,
undisclosed probation length plus permanent random audits).
