# DESIGN — "Juries, not gatekeepers" (agent6)

Phase 1 (written before looking at `sim/`).

## Principle

Trust is the attack surface. Every reputation system that buys an author a faster/lighter path
(xz-utils: years of good work -> maintainer -> backdoor) is a loan an attacker can take and default on.
So in this design **an author's history never reduces how much their own change is checked**.
History only decides *who is allowed to check other people's changes*, and checking is assigned
by lottery, so nobody (honest or not) chooses what they review or who reviews them.

Speed comes from parallelism (10,000 people, review is spread uniformly, no maintainer queue),
not from skipping review for "trusted" people.

## Mechanisms

1. **Risk classes by path, not by person.** Every file has a risk class declared in-repo
   (`RISK` file, changed only through the high-risk process itself):
   - *low* (docs, tests, leaf modules): 1 juror review.
   - *normal*: 2 independent jurors.
   - *critical* (build, CI, release, crypto, auth, parsers of untrusted input, anything that runs
     at install time): 3 jurors + a mandatory deep audit before release.
   Tooling (CI, static analysis, fuzzers) runs on everything first; it is free for humans but only
   catches part of the problems.

2. **Lottery jury.** When a change is ready, jurors are drawn uniformly at random from the pool of
   *eligible and currently available* contributors. The author cannot pick, request or veto jurors.
   Exclusions: the author, people who co-authored with the author recently, and anybody who was on
   a jury with/for this author in the last N draws (pair-frequency cap). The draw seed is public
   (e.g. hash of the merge-base + a beacon) so draws are verifiable. With a malicious ring of r
   people among 10k, the chance that a jury of 2 is entirely the author's ring is ~ (r/10k)^2.
   A juror who is offline/doesn't answer within a deadline is simply replaced by the next draw:
   joining and leaving never blocks anybody.

3. **Eligibility to judge, earned by being right.** New contributors can submit immediately (their
   code is reviewed like anyone's). They become jurors after a probation of a few reviewed merges
   and a few *shadow reviews* (reviews that are recorded but don't count). Juror weight is a
   *calibration score*: how often their approvals survived later audits and how often they caught
   what audits later found. Lazy rubber-stampers lose eligibility; there is no "maintainer" title.

4. **Unanimity to merge, any juror can block.** A change merges when all jurors approve. A rejection
   with a concrete finding goes back to the author; a "this is suspicious" flag escalates to a
   bigger jury (+2) instead of a debate.

5. **Random deep audits after merge (the deterrent).** A fixed fraction p of merged changes (plus
   all critical-path changes before a release) is drawn for a deep audit by a random auditor who
   spends several times the review effort. Audits are cheap in aggregate because they are sampled;
   they are what keeps the rest honest, because the attacker cannot know which change will be audited.

6. **Joint liability + taint sweep.** When an audit (or anybody) proves a backdoor:
   - revert, the author is banned, all their *other* merged changes are queued for audit
     (taint sweep — an attacker who planted several is caught several times);
   - every juror who approved it loses calibration score heavily and their recent approvals get
     extra audit probability. Colluders who wave each other through therefore burn themselves;
     honest jurors who missed it lose a little and recover.
   Being on the jury that *caught* something raises score.

7. **Release gate.** Merged != shipped. Releases are cut from commits whose critical-path changes
   have been audited. Undetected backdoors can still ship, but the window is bounded.

## Why fast

- No single maintainer: review load is ~ (jurors per change) × (changes) / (eligible people),
  uniform, so the queue is the time of 1–2 reviews, done in parallel.
- Most changes are low/normal risk: 1–2 short reviews, no wait on a particular person.
- Offline jurors are replaced, so latency doesn't depend on who is online.

## Why safe

- Behaving well for months buys **no reduction in scrutiny** of your own code, so the xz playbook
  (earn trust, then slip it in) gains nothing except juror eligibility.
- Juror eligibility is useless for a colluding ring because draws are random and pair-capped;
  a ring member rarely judges a ring member.
- Every merged change has a non-zero, unpredictable chance of deep audit, and one catch triggers
  a taint sweep of everything that person and their approving jurors touched.

## Known costs / limits

- More review hours than an author-trust fast lane (every change is reviewed).
- Path risk classes must be honest; a "low risk" path that is actually reachable at install time
  is a hole (mitigated: the RISK file is itself critical).
- Calibration scores only update when audits happen, so they are slow to learn.

---

## Phase 2 — what the simulation measured and taught me

Implemented as scenario `mine` in `sim/trustsim.py` (`MINE` dict + `design == "mine"` branches; `proposal`
is bit-for-bit unchanged, verified against the original on the same seed). Variant sweeps: `sweep.py`.

| metric (5 seeds, mean) | proposal | mine |
|---|---|---|
| merged/day (last 7 d) | 2268 | 1814 |
| median hours to merge | 11 | **2** |
| backdoor attempts | 139 | 89 |
| stopped in review | 41 | 45 |
| merged | 67 | **42** |
| found later by audit | 0 | 10 |
| reverted with author | 12 | 9 |
| still live at end | 55 | **23** |
| backdoor-days live | 471 | **218** |
| canaries caught % | 69 | 53 (harder canaries, see below) |
| bad actors banned | 39 | 55 |
| honest banned | 0 | 0 |
| abandoned leases | 41002 | 26902 |
| reviews waiting at end | 1350 | 0 |

Per attempt: live-at-end 0.40 -> 0.26, backdoor-days 3.4 -> 2.4. Diagnostic (not an output metric):
honest bugs merged 6698 -> 3416, so part of the lower merge count is buggy code that is now rejected.

How each mechanism maps to code:
- No author trust: tasks drawn from a fixed path mix (40/40/20), anyone can write anything.
  Attack trigger: separate counter `act` with the proposal's positive trust increments (merge +1, review
  +0.2, audit +0.5, canary +0.2 instead of +1/-4) -> attackers attack after the same honest activity (5).
- Lottery jury: green 3 / amber 2 / red 2 approvals, **+1 spare juror**, merge on first k approvals; the
  spare's late review still runs and counts as a post-merge catch ("found later").
- Juror calibration: newcomers' first 2 jobs are canaries scored with the frozen `detects()` model (so
  canaries measure diligence x skill, cost review hours); juror if (hits+1)/(n+2) >= 0.5. Seed members
  start calibrated. Excluded people still write code and retry canaries rarely.
