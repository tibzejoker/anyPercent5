# Lease parameters: a benchmark of first push, renewal and cap

Question: what are the best values for the three lease parameters?
- **first push due** (F): currently 24 h after the claim;
- **renewal per push** (R): currently 72 h;
- **hard cap** (C): currently 7 days.

The answer must hold across several kinds of population, not just one.

## Model

`make_lease_sim.py` builds `lease_sim.py` from `anyp5_sim.py`, with the lease of `anyp5/engine.py` v0.2 made
explicit. The project has 1,000 people over 90 days. Work comes as features of 20 tasks with prerequisites; 2
core tasks are needed by most of the others and are claimed first. Throughput, review and attackers are as in
the any%5 synth sim.

**The lease:**
- The first pushed commit is due F hours after the claim.
- Each hour of work an honest holder does is a push, which renews the lease to now + R.
- The lease never runs past claim + C. At the cap, the task is reassigned with its branch.
- **Extension variant (ext):** at the cap, a holder who pushed in the last 48 h keeps the task for another
  window once a reviewer confirms the progress is real. A fake pusher's "progress" is caught 80 % of the time.

**Handover:** when a task changes hands, the next holder pays a ramp-up of 2 h plus 20 % of the work already
done on it. A sensitivity run varies this cost (see below).

**Holder behaviours:**
- **honest:** works while online, about 17 % of the time.
- **silent:** stops. Half never start; the others stop part way through.
- **zombie:** sends heartbeats but never pushes.
- **fake pusher:** pushes token commits forever and never finishes. This is the case only a cap can stop.

**Worlds:**
- **honest:** 0 % silent;
- **silent 20 % + zombie 5 %**;
- **silent 50 %**;
- **weekend-only half:** half the people are online only on weekends, so gaps run about 5 days;
- **big tasks:** 10 % of tasks need 40-120 h of work, i.e. 2-4 weeks for a part-time contributor;
- **fake pushers 5 %.**

The last three worlds also have 5 % silent holders.

**Sweep:**
- Coarse grid, 8 seeds: F ∈ {6, 12, 24, 48, 72} h, R ∈ {24, 48, 72, 120} h, C ∈ {3, 5, 7, 10, 14 days, none}.
- Refine, 24-48 seeds: the best rules, their extension variants, and F ∈ {24, 36, 48} h, R ∈ {72, 96} h,
  C ∈ {4, 5, 6} days.
- Sensitivity: 3 settings × 5 finalist rules, 16 seeds.
- 16,700 runs in total.

**Score:** for each world, a rule's regret is the worse of two gaps against the best rule in that world:
- p95 feature delay (delay ÷ best − 1);
- throughput (best ÷ merged per day − 1).

The robust rule minimises its worst-world regret. ± is the 95 % CI half-width.

Reproduce:

```
python make_lease_sim.py
python lease_sweep.py coarse
python lease_sweep.py refine
python lease_sweep.py extra
python lease_sweep.py report
```

## Recommended rule vs the current one

