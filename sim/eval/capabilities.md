# Capabilities, task supply and serial blockers

Questions:
1. Not everyone can do every task. Example: a GTA V port where most tasks need Windows, owning the game and a
   strong GPU, while shader replay from dumped `.req` files, unit tests and docs don't. How should claiming and
   reviewing work?
2. Who should create tasks?
3. In a project where each blocker hides the next (the game stops at its first crash), how can work go in
   parallel?

## 1. Capabilities

**Model.** `capsim.py` (see `make_capsim.py`): 1,000 people over 90 days, with the recommended lease. Results are
40 seeds per row.

Members:
- 15 % have the full set (Windows + game + GPU);
- 40 % have Windows only;
- 45 % have none of the three.

Task requirements:
- 60 % of tasks need the full set;
- 15 % need Windows;
- 25 % need nothing.

Members claim only tasks they can do, highest requirement first. Code review never needs the game unless a
variant restricts it.

Variants:
- **(a)** reviews open to everyone;
- **(b)** reviews restricted to members whose claimed capability covers the task;
- **(c) artifact capture:** 70 % of full-set tasks become three steps:
  - a full-set member captures the repro artifacts (logs, shader dumps, memory dumps; 1-2 h);
  - anyone does the work without the game (taking 20 % longer);
  - a full-set member runs the verification (0.5-1.5 h);
- **(d) unverifiable claims:**
  - 10 % of members without the full set claim it anyway; they get stuck, lose the lease at the first-push
    deadline, and stop lying half the time;
  - every attacker claims the full set.

**Metrics:**
- "Idle claim attempts": the share of tries by an idle online member that found nothing they could claim.
- "Shipped per attempt": the share of backdoor attempts still undetected after the 14-day soak.

| Variant | Merged/day | Median h to merge | Feature p95 (d) | Idle claim attempts | Full-set tasks waiting | Wait for full-set task med / p95 (h) | Liar failed claims | Careless review share | Backdoor reviews by bad actors | Backdoor attempts /run | Shipped per attempt | Shipped /10k (90 % CI) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| no capabilities (everyone can do everything) | 236 ± 1 | 2.0 | 33.3 ± 0.2 | 1 % | 0 | – | 0 | 6.2 % | 1.6 | 83.5 | 17.9 % | 8.4 (7.6-9.4) |
| (a) claim matching, reviews open | 82 ± 2 | 2.0 | 35.4 ± 0.5 | 96 % | 102 | 1 / 169 | 0 | 5.8 % | 1.2 | 39.2 | 24.6 % | 15.6 (13.5-17.6) |
| (b) claim matching, reviews restricted | 61 ± 1 | 2.1 | 38.0 ± 0.6 | 97 % | 111 | 2 / 228 | 0 | 18.9 % | 0.6 | 31.5 | 29.2 % | 19.6 (17.5-21.8) |
| (c) (a) + artifact capture 70 % | 135 ± 3 | 2.0 | 39.8 ± 0.3 | 81 % | 112 | 0 / 115 | 0 | 5.0 % | 1.6 | 65.6 | 24.4 % | 16.0 (14.7-17.4) |
| (c) + restricted reviews | 115 ± 3 | 2.0 | 40.7 ± 0.4 | 86 % | 120 | 0 / 134 | 0 | 14.8 % | 1.3 | 61.6 | 29.4 % | 21.0 (19.1-23.0) |
| (d) (a) + liars | 84 ± 2 | 2.0 | 38.6 ± 0.5 | 95 % | 121 | 0 / 179 | 753 | 5.9 % | 0.5 | 105.2 | 14.8 % | 23.8 (21.1-27.3) |
| (d) (b) + liars | 65 ± 2 | 2.0 | 43.9 ± 0.8 | 96 % | 141 | 1 / 274 | 704 | 18.0 % | 8.4 | 103.0 | 22.5 % | 46.0 (42.9-49.3) |
| (d) (c) + liars | 139 ± 3 | 2.0 | 40.3 ± 0.3 | 77 % | 118 | 0 / 109 | 750 | 5.0 % | 1.5 | 73.6 | 21.1 % | 14.7 (13.4-15.9) |
| supply: maintainers only | 162 ± 4 | 2.0 | 32.5 ± 0.3 | 88 % | 0 | – | 0 | 4.9 % | 1.4 | 66.9 | 22.0 % | 13.9 (12.6-15.3) |
| supply: anyone proposes (1-4 h review) | 235 ± 2 | 2.0 | 33.4 ± 0.2 | 6 % | 0 | – | 0 | 6.3 % | 1.6 | 84.4 | 18.9 % | 9.0 (8.2-9.8) |
| (c) + supply: maintainers only | 138 ± 3 | 2.0 | 38.2 ± 0.3 | 83 % | 63 | 0 / 78 | 0 | 5.1 % | 1.3 | 62.0 | 23.2 % | 15.3 (13.7-16.9) |
| (c) + supply: anyone proposes | 133 ± 3 | 2.0 | 39.6 ± 0.3 | 81 % | 108 | 0 / 110 | 0 | 5.1 % | 1.8 | 67.2 | 23.2 % | 15.7 (14.1-17.4) |

