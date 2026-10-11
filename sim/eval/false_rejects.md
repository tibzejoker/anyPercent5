# False rejects: the cost of rejecting honest work

The red team found that the simulator treated rejecting as free, so the "reject everything" break could not show up
in it. This run models false rejects and compares the baseline with any%5 under its v0.2 and v0.3 review rules.

## Command, setup, seeds

```sh
cd sim/eval
python false_rejects.py 10000 90 5              # 94 s wall on 32 threads, slowest single run 51 s
python false_rejects.py 10000 90 5 --ablation   # 42 s wall, slowest run 41 s
python false_rejects.py --report [--ablation]   # reprint a table from the json
```

- Model: `anyp5_sim.py` (same population, presence, durations and detection as docs/RESULTS.md), patched by
  `false_rejects.py` into `build/anyp5_fr.py`. The new rules are off unless a scenario turns them on.
- 10,000 members, 90 days, seeds 1-5. Mean of 5 seeds. Standard attacker only: malicious 3 days after joining.
  "sd" is the standard deviation across seeds.
- Raw results: `false_rejects.json` and `false_rejects_ablation.json`.

What is new in the model:

- **Honest false rejects.** An honest reviewer who found nothing wrong still rejects with probability 3 %, or
  10 % for a careless reviewer. These rates are assumptions, not measurements.
- **Reject-all ring.** 5 % of members, in rings of 5. A ring member rejects every real change that its ring did
  not write, and approves its own ring's changes. Canaries look like real changes, so it treats them the same
  way: it searches each one for the planted line and rejects it anyway. In the last scenario, its rejects also
  claim a backdoor.
- **Cost of a rejection.** A closed change goes back to its author. The author reworks it, which takes 0.3x a
  fresh implementation time, and resubmits it at most twice. After that, or if the author has left, the work is
  lost.
- **Baseline rules** (trust levels + review credits): one reject closes the change. The baseline has no rule for
  backdoor claims, so a claim counts as a plain reject. Its canaries are one-sided: any reject counts as a catch.
- **any%5 v0.2 rules:** one-sided canaries (any reject of a planted canary counts as a catch), and reviews go to
  the best-calibrated idle members first. One reject closes the change. A backdoor claim goes to 2 auditors,
  also drawn best-ranked first, and the author is banned if both confirm.
- **any%5 v0.3 rules**, checked against `anyp5/engine.py`:
  - canaries: 40 % are clean and count only if approved, and a planted canary counts only if the reject names its
    line (`score_canary`);
  - score: (hits + 1) / (seen + 2) x (clean approved + 1) / (clean seen + 2) (`score`);
  - draw: at random among the better-calibrated half of the members who are still idle (`draw`);
  - canaries go to the least-tested members first (`mix_canaries`);
  - rejects: a lone reject asks for one more approval, and 2 rejects close a change (`needed`, `verdict`);
  - backdoor claims: a claim holds the change until 2 auditors answer. They are drawn the same way, and the author
    is banned if both confirm (`escalate`, `check_escalation`).

Columns:
- *Honest clean PRs rejected*: the share of PR versions by honest authors, with no defect, that were closed.
- *With at least one reject*: the same, including lone rejects that only asked for one more review.
- *Honest work lost*: the share of honest work items that never merged (rework limit reached, or author gone).
- *Honest work hours*: from the first submission to the merge, including rework and resubmissions.

## Results

| Population | System | Merged/day | Median h to merge (one PR) | Honest work: median / p90 h to merge | Honest clean PRs rejected | ... with at least one reject | Honest work lost | Ring share of real reviews | Honest members banned | Backdoors shipped per attempt (14-day soak) |
|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|
| no false rejects (old model) | Baseline: trust levels + credits | 2280 | 11.0 | 13.0 / 44 | 0.0 % | 0.0 % | 0.86 % | 0.0 % | 0.0 | 25.6 % (sd 0.1) |
| no false rejects (old model) | any%5, v0.2 review rules | 2405 | 2.0 | 2.0 / 32 | 0.0 % | 0.0 % | 0.70 % | 0.0 % | 0.0 | 15.0 % (sd 2.4) |
| no false rejects (old model) | any%5, v0.3 review rules | 2370 | 2.0 | 2.0 / 29 | 0.0 % | 0.0 % | 0.40 % | 0.0 % | 0.0 | 21.2 % (sd 1.3) |
| honest false rejects 3 % / 10 % | Baseline: trust levels + credits | 2180 | 11.0 | 15.0 / 58 | 8.5 % | 8.5 % | 2.42 % | 0.0 % | 0.0 | 25.7 % (sd 0.8) |
| honest false rejects 3 % / 10 % | any%5, v0.2 review rules | 2352 | 2.0 | 2.2 / 40 | 3.9 % | 3.9 % | 1.38 % | 0.0 % | 0.0 | 16.0 % (sd 1.7) |
| honest false rejects 3 % / 10 % | any%5, v0.3 review rules | 2332 | 3.0 | 3.0 / 32 | 0.4 % | 4.4 % | 0.53 % | 0.0 % | 0.0 | 20.3 % (sd 1.6) |
| + reject-all ring, 5 % of members | Baseline: trust levels + credits | 2032 | 11.0 | 17.6 / 88 | 17.7 % | 17.7 % | 4.61 % | 5.1 % | 0.0 | 23.2 % (sd 1.2) |
| + reject-all ring, 5 % of members | any%5, v0.2 review rules | 2103 | 2.0 | 6.0 / 80 | 19.5 % | 19.5 % | 5.19 % | 14.4 % | 0.0 | 15.9 % (sd 1.3) |
| + reject-all ring, 5 % of members | any%5, v0.3 review rules | 2325 | 3.0 | 3.0 / 34 | 0.8 % | 6.5 % | 0.63 % | 1.9 % | 0.0 | 21.4 % (sd 0.3) |
| + reject-all ring that claims backdoors | Baseline: trust levels + credits | 2032 | 11.0 | 17.6 / 88 | 17.7 % | 17.7 % | 4.61 % | 5.1 % | 0.0 | 23.2 % (sd 1.2) |
| + reject-all ring that claims backdoors | any%5, v0.2 review rules | 2048 | 3.8 | 6.0 / 55 | 4.8 % | 16.2 % | 1.92 % | 11.6 % | 452.0 | 16.4 % (sd 1.8) |
| + reject-all ring that claims backdoors | any%5, v0.3 review rules | 2294 | 3.0 | 3.0 / 36 | 0.5 % | 6.6 % | 0.61 % | 2.0 % | 2.6 | 20.7 % (sd 1.3) |

