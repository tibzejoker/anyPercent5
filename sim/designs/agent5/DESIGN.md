# DESIGN — "Sortition review, no earned bypass, retroactive accountability"

(Phase 1 written before opening `sim/`.)

## Threat model in one line
Writing code is free; review time is the scarce resource; the most dangerous actor is the patient one who
earns trust and then spends it (xz-utils), possibly with a few friends (a collusion ring).

## Core principle
**Reputation must never buy a weaker check on a change.** In reputation-gated systems, trust is a currency an
attacker accumulates and then spends on one unreviewed backdoor. Here trust buys only *throughput and
responsibility* (how many changes you may have in flight, whether you may be drawn as a reviewer), never
*less scrutiny of your own code*. So the "behave, then attack" strategy gains nothing from the behave phase.

## Mechanisms

1. **Risk lanes by blast radius, not by author.** Every path in the repo is tagged by what it can reach
   (green: docs, tests, isolated leaf modules running without privileges; red: build system, CI, crypto,
   auth, parsers of untrusted input, release tooling, dependency manifests). The tag is decided by the file,
   not the person. Green changes need 1 independent review; red changes need 2 independent reviews, one of
   which must come from a reviewer with a proven detection record.

2. **Sortition: reviewers are drawn, not chosen.** When a change is submitted, the system draws reviewers
   uniformly at random from currently-online eligible participants (excluding the author and anyone who
   co-authored/reviewed with the author recently — a cheap anti-ring filter that is blind to who colludes).
   A ring of size r among N = 10 000 has probability ≈ r/N of getting a friendly reviewer — negligible. Nobody
   can self-select into reviewing a friend. No central maintainer queue: any online person is a reviewer.

3. **Every review is a recorded attestation.** "I reviewed X at effort E and found nothing" is signed and
   kept forever. Reviewers are paid in review credit; to submit work you must have done reviews (roughly
   1 review per change you submit), so review capacity scales with contribution volume automatically.

4. **Random post-merge audits (spot checks).** A fixed fraction of merged changes (say 10 %, higher on red)
   is re-reviewed later by another sortition-drawn, deeper audit. Audits are where detection of subtle
   backdoors mostly happens; their randomness means an attacker cannot predict which merge will be audited.

5. **Retroactive accountability cascade.** When an audit (or a user, or a tool) finds a planted backdoor:
   the author is expelled; **every other merged change by that author is queued for audit** (the attacker's
   earlier "good" work is now suspect); every reviewer who attested the bad change loses reviewer
   eligibility for a period and has their own attestations sampled for re-audit. Honest mistakes (bugs) cost
   little; a backdoor someone approved costs the approver measurable standing. This makes rings expensive:
   one caught member exposes the rest through their shared attestations.

6. **Trust = measured detection accuracy, not tenure.** Reviewer standing is updated from outcomes: bugs
   later found in changes you approved lower it, defects you caught raise it. Only reviewers in the top
   band can be the second (gate) reviewer on red. Authors' own trust score only controls how many PRs they
   may have open at once (rate-limit, anti-flood), never review depth.

7. **Join/leave freely.** Newcomers can submit immediately (green, low concurrency) and are drawn as
   reviewers after a few accepted reviews; leaving drops you from the draw pool — there is no reviewer
   that work waits on, since any online person can be drawn. Pending reviews of someone who goes offline
   time out and are redrawn.

8. **Long-standing members (seed 2 %)** are not gatekeepers; they are the initial "gate reviewer" pool for
   red until measured accuracy produces more, and they are drawn like everyone else.

## Why fast
- No central queue: review capacity = whole online population, drawn in parallel.
- One review on green, merges within hours; review credit keeps supply ≈ demand.
- Audits run asynchronously after merge, so they never block shipping.

## Why safe
- An attacker's trust does not reduce the review its backdoor receives.
- Collusion needs the ring to be drawn as each other's reviewers: ≈ r/N per draw.
- Undetected-at-merge backdoors still face random audits, and once one is found every other merge by that
  author is audited (cascade), so the long-game attacker's earlier plants are found too.
- Cost: more total review hours than a trust-gated scheme (no free passes). That is the deliberate trade.