- No credit gate (debt unbounded): idle people always take pending jury duty before new work.
  Review lease 48 h (spare covers latency, so a long lease costs nothing and halves abandoned work).
- Liability: author banned + all their merges reverted/audited (as proposal); approving jurors lose
  calibration and all their approvals go to audit (no bans of jurors -> 0 honest banned).

What the simulation taught me:
1. **Latency is offline interruptions, not queues.** A 1-2 h review is interrupted ~45% of the time and
   then waits ~20 h. One spare juror cut median merge time 14 h -> 2 h and adds a free post-merge review.
2. **Reviews are cheap next to implementation** (1.5 h vs 10 h), so extra eyes cost little throughput;
   but per-review detection on subtle (green) code is ~15%, so safety comes from many independent looks
   per attacker plus ban-and-revert on the first catch, not from any single gate.
3. Random post-merge audits are nearly useless here (careful audit ~= 1.5 reviews for 2x the hours); my
   5% audits changed nothing measurable. A spare reviewer beats an audit.
4. Calibration helps (+20-30% detection per juror) but too strict a bar collapses the jury pool
   (juror_min 0.7 -> 197 h latency, 120k reviews waiting). Probation canaries for a population that
   turns over every ~20 days cost ~6% of all hours.
5. My Phase-1 idea "fewer reviewers on low-risk paths" is wrong in this world: attackers pick the
   low-visibility tier, and the sim has no blast radius, so green needs *more* jurors, not fewer.
6. Collusion rings are irrelevant once jurors are drawn at random (P(ring-mate) ~ 4/10000).

Not expressible in the simulator: blast radius / path risk classes (all backdoors equally bad), the
release gate (merged == live), verifiable public draw seeds, pair-frequency caps, escalation to a larger
jury on "suspicious" flags, fix-and-resubmit after a rejection (rejected work is thrown away, which
exaggerates my throughput cost), and the deterrent effect of audits on attacker behaviour.
