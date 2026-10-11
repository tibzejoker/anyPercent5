# Design: "Stake, Sample, Sweep" (agent2)

Phase 1 (written blind, before reading `sim/`).

## Core idea in one paragraph

Trust is not a gate you pass once; it is **collateral you put at risk on every action**, and it is only
**credited after the change has survived** a quarantine period. Review effort is **assigned by lottery**
(nobody picks their reviewer), **scaled by blast radius, not by reputation**, and backed by a
**random audit stream whose rate never drops to zero** for anyone. When a backdoor is caught, the system
**sweeps**: the author's whole history and everyone who approved them get re-checked, and trust that was
lent to them is burned. Speed comes from making the common case (small, low-risk change) cost exactly one
review, with no human queue owner anywhere.

## 1. Work split and assignment

- **Tasks are small cells**: target ≤ 4 h of implementation, ≤ 400 changed lines. A task above that is
  split by whoever claims it (the split is itself a cheap task).
- **Risk tier per task, computed by a bot from the paths touched** (not by the author):
  - `green` — tests, docs, isolated leaf modules, no new dependencies, sandboxed by CI (≈ 60 % of work);
  - `amber` — ordinary product code (≈ 30 %);
  - `red` — build system, CI, crypto/auth, release tooling, dependency bumps, code that runs with
    privileges (≈ 10 %).
  A change that touches a red path is red regardless of its title. A "green" change whose diff touches
  red paths is rejected by the bot (attackers prefer green; the tier must be honest by construction).
- **Pull-based queue with leases**: anyone eligible claims a task; the lease is 1.5 × the task estimate,
  renewed by a heartbeat (push to the task branch). Lease expiry → task returns to the pool with the
  partial branch attached as a checkpoint; the next claimant continues from it. No penalty beyond a
  small reliability score used only for tie-breaking. This is how abandoned work is recovered and how
  people leave at any time.
- **Priority**: tasks age (priority += 1 per day waiting) so nothing starves.

## 2. Review: how many, by whom

- **Reviewers are drawn by lottery** from eligible, online contributors, weighted by domain trust, with
  **independence constraints**: not the author, not someone who reviewed or was reviewed by the author in
  the last 30 days more than twice (breaks rings), not two reviewers from the same affinity cluster.
- **Number of independent reviews (k)**:

  | tier  | author trust low | medium | high |
  |-------|-----------------|--------|------|
  | green | 2               | 1      | 1    |
  | amber | 2               | 2      | 1    |
  | red   | 3 (≥1 high)     | 2 (≥1 high) | 2 (≥1 high) |

  Reputation lowers review count only for green/amber. **Red never gets cheaper with reputation**: that
  is exactly where a patient attacker cashes in.
- **Review effort is explicit**: reviewers have a minimum time budget per tier (green 0.5 h, amber 1 h,
  red 2 h); reviews that are too fast relative to diff size are discarded (rubber-stamp detector).
- **Automated checks first**: CI, static analysis, dependency/secret scanners, diff-vs-description LLM
  check. They catch a fraction of defects for free before any human time is spent.

## 3. Trust: how it is earned and lost

- Trust is a number per (person, domain) in [0, 1]. Newcomers start at 0.05; ~2 % seed maintainers at 0.9.
- **Earned only after quarantine**: a merged change credits its author +0.02 (green) / +0.04 (amber) /
  +0.06 (red) **14 days after merge if nothing was found**. Reviewers earn +0.01 per review that survives.
  Reviewers also earn when an audit later *confirms* their review (good reviews are the main path to trust).
- **Staking**: approving a change stakes 20 % of the reviewer's trust on it. If a defect is found in the
  change later: honest bug → reviewer −5 %, author −5 %. Backdoor → author → 0 and banned; each approver
  loses their stake (20 %) and is placed in probation (their next 10 reviews are shadow-reviewed).
- **Decay**: trust decays 2 %/week of inactivity, so dormant accounts (bought or stolen) are not
  valuable.
