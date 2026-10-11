# DESIGN — "Stake, Sample, Soak" (agent1)

Phase 1 was written before opening `sim/`. Phase 2 notes are at the end.

## Premise

Writing code is free; **attention** is the scarce resource. A project scales only if review capacity grows with the
number of contributors, so every contributor has to pay for what they consume. Trust should never be a gate that,
once passed, switches checking off: it should only change *how much* checking a change gets. Every merged change
can still be checked after the merge, by anyone, at any time.

## 1. Work: small tasks, leases, give-to-get

- **Task board** (bot-maintained): issues are split into tasks that one person and their agent can do in about 1 day
  (target diff < 400 lines). Tasks tagged with an **area** (module) and a **sensitivity** level
  S0 docs/tests, S1 normal code, S2 build/CI/crypto/auth/parsers/release tooling.
- **Pull-based assignment**: a contributor takes a task; it holds a **lease** (impl 48 h, review 12 h, audit 24 h).
  No heartbeat (a pushed commit or a comment) before the lease expires -> task goes back to the queue, with the partial
  branch attached so the next person resumes it. Nobody "owns" work they are not actively doing.
  Leaving the project = leases expire; nothing else to clean up.
- **Give-to-get**: opening a PR costs **review credits**. 1 credit per S0/S1 PR, 2 per S2 PR. One completed review =
  1 credit, an audit = 2. New members start with 3 credits. This makes review capacity proportional to output,
  which is what lets 10,000 people work without a central bottleneck.
- **Priority**: the queue serves reviews before new implementation work when the review backlog is > 1 day old
  (a contributor asking for work gets a review first).

## 2. Review: random, independent, risk-proportional

- Reviewers are **drawn by the bot**, never chosen by the author, from the eligible pool (online, with area
  familiarity, not the author, not anyone who reviewed together with this author more than 3 times in the last
  30 days). This breaks collusion rings: a ring of 5 inside 10,000 rarely draws its own members.