---

## Phase 2 — what the simulation says (added after opening `sim/`)

### How `mine` is expressed in `sim/trustsim.py`
(`design="mine"`, knobs in the `MINE` dict; `proposal` is byte-for-byte unchanged in behaviour, verified by diffing
its output against the original file `sim/trustsim_orig.py`.)
- Tier comes from the files, not from the author: every author gets the project mix (50/35/15 green/amber/red).
- Pre-merge reviews: green 1, amber 2, red 2 with one "gate" reviewer (seed member or ≥2 canaries at ≥0.6
  hit-rate posterior); if no gate reviewer is free within 6 h any qualified reviewer takes it (no jam).
- Reviewers drawn at random from idle, non-suspended, canary-qualified people; rotation rule: you never review
  the same author twice (blind anti-ring filter); canaries go to the least-measured reviewers first.
- Post-merge audits (non-blocking, any qualified person): green 100 %, amber 50 %, red 5 % (`mine`);
  `mine-balanced` uses 60/30/5.
- Backdoor found → author banned + all their merges reverted and their merges/approvals audited (same as
  proposal); approvers are **not** slashed: suspended from reviewing 7 days, half their past approvals re-audited.
- Attack clock: trust is not used for anything except author bookkeeping, so attackers start after the
  proposal-equivalent activity (+1 per merge, +0.2 per review/canary, +0.5 per audit, ≥ attack_trust=5).

### Results (5 seeds)
| metric | proposal | mine | mine-balanced |
|---|---|---|---|
| merged/day (last 7 d) | 2268 | 2020 | 2142 |
| median hours to merge | 11 | **7** | **7** |
| backdoor attempts | 139 | 125 | 120 |
| stopped in review | 41 | 47 | 44 |
| merged | 67 | 75 | 74 |
| found later by audit | 0 | 18 | 11 |
| reverted with author | 12 | 15 | 16 |
| still live at end | 55 | **43** | 46 |
| backdoor-days live | 471 | **401** | 401 |
| canaries caught % | 69 | 70 | 69 |
| bad actors banned | 39 | 63 | 55 |
| honest banned | 0 | 0 | 0 |
| abandoned leases | 41002 | 44759 | 41444 |
| reviews waiting at end | 1350 | 148 | 162 |

### What the simulation taught me
1. **Detection per checking-hour is the binding constraint.** A subtle green backdoor (vis ≈ 0.26) is caught
   by one review ~14 % and by one audit ~26 % of the time. Every variant I tried (1–3 reviewers, audits
   0–100 %, canaries every 5–20, skill-measuring canaries, gated auditors, short leases) lands at
   **35–39 % of attempts still live at the end** (proposal: 37 %). Rules blind to who is malicious can only
   trade throughput for check-hours; they cannot aim checks at attackers.
2. **Post-merge audits beat a second pre-merge review** for the same hours: same detection per hour, but no
   latency (11 h → 7 h) and they never block merging. Proposal's audits never ran (level-2 auditors are scarce,
   1350 slots queued, 0 found); proposal's red queue also jams, which "hides" ~30 attempts as never-merged —
   safe, but honest red work stalls too.
3. **"No earned bypass" had no measurable edge** here: the proposal does not give trusted authors less review
   either, and random reviewer draws already make the ring size irrelevant (ring-mates are almost never drawn).
4. Canary-based filtering of lazy reviewers and skill-based auditor selection gave no significant gain in
   10-seed runs (noise ±5 live backdoors): too few canary trials per person in 30 days to tell people apart.
5. Throughput and absolute safety are coupled: more merges → attackers reach their attack threshold sooner →
   more attempts. Compare designs per attempt, not only absolute counts.

### Not expressible in the simulator
Blast-radius tiering by file (tier is drawn at random), the attestation graph exposing rings through shared
approvals (rings never meet under random draws), reviewer-credit economy details, patient attackers who adapt to
audit rates, sandboxing/capabilities that would raise visibility or limit damage of green changes. The detection
model is fixed, so the design levers that would really change the picture (making backdoors more visible
through smaller diffs, reproducible builds, capability isolation) cannot be credited.