- **Caps**: trust above 0.6 requires both age ≥ 60 days and ≥ 5 audited-clean changes, so it cannot be
  farmed in a burst by agents.

## 4. Audits (the safety net that does not get bypassed)

- **Random post-merge audits**: each merged change is audited with probability
  `p = base(tier) + 0.1 × (1 − author_trust)`, with base green 3 %, amber 6 %, red 25 %.
  The floor (base) applies even to maximally trusted authors.
- **Audits are jobs**: they are picked up from the same queue by high-trust contributors, cost real time
  (2–4 h), and pay double credit. The auditor does not know whether the author is trusted.
- **Canaries / honeypots**: the bot injects known-bad (seeded, harmless) changes into review queues at
  ~1 %; a reviewer who approves one loses 10 % trust. This measures diligence directly and makes
  rubber-stamping expensive. (Costs reviewer time; that is the point.)

## 5. When a backdoor is caught: the sweep

1. Revert the change (bot, immediate), ship a fixed release.
2. **Taint sweep**: every merged change by the author in the last 180 days is queued for audit at top
   priority; every reviewer who approved any of them gets their recent approvals sampled at 50 %.
3. **Ring detection**: the review graph around the author (who approved whom, co-review frequency) is
   flagged; accounts with ≥ 3 mutual approvals with the attacker get their trust frozen until audited.
4. Bounty: whoever found it gets a large trust/credit bonus (finding backdoors is the best way to earn).

## 6. Economy: review supply must match demand

- **Review credit**: submitting a change costs `k` review credits (the reviews it consumes); doing a review
  earns 1, an audit 2. New joiners get 3 free credits. This makes review a duty proportional to what you
  submit and prevents agent swarms from flooding the queue with PRs nobody reviews.
- If the review queue backs up > 24 h, the bot temporarily raises reviewer credit and throttles new claims.

## Why it should be fast

- 60 % of changes (green) need one review by anyone available + CI; no central maintainer.
- Lottery assignment means no waiting on a specific person; leases mean no task is blocked by a vanished
  owner for longer than 1.5 × its estimate.
- Audits are post-merge, so they do not add latency.

## Why it should be safe

- An attacker who farms trust still faces ≥ 2 independent, randomly drawn reviewers on red code plus a
  ≥ 25 % audit chance, and a single catch burns everything, including the reputation of whoever vouched.
- Planting in green (where review is light) is limited by bot-enforced tiering and sandboxing, and by the
  audit floor.
- Rings cannot choose each other as reviewers; the lottery + independence constraints make collusion
  require a large fraction of the reviewer pool.
- Expected cost of an attempt (lose all accrued trust with probability ≈ 1 − (1−d)^k × (1−p_audit))
  exceeds the benefit for an attacker who has to spend months earning it.

## Phase 2 — what the simulation taught me (written after reading `sim/`)

Implemented as scenario `mine` in `sim/trustsim.py` (all rules in the `MINE` dict; `proposal` output is
identical to the original). `python sim/variants.py '{"key": value}'` reruns `mine` with overrides;
`--debug` adds audits done, tier mix and hours/day spent idle/impl/review/audit.

### Results, 5 seeds, 30 days (`sim/out_proposal.txt`, `sim/out_mine.txt`)

| metric | proposal | mine |
|---|---|---|
| merged/day (last 7 d) | 2268 | 2125 |
| median hours to merge | 11 | 5 |
| backdoor attempts | 139 | 39 |
| stopped in review | 41 | 20 |
| merged | 67 | 17 |
| found later by audit | 0 | 5 |
| reverted with author | 12 | 2 |
| still live at end | 55 | 10 |
| backdoor-days live | 471 | 65 |
| canaries caught % | 69 | 69 |
| bad actors banned | 39 | 25 |
| honest banned | 0 | 0 |
| abandoned leases | 41002 | 27213 |
| reviews waiting at end | 1350 | 82 |

60 days (to check the quarantine is not just a 30-day artefact): proposal 356 attempts / 183 merged /
101 live / 3083 backdoor-days, 2225 merged/day; mine 140 / 70 / 30 / 694, 2066 merged/day.