**Notes on the security columns:**
- Shipped per 10k merges rises in (a) partly because throughput falls while attackers keep trying. The
  per-attempt leak isolates review quality.
- "Backdoor reviews by bad actors" counts reviews of backdoored changes done by attackers, per run.

**Conclusion:**
1. **Capabilities are the bottleneck, not trust.** With 15 % full-set members and 60 % full-set tasks,
   throughput falls from 236 to 82 merged/day:
   - 96 % of idle members' claim attempts find nothing;
   - about 100 full-set tasks wait at any time, with a p95 wait of 7 days.
   The full-set members bound throughput.
2. **Artifact capture is the big lever: 82 → 135 merged/day (+65 %).** Full-set people spend 2-3 h per task
   instead of 4-16 h, and idle claim attempts fall from 96 % to 81 %. It is also the right fix for unverifiable
   claims: work that needs no game can't be faked by lying about owning the game, and the full-set steps are
   short and checkable. With liars, (d)(c) still does 139/day and its per-attempt leak is 21 %.
3. **Never restrict reviews to capabilities.**
   - Restricted reviews (b) cost 25 % of throughput. Careless reviews rise from 6 % to 19 %, because
     calibration has far fewer people to choose from, and the per-attempt leak rises from 25 % to 29 %.
   - With unverifiable claims (d)(b), attackers who claim the rare capability land in the small pool:
     - 8.4 reviews of backdoors by bad actors per run, against 0.5 with open reviews;
     - shipped backdoors double, 24 → 46 per 10k merges.
   - Code review needs the diff, not the game. What does need the game is the verification run, and (c) makes
     it a separate short task.
4. **Unverifiable claims mostly waste time.** 750 stuck claims per run lose about 24 h each. They also let
   attackers claim full-set tasks, which are 60 % of the work: with open reviews, shipped backdoors rise 16 →
   24 per 10k. Fixes:
   - turn capability claims into evidence: a first push must include a run log from the game;
   - keep reviews open, and keep the per-attempt leak low (15 % in (d)(a)).

## 2. Who creates tasks

Same model: no capabilities, or capabilities with artifact capture.
- **Maintainers only:** 10 maintainers write 4 tasks per online hour.
- **Anyone proposes:** a proposed task is reviewed like a green change, with a 1-4 h delay.
- **Auto:** tasks come from failures immediately; this is the default in every other run.

From the table above:
- **Maintainers only costs 31 % of throughput** (236 → 162 merged/day) and leaves 88 % of idle claim attempts
  with nothing to take. Its higher shipped-per-10k (13.9) is again fewer merges for the same attempts.
- **Anyone proposes ≈ auto** (235 vs 236). The 1-4 h review of a proposal costs almost nothing, because there is
  always a backlog.
- With capabilities and artifact capture, supply doesn't matter (133-138). The full-set members are the
  bottleneck, not task writing.
- **Recommendation:** anyone can propose or split a task. A proposal is reviewed like a green change. Failures
  of automated runs become tasks automatically.

## 3. Serial blockers

**Model.** `blockers.py`: 100 or 1,000 members over 90 days, 20 seeds.
- Each blocker hides the next. Revealing blocker j needs every earlier one fixed or bypassed, then a 1 h run by
  a full-set member.
- A fix takes 4-16 h, and 60 % of fixes need the full set. A fix merges 1-4 h after the work.
- **Bypass depth D:** a full-set member writes a temporary workaround (2-4 h; 70 % of blockers can be bypassed)
  to reveal the next blocker. At most D blockers can be known and unfixed at once. When a bypassed blocker's
  real fix lands, each later fix is reopened with 10 % probability.
- **Relay:** when the holder of a fix goes offline, the fix goes back to the pool with its branch, at a cost of
  1 h plus 20 % of the work done, so whoever is online continues it.

**Bypass depth and relay** (task supply auto, no artifact capture):