Ablation: v0.3 with one rule reverted at a time, with honest false rejects and no ring.

| System | Merged/day | Median h | Honest clean PRs rejected | Honest work lost | Backdoors shipped per attempt |
|---|---:|---:|---:|---:|---:|
| any%5, v0.2 review rules | 2352 | 2 | 3.9 % | 1.38 % | 16.0 % (sd 1.7) |
| any%5, v0.3 review rules | 2332 | 3 | 0.4 % | 0.53 % | 20.3 % (sd 1.6) |
| v0.3 but ranked draw (best first) | 2339 | 3 | 0.4 % | 0.52 % | 19.0 % (sd 0.4) |
| v0.3 but one-sided canaries | 2361 | 2 | 0.3 % | 0.51 % | 19.2 % (sd 2.6) |
| v0.3 but one reject closes | 2305 | 2 | 4.5 % | 1.58 % | 18.1 % (sd 1.8) |
| v0.3 but canaries confirm perfect records first (v0.2 routing) | 2348 | 2 | 0.4 % | 0.53 % | 18.6 % (sd 2.6) |

## What this shows

- **The v0.3 rules contain the reject-all ring.** The ring is 5 % of members but does 1.9 % of real reviews under
  v0.3, against 14.4 % under v0.2 and 5.1 % in the baseline. Under v0.3 it gets 0.8 % of honest changes closed,
  against 19.5 % under v0.2 and 17.7 % in the baseline. This matches the engine proofs of concept in
  docs/REDTEAM.md (3.4 % of reviews, 1 % rejected).
- **v0.2 reproduces the red-team break.** With backdoor claims, best-ranked auditors are ring members: 452 honest
  members are banned on average. v0.3 draws auditors at random among the better half: 2.6 bans per run, from about
  5,400 claims. That is not zero. At this size (above 100 members), no maintainer confirms a ban.
- **What a reject costs.**
  - In the baseline, one reject closes a change, with 2-3 reviewers per change. Honest false rejects alone close
    8.5 % of honest changes and lose 2.4 % of honest work. Adding the ring takes that to 17.7 % and 4.6 %. Honest
    work then needs 18 h at the median and 88 h at p90, against 13 h and 44 h without rejects. Throughput drops by
    11 %.
  - Under v0.3, 4-7 % of honest changes get a reject. A lone reject only costs one more review, so the median goes
    from 2 h to 3 h, and only 0.4-0.8 % are closed. Throughput drops by 2 %.
- **Where any%5 looks worse: safety per attempt.**
  - With these rules, backdoors ship 20-21 % of attempts under v0.3, against 15-16 % under v0.2 and 23-26 % in the
    baseline. The rules that stop rejecters give back most of v0.2's per-attempt safety advantage.
  - The ablation shows no single cause. Each reverted rule recovers 1-2 points, and seed noise is about 1.5
    points. All of them spread reviews away from the best reviewers: a random draw among the top half, a calibration
    signal split with clean canaries, a second opinion instead of a close, and canaries spread over more members.
  - Rejecters also reject backdoors, so they lower the baseline's leak (25.7 % to 23.2 %). They are not a defence.

## Caveats

- The false-reject rates (3 % diligent, 10 % careless) and the rework model (0.3x, two resubmissions) are
  assumptions. With lower rates, the costs shrink in every design.
- Only the reject-all strategy is modelled. A stealthier ring could approve clean canaries and reject only real
  changes, if it could tell them apart. Pending-copy canaries are meant to prevent that. See canary_gaming.py and
  docs/REDTEAM.md, where the stealth ring got 6.7 % of reviews.
- Honest reviewers who catch a real backdoor still trigger the author ban at once, as in the original simulator,
  not through the 2-auditor escalation. A ring's rejects on post-merge reviews and audits are not modelled.
- Each population uses one attacker profile (standard), with 5 seeds. The per-attempt leak has an sd of about
  1.5 points across seeds.
- The headline table in docs/RESULTS.md comes from `crosseval.py` with v0.2 rules. It does not include these
  effects.