Decomposition (30 d): mine with the proposal's trust accrual (no quarantine, canary pass = +1) gives 95
attempts, 42 merged, 20 live, 201 backdoor-days, 1971/day; additionally without promotion audits: 119 / 58 /
41 / 400, 2178/day. So the gain comes from three places of similar weight: **delayed (quarantined) trust**,
**audits on privilege gain**, and the rest (canary-proven reviewers, approver sweep).

### What the code does (mechanisms that map onto the simulator)

- Review count: green 1, amber 2, red 2, **+1 while author trust < 20** (probation). Reviewers drawn at random
  from the pool of **canary-proven** reviewers, `(caught+2)/(seen+3) >= 0.5`. Canaries (1 per 10 PRs) are
  routed first to reviewers who have never seen one, then to those currently filtered out, so they can
  requalify. Lazy reviewers end up implementing instead of rubber-stamping.
- **Quarantined trust**: author +1 and reviewer +0.2 are credited 7 days after merge, only if nothing was
  found and the person is not banned. Passing a canary is worth a review (+0.2), not +1 (in the proposal,
  canaries are a trust farm for attackers).
- Post-merge audits: 5 % (author trust < 20), 4 % (< 60), 3 % floor, +20 % for red, and **the first 4 merged
  PRs after every level-up are audited** (privilege gain is when a patient attacker strikes).
- Sweep on a catch: the proposal's revert + audit of the author's history, plus an audit of the whole
  history of everyone who approved the author.
- **Hedged review**: if a PR is still unmerged 3 h after submission, one extra reviewer slot opens at the head
  of the queue; on merge/reject, outstanding review jobs are cancelled. Median latency 11 h to 5 h
  (immediate hedging gave 2 h but cost about 5 % throughput).
- **Checkpoints**: an abandoned implementation lease puts its remaining hours in a pool that the next idle
  contributor resumes before starting new work.
- Review credits: a PR costs 1 credit, enforced only when the review queue is > 500 items (otherwise the
  throttle just idles people); contributors filtered out of reviewing are exempt (they cannot earn credits).

### Lessons

1. The first version (8 h review leases, 60 % audits on low-trust authors, canaries every 5 PRs) cut
   throughput to 1562/day: short leases on people who go offline for ~20 h waste work, and blanket audits on
   newcomers are wasted because detection per hour is about the same in review and audit. **Effort only pays
   where it is targeted.**
2. A bug in my canary routing permanently excluded reviewers who failed once (they never got another canary
   to requalify). Any reputation filter needs a way back.
3. With 1 review per PR, the proposal's credit rule (PR = 2 credits, review = 1) starves implementers;
   credit costs must match the review load actually generated.
4. Detection is the scarce resource: with backdoors in green code (visibility 0.35 x 0.5-1), one review
   catches ~14 %, an audit ~27 %. No mix of reviews alone gets far; what works is (a) making attackers earn
   real, surviving work before they can strike, (b) concentrating audits where trust is *newly* granted, and
   (c) making one catch revert everything (an attacker who tries N times is caught with 1-(1-p)^N).
5. Cost of the safety: -6 % throughput (30 d) to -7 % (60 d), mostly audits and canaries.

### Not expressible in the simulator

Path-based tiering by a bot (backdoor tier and `tool_catches` are frozen), per-domain trust, rubber-stamp timing
detection, review-graph ring detection (random assignment already makes rings irrelevant here), trust decay
and age caps (30 days is too short), staking amounts (I kept the proposal's approver penalty), task splitting
(task sizes are frozen), bounties. Red-tier rules barely matter: almost no task is red in the sim.
Promotion audits exploit the fact that the simulated attacker strikes as soon as it reaches trust 5; a real
attacker could wait a few more PRs, so the real rule should be "audit a random 4 of the next 10" plus the
permanent floor. Quarantine also directly delays the frozen `attack_trust` threshold; at 60 days it still
helps, but part of its effect is "attackers start later", which a longer-lived project would see less of.