| World | Rule | Merged/day | Feature delay med (d) | p95 (d) | Unfinished after 30 d | Honest leases lost /1k claims | Handover overhead | Big tasks unfinished |
|---|---|---|---|---|---|---|---|---|
| honest | current 24 h / 72 h / 7 d | 235 ± 2 | 20.4 | 31.9 ± 0.2 | 0.5 % | 120 | 5.4 % | – |
| honest | **recommended 24 h / 72 h / 5 d + ext** | 238 ± 1 | 20.3 | 31.6 ± 0.2 | 0.5 % | 119 | 5.3 % | – |
| silent 20 % + zombie 5 % | current 24 h / 72 h / 7 d | 229 ± 2 | 26.2 | 40.7 ± 0.2 | 2.6 % | 120 | 9.3 % | – |
| silent 20 % + zombie 5 % | **recommended 24 h / 72 h / 5 d + ext** | 229 ± 1 | 26.1 | 40.8 ± 0.3 | 2.7 % | 119 | 9.2 % | – |
| silent 50 % | current 24 h / 72 h / 7 d | 216 ± 1 | 38.8 | 61.9 ± 0.3 | 15.8 % | 120 | 18.6 % | – |
| silent 50 % | **recommended 24 h / 72 h / 5 d + ext** | 217 ± 1 | 38.6 | 61.6 ± 0.3 | 15.8 % | 119 | 18.5 % | – |
| weekend-only half | current 24 h / 72 h / 7 d | 185 ± 1 | 25.6 | 38.6 ± 0.3 | 1.7 % | 214 | 10.8 % | – |
| weekend-only half | **recommended 24 h / 72 h / 5 d + ext** | 187 ± 1 | 25.7 | 38.4 ± 0.2 | 1.7 % | 215 | 10.8 % | – |
| big tasks 10 % (40-120 h) | current 24 h / 72 h / 7 d | 89 ± 1 | 50.9 | 89.9 ± 0.0 | 63.0 % | 279 | 50.7 % | 55 % |
| big tasks 10 % (40-120 h) | **recommended 24 h / 72 h / 5 d + ext** | 154 ± 1 | 39.2 | 76.7 ± 0.6 | 25.7 % | 146 | 14.3 % | 17 % |
| fake pushers 5 % | current 24 h / 72 h / 7 d | 235 ± 1 | 25.3 | 40.6 ± 0.3 | 2.6 % | 120 | 6.3 % | – |
| fake pushers 5 % | **recommended 24 h / 72 h / 5 d + ext** | 236 ± 1 | 24.8 | 39.9 ± 0.2 | 2.3 % | 118 | 6.2 % | – |

The current rule's p95 of 89.9 d in the big-task world is the 90-day horizon: many features never finish.

## Ranking (worst-world regret, refined rules)

Per-world columns are regret in %.

| Rule | n seeds | Worst-world regret | Mean regret | honest | silent 20 % + zombie 5 % | silent 50 % | weekend-only half | big tasks 10 % (40-120 h) | fake pushers 5 % | Honest leases lost /1k (honest / weekend) |
|---|---|---|---|---|---|---|---|---|---|---|
| 12 h / 72 h / 5 d + ext | 24 | 7.5 % | 5.3 % | 3 | 5 | 6 | 4 | 8 | 7 | 180 / 258 |
| 24 h / 72 h / 4 d + ext | 24 | 8.0 % | 5.7 % | 3 | 8 | 7 | 5 | 8 | 2 | 123 / 215 |
| 6 h / 72 h / 5 d + ext | 24 | 8.1 % | 5.3 % | 3 | 4 | 6 | 4 | 8 | 6 | 230 / 301 |
| **24 h / 72 h / 5 d + ext (recommended)** | 48 | 8.5 % | 6.3 % | 4 | 8 | 8 | 6 | 6 | 6 | 119 / 215 |
| 36 h / 72 h / 5 d + ext | 24 | 12.6 % | 8.5 % | 5 | 13 | 12 | 9 | 7 | 6 | 86 / 193 |
| 24 h / 72 h / 7 d + ext | 24 | 16.9 % | 8.5 % | 5 | 8 | 8 | 7 | 6 | 17 | 115 / 210 |
| 48 h / 72 h / 5 d + ext | 24 | 17.6 % | 10.4 % | 6 | 15 | 18 | 10 | 7 | 7 | 69 / 183 |
| 24 h / 120 h / 5 d + ext | 24 | 22.1 % | 13.1 % | 9 | 19 | 22 | 15 | 3 | 10 | 89 / 177 |
| 24 h / 120 h / 10 d | 32 | 28.9 % | 23.0 % | 17 | 26 | 29 | 18 | 24 | 25 | 77 / 152 |
| 24 h / 72 h / 10 d + ext | 24 | 34.8 % | 11.6 % | 5 | 9 | 8 | 7 | 5 | 35 | 114 / 210 |
| 24 h / 48 h / 5 d + ext | 24 | 37.0 % | 12.3 % | 6 | 8 | 9 | 7 | 37 | 8 | 213 / 282 |
| 24 h / 72 h / 14 d + ext | 24 | 59.6 % | 15.5 % | 5 | 9 | 7 | 7 | 6 | 60 | 114 / 210 |
| 24 h / 72 h / 7 d (current) | 32 | 84.4 % | 20.0 % | 5 | 8 | 8 | 7 | 84 | 7 | 120 / 214 |