- **Number of reviewers by risk** `r = sensitivity + (1 - author_trust) + tool_flags`:
  - low risk (trusted author, S0/S1, no tool flag): **1 review**;
  - medium: **2 independent reviews** (reviewers do not see each other's verdict before submitting);
  - high (S2, or new author on S1, or tool flag): **2 reviews + 1 from a trust ≥ 0.8 reviewer**.
- Automated checks (static analysis, fuzzing, diff-shape heuristics: obfuscated code, build script changes,
  binary blobs, test data) run first and are free for humans; they set `tool_flags`.
- Reviewer's verdict is **staked**: if a change they approved is later found to be malicious, they lose trust.

## 3. Trust: earned slowly, from verified outcomes, per area, and lost fast

- Trust in [0, 1]. Newcomers start at 0.1 (seed maintainers 0.9).
- Earned **only from outcomes that were checked by someone else**: a merged change that survives its **soak**
  (14 days without revert/finding) +0.02; a review whose verdict agrees with a later audit +0.01; a caught real bug
  or canary +0.03. Gains are capped at +0.1 / week, so trust cannot be farmed quickly even with many agents
  (an attacker needs ~2 months to reach 0.8 and has to do real, audited work all that time).
- **Canaries**: the bot injects synthetic known-bad changes into ~3% of review jobs. Approving a canary = −0.1 trust
  and the reviewer's recent approvals get extra audit. This measures diligence directly instead of guessing it.
- **Lost fast**: shipped honest bug −0.03; approving a later-caught bug −0.02; approving a backdoor −0.3;
  authoring a backdoor = trust 0, ban, identity burned.

## 4. Post-merge audit (the safety net that does not block anyone)

- Merges are not blocking on audits. Every day the bot samples **merged-but-unaudited** changes for audit:
  rate = 5 % baseline, 20 % for S2, 30 % for authors with trust < 0.3, 100 % for changes of anyone who touched
  something later found malicious. Auditors are random high-trust reviewers paid 2 credits.
- **Soak**: a change ships to a release only after 7 days on main (unless it fixes a security issue). Audits happen
  mostly inside that window, so a caught backdoor never reaches users.

## 5. When a backdoor is caught

1. Revert the change; author banned; all of the author's merged changes are reverted or queued for **100 % audit**.
2. Reviewers who approved it: −0.3 trust, and their last 30 days of approvals go to audit (taint propagation).
3. **Ring detection**: anyone whose co-review/co-approval graph overlaps strongly with the attacker is audited.
4. The catcher gets +0.1 trust and 5 credits (bounty).

## 6. Why it should be fast and safe

- Fast: 1 review for most changes, reviewers picked automatically (no waiting for a specific maintainer), review
  capacity scales with contributors via credits, leases recover abandoned work in ≤ 48 h, audits are post-merge.
- Safe: no "trusted = unchecked" state; random reviewer draw defeats rings; canaries keep reviewers honest;
  random post-merge audits give a patient attacker a per-attack catch probability that grows with every attack,
  and one catch unravels all his past work and his accomplices.

Expected numbers (guess before measuring): ~1.3 reviews per PR on average, < 1 day median merge time,
> 90 % of backdoors caught before release.

## Phase 2 — what the simulation taught me

Scenario `mine` in `sim/trustsim.py` (5 seeds, `python sim/trustsim.py --seeds 5 --only mine`):

| metric | proposal | **mine** |
|---|---|---|
| merged/day (last 7 d) | 2268 | **2299** |
| median hours to merge | 11 | **4** |
| backdoor attempts | 139 | 105 |
| stopped in review | 41 | 66 |
| merged (backdoors) | 67 | **36** |
| found later by audit | 0 | 2 |
| reverted with author | 12 | 12 |
| still live at end | 55 | **22** |
| backdoor-days live | 471 | **216** |
| canaries caught % | 69 | 71 |
| bad actors banned | 39 | 67 |
| honest banned | 0 | 0 |
| abandoned leases | 41002 | 40053 |
| reviews waiting at end | 1350 | 0 |

Per attempt: merged 48 % -> 34 %, still live 40 % -> 21 %. Fewer attempts is a side effect of attackers being banned
earlier (67 vs 39 banned), not of slowing their trust growth: trust is earned exactly as in `proposal`.

### What `mine` implements (rules only; environment untouched)

- **Review shape by risk**: newcomers (trust < 5, sandbox tier, which the attacker model never uses) 1 review;
  everyone else 2 independent reviews + 1 **deep review** (an audit-grade job: audit hours, careful detection) before
  merge; red/tool-flagged 3 reviews. Post-merge audit 10 % for authors with trust in [5, 60).
- **Red and audits no longer need level-2 people**: any trust >= 5 reviewer with a clean canary record.
- **Canary screening**: one missed canary removes you from the review/audit pool (you keep implementing), and your
  last 5 approvals go to audit.
- **Jury duty instead of credits**: reviews are always assigned first; credits never block an idle person.
- **Hand-over of abandoned implementation** with its remaining hours (checkpointed branch).
- Audits served before reviews; audit pays 2 credits.

### What the simulation taught me

1. **Detection per hour is roughly constant**: a review (2 slot-hours) and an audit (3.5) catch about the same per hour.
   You cannot buy safety for free; you can only (a) stop wasting checking effort and (b) put it where attacks are.
2. **The biggest flaw in `proposal` was a starved queue, not weak review**: red work and audits needed trust >= 20
   (2 % of people). Tool-flagged backdoors sat unreviewed, audits never ran ("found later by audit" = 0, 1350 waiting).
   Opening those jobs to proven trust >= 5 reviewers alone raised "stopped in review" from ~38 to ~67.
3. **Give-to-get credits hurt in this world**: when the system already assigns reviews first, credits only make
   people idle. Ranking reviewers by measured catch rate (my Phase-1 idea "best reviewers first") broke the credit
   economy (throughput 2260 -> 1540) and, with credits removed, gave no gain: catches are too rare to measure skill.
4. **Unravelling barely matters here**: 86 % of attackers attack only once in 30 days (they reach trust 5 late and
   churn), so per-attempt detection is what counts; post-ban reverts help only a little.
5. **Strict canary screening beat a lenient rule** (`mine-evidence-screen`: live 30 / 127 attempts) even though it also
   benches ~19 % of diligent reviewers after one unlucky canary; in a real project you need a rehabilitation path.
6. Shorter review leases cut latency but cost throughput (partial work lost); 24 h kept.

### Not expressible in the simulator

- Areas/sensitivity and blast radius (S0 sandbox that cannot ship code is only approximated by "trust < 5 cannot
  attack"; see `mine-no-newcomer-discount` below for the honest cost of reviewing newcomers like everyone else).
- Soak before release (the metric counts live time from merge), slow capped trust growth (the attacker trigger is a
  fixed trust number, so slowing trust would "win" by delaying attacks: I deliberately did not do it),
  co-review-graph ring detection (no social signal in the sim; random draw already neutralises rings),
  false positives of reviewers, adaptive attackers who wait out scrutiny.

### Sensitivity: the newcomer discount (honest cost)

- `mine-no-newcomer-discount` (2 reviews + 1 deep for everyone, deep reviewers trust >= 5): **collapses**, 450
  merged/day, median 488 h, 63k jobs waiting. Bootstrap deadlock: deep reviews need trust >= 5, trust comes from merges,
  merges need deep reviews. Lesson: *no check may require a class of people that only that check can produce.*
- `mine-no-discount-open-deep` (same, any screened person may deep-review): 1801 merged/day, median 24 h,
  88 attempts, 30 merged, 22 live, 171 backdoor-days. Still twice as safe as `proposal` per attempt, at -20 %
  throughput. So about half of `mine`'s speed advantage depends on the sandbox assumption (newcomers' work cannot
  carry a backdoor), which the simulator encodes as "attackers only attack at trust >= 5".

### Honest limits

- Detection is the frozen per-check model; no mechanism here makes a single review smarter, it only spends checks
  better. Residual: about a third of attempts still merge, and most live backdoors are from the last 10 days.
- The attacker is not adaptive (does not wait out deep review, does not target areas, does not farm many identities).
- Strict canary screening benches unlucky diligent reviewers; a rehab path is needed in practice.
- Pre-merge deep review for every non-newcomer change is affordable only because newcomers' sandbox work is cheap to
  review; if the sandbox leaks, use the `open-deep` numbers.