| Members | Bypass depth | Task supply | Artifact capture | Relay | Blockers fixed in 90 d | Blockers worked in parallel | Reopened by bypass | Idle online hours | Full-set members busy |
|---|---|---|---|---|---|---|---|---|---|
| 100 | none | auto | no | no | 32.6 ± 1.7 | 0.9 | 0.0 | 99 % | 4 % |
| 100 | none | auto | no | yes | 61.4 ± 2.3 | 0.9 | 0.0 | 97 % | 13 % |
| 100 | 3 | auto | no | no | 58.5 ± 3.0 | 1.8 | 2.5 | 98 % | 10 % |
| 100 | 3 | auto | no | yes | 95.7 ± 4.1 | 1.5 | 3.5 | 94 % | 23 % |
| 100 | 5 | auto | no | no | 64.8 ± 3.7 | 1.9 | 4.8 | 97 % | 11 % |
| 100 | 5 | auto | no | yes | 99.8 ± 4.9 | 1.5 | 5.2 | 94 % | 24 % |
| 100 | 10 | auto | no | no | 63.5 ± 3.9 | 1.9 | 4.7 | 97 % | 11 % |
| 100 | 10 | auto | no | yes | 100.5 ± 5.2 | 1.5 | 5.1 | 94 % | 24 % |
| 1000 | none | auto | no | no | 32.2 ± 1.6 | 0.9 | 0.0 | 100 % | 1 % |
| 1000 | none | auto | no | yes | 60.6 ± 2.5 | 0.9 | 0.0 | 100 % | 2 % |
| 1000 | 5 | auto | no | no | 67.8 ± 3.7 | 1.9 | 4.5 | 100 % | 1 % |
| 1000 | 5 | auto | no | yes | 99.6 ± 4.9 | 1.5 | 4.2 | 99 % | 3 % |

**Task supply** (100 members, relay on):

| Members | Bypass depth | Task supply | Artifact capture | Relay | Blockers fixed in 90 d | Blockers worked in parallel | Reopened by bypass | Idle online hours | Full-set members busy |
|---|---|---|---|---|---|---|---|---|---|
| 100 | none | auto | no | yes | 61.4 ± 2.3 | 0.9 | 0.0 | 97 % | 13 % |
| 100 | none | maintainers | no | yes | 49.6 ± 1.8 | 0.9 | 0.0 | 97 % | 11 % |
| 100 | none | propose | no | yes | 55.9 ± 2.1 | 0.9 | 0.0 | 97 % | 12 % |
| 100 | 5 | auto | no | yes | 99.8 ± 4.9 | 1.5 | 5.2 | 94 % | 24 % |
| 100 | 5 | maintainers | no | yes | 88.8 ± 4.0 | 1.6 | 4.0 | 95 % | 22 % |
| 100 | 5 | propose | no | yes | 91.5 ± 4.5 | 1.5 | 4.0 | 95 % | 22 % |

**Artifact capture on the critical path** (100 members, relay on):

| Members | Bypass depth | Task supply | Artifact capture | Relay | Blockers fixed in 90 d | Blockers worked in parallel | Reopened by bypass | Idle online hours | Full-set members busy |
|---|---|---|---|---|---|---|---|---|---|
| 100 | none | auto | no | yes | 61.4 ± 2.3 | 0.9 | 0.0 | 97 % | 13 % |
| 100 | none | auto | yes | yes | 47.6 ± 2.0 | 0.9 | 0.0 | 97 % | 8 % |
| 100 | 5 | auto | no | yes | 99.8 ± 4.9 | 1.5 | 5.2 | 94 % | 24 % |
| 100 | 5 | auto | yes | yes | 86.7 ± 3.5 | 1.7 | 4.2 | 94 % | 16 % |

**Conclusion:**
1. **A serial project does not scale with people.** About 32 blockers in 90 days with either 100 or 1,000
   members, and 97-100 % of online hours idle. One person fixes one blocker at a time, and part-time people
   stretch a 10 h fix over 2-3 days.
2. **Relay doubles the pace** (32 → 61) by handing the critical fix to whoever is online. On the critical path,
   handover cost is worth paying. Rule: the oldest unfixed blocker is relayed, not held, whenever its holder goes
   offline.
3. **Bypass hacks double it again.**
   - Depth 3: ×1.8; depth 5: ×2.0. Depth beyond 5 gives nothing, because only 70 % of blockers can be bypassed
     and each bypass takes a run. Parallel blockers rise from 0.9 to about 2.
   - Relay plus depth 5: 32 → 100 blockers in 90 days (×3.1). About 5 % of fixes are reopened when the real fix
     of a bypassed blocker lands.
   - Rule: bypasses are tagged `hack(...)` changes that never ship. A release needs every bypass replaced by its
     fix, like the gtaV branch's `hack(gtav)` commits.
4. **Artifact capture slows a serial chain** (61 → 48, and 100 → 87). It adds two steps and a review to the
   critical path. Use it for parallel work, not for the current blocker.
5. **Task supply matters on the critical path.** With relay, auto gives 61, propose 56 and maintainers-only 50.
   Every hour between a crash and its task is lost. So the run that reveals a blocker should create the task
   itself, from its log.