Without a cap (24 h / 72 h / none, 8 seeds), fake pushers hold their tasks until they leave the project, and
p95 feature delay rises from 39.9 to 75.3 days.

## Sensitivity (16 seeds per world)

- **Cheap handovers** (1 h + 5 % of the work done):
  - 24 h / 72 h / 5 d + ext has a worst-world regret of 3.5 %;
  - the current rule is fine there (4.4 %), because reassigning a big task costs little.
- **Expensive handovers** (4 h + 40 %):
  - the current rule collapses on big tasks: 46 merged/day against 119-132, and 187 % regret;
  - with the extension, every finalist stays within 4.5-11 %;
  - 24 h / 72 h / 5 d + ext: 11 %.
- **Weaker progress check** (fake progress caught 50 % of the time instead of 80 %):
  - 5-day caps stay best: 21-22 % regret, against 41 % for 7 d + ext and 79 % for the current rule;
  - the p95 delay with fake pushers rises from 40 to 49 days, so the cap matters more when the check is weak.

## Conclusion

1. **The optimum is flat on first push and renewal.**
   - F between 6 and 24 h changes p95 delay by about 1-3 %, which is within noise.
   - 24 h loses half as many honest leases as 6 h (119 against 230 per 1,000 claims), so 24 h stays.
   - 36-48 h makes features wait 5-18 % longer when holders go silent.
   - R = 72 h is clearly best:
     - 48 h drops honest part-timers between sessions, costing 21 % of throughput with big tasks;
     - 120 h lets silent holders sit 2 more days, so p95 is 15-22 % worse when 20-50 % go silent.
2. **The cap is the parameter that matters, and it only works with the extension.**
   - Without extension, a 7-day cap takes big tasks away from honest people who are progressing: 55 % of big
     tasks are never finished, throughput is 89 against 154 per day, and 51 % of the work goes to handovers.
   - With the extension, the cap only stops holders who don't show real progress, so it can be shorter.
     5 days beats 7 against fake pushers (p95 39.9 against 44.2 days) at no cost elsewhere.
   - 10-14 days, or no cap, lets fake pushers stall features: p95 51 to 75 days.
3. **Recommended defaults: first push 24 h, renewal 72 h per push, cap 5 days, extended while a reviewer
   confirms progress.**
   - Worst-world regret is 8.5 %, against 84 % for the current rule. The best rule found is 7.5 %
     (12 h / 72 h / 5 d + ext), within noise, but it loses 50 % more honest leases.
   - Against the current 24 h / 72 h / 7 d, the gain is the same or slightly better everywhere, and large with
     big tasks: +73 % throughput (154 against 89 per day), unfinished features 26 % against 63 %, big tasks
     never finished 17 % against 55 %.
   - Against fake pushers: p95 39.9 against 40.6 days.
4. **Weekend-only contributors lose about 1 lease in 5**, because their gaps between sessions exceed 72 h.
   Throughput and delay barely move, because the branch is handed over, but it is frustrating. A cheap fix,
   not modelled: the holder declares an absence (`anyp5 away 5d`), which extends R once, within the cap.
5. **Limits.** Behaviour shares and handover costs are assumptions. The ranking held across the handover and
   detection settings tested, but absolute delays depend on them. The extension needs a reviewer action at
   each cap (5 days per long task); that load is not counted in throughput.
