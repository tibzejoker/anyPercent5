# Scaling: from 10 to 100,000 contributors

Question: how does any%5 behave from a small project up to a huge one?

## Model

- Simulator: any%5 synth (`anyp5_sim.py`), standard attacker profile (turns malicious 3 days after joining).
- Behaviour per person is the same at every size: about 17 % online, 20-day churn, 1 % lone attackers, 1 %
  colluders, 20 % careless reviewers. The project always has at least one founder member.
- Each run lasts 90 days. Seeds: 40 at N = 10, down to 1 at N = 100,000.
- Two variants:
  - `flat`: the engine as coded, where any member reviews any tier;
  - `synth`: the published simulation, where amber and red reviews need trust levels.
- Commands per hour are claims, submissions and verdicts, plus one heartbeat per 4 working hours. This is
  the inbox load the coordinator must process.

`python scaling.py && python report_scaling.py scaling`, `python small_fixes_more.py`

## Fixed size

| Variant | N | Seeds | Online | Merged/day | Merged/day per online | Median h | p95 h | Review backlog | Shipped /10k (14 d soak) | Runs with ≥1 attacker | Careless review share | Review quality | Commands/h (peak/h w/o heartbeats) | Sim s/run |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| flat | 10 | 40 | 1.6 | 2.2 | 1.44 | 12.6 | 59 | 1.7 | 23.9 | 30/40 | 17 % | 0.55 | 1 (6) | 0.0 |
| flat | 30 | 30 | 5.0 | 7.1 | 1.43 | 5.8 | 43 | 1.6 | 16.6 | 29/30 | 15 % | 0.56 | 2 (9) | 0.0 |
| flat | 100 | 20 | 16.4 | 23.9 | 1.46 | 3.1 | 39 | 1.1 | 13.9 | 20/20 | 13 % | 0.57 | 7 (16) | 0.1 |
| flat | 300 | 10 | 50.2 | 74.0 | 1.47 | 2.2 | 38 | 0.5 | 8.8 | 10/10 | 9 % | 0.60 | 22 (36) | 0.4 |
| flat | 1,000 | 5 | 166 | 241 | 1.46 | 2.0 | 39 | 0.1 | 7.6 | 5/5 | 7 % | 0.62 | 74 (82) | 1.4 |
| flat | 3,000 | 4 | 496 | 714 | 1.44 | 2.0 | 39 | 0.2 | 6.3 | 4/4 | 5 % | 0.64 | 221 (187) | 5.7 |
| flat | 10,000 | 3 | 1,656 | 2,372 | 1.43 | 2.0 | 38 | 0.1 | 7.3 | 3/3 | 4 % | 0.65 | 736 (556) | 25 |
| flat | 30,000 | 2 | 4,965 | 7,126 | 1.44 | 2.0 | 39 | 0.0 | 6.3 | 2/2 | 4 % | 0.65 | 2,209 (1,533) | 85 |
| synth | 10 | 40 | 1.7 | 2.4 | 1.42 | 12.7 | 93 | 7.3 | 16.8 | 28/40 | 13 % | 0.56 | 1 (6) | 0.0 |
| synth | 100 | 20 | 16.1 | 23.0 | 1.42 | 3.1 | 40 | 2.6 | 13.6 | 20/20 | 12 % | 0.58 | 7 (18) | 0.1 |
| synth | 1,000 | 5 | 165 | 240 | 1.45 | 2.0 | 39 | 0.4 | 9.3 | 5/5 | 7 % | 0.62 | 74 (73) | 1.6 |
| synth | 10,000 | 3 | 1,652 | 2,366 | 1.43 | 2.0 | 39 | 0.1 | 6.3 | 3/3 | 4 % | 0.65 | 735 (585) | 22 |
| synth | 100,000 | 1 | 16,566 | 23,584 | 1.42 | 2.0 | 39 | 0.0 | 6.4 | 1/1 | 4 % | 0.65 | 7,349 (4,875) | 249 |

**Small sizes measured again with many more seeds** (backdoor counts are small, so a few seeds are noisy):

| Rule | N | Seeds | Merged/day | Median h | Shipped (count) | Shipped /10k | Careless share |
|---|---|---|---|---|---|---|---|
| published (flat) | 10 | 400 | 2.4 | 12.6 | 79 | 11.0 | 15 % |
| published (flat) | 30 | 200 | 7.2 | 5.8 | 109 | 10.0 | 15 % |
| published (flat) | 100 | 80 | 23.8 | 3.1 | 155 | 10.8 | 12 % |
| published (flat) | 1,000 | 4 | 240.8 | 2.0 | 72 | 9.9 | 7 % |
| post-merge review on 100 % of green | 10 / 30 / 100 | | 2.2 / 6.8 / 22.7 | 12.9 / 5.9 / 3.1 | | 9.4 / 12.5 / 10.9 | 15 / 15 / 13 % |
| green needs 2 reviewers + 100 % post-merge | 10 / 30 / 100 | | 2.0 / 5.9 / 19.6 | 26 / 15 / 13 | | 11.2 / 8.9 / 8.9 | 16 % |
| real reviews need calibration score ≥ 0.6 | 10 / 30 / 100 | | 2.4 / 7.3 / 24.0 | 52 / 17 / 7.8 | | 12.2 / 12.5 / 11.0 | 6 % |

## A project growing from 10 to 10,000 people in 120 days

Variant `flat`, 3 seeds. The `synth` variant gives the same numbers, with a review backlog of 2 to 4
instead of below 1.

| Day | Online | Merged/day | Median h | p95 h | Review backlog | Commands/h |
|---|---|---|---|---|---|---|
| 0 | 2 | 2.6 | 7.3 | 49 | 1.0 | 1 |
| 20 | 7 | 10.7 | 4.0 | 36 | 0.8 | 2 |
| 40 | 22 | 31.2 | 2.3 | 35 | 0.5 | 6 |
| 60 | 71 | 98 | 2.0 | 33 | 0.2 | 19 |
| 80 | 225 | 310 | 2.0 | 37 | 0.1 | 60 |
| 100 | 709 | 980 | 2.0 | 36 | 0.0 | 189 |
| 110 | 1,260 | 1,749 | 2.0 | 36 | 0.0 | 336 |
| 120 | 1,655 | 2,422 | 2.0 | 37 | 0.0 | 450 |
| 140 | 1,654 | 2,381 | 2.0 | 39 | 0.0 | 455 |

## Conclusion

1. **Throughput scales linearly, with no bottleneck up to 100,000 people.** The project merges 1.43 changes
   per day per person online at every size, and growth from 10 to 10,000 people never builds a review
   backlog. Nothing in the method needs a central person: reviews are drawn from whoever is online.
2. **Latency needs a few dozen people.**

   | People | Median time to merge |
   |---|---|
   | 10 | 12.6 h |
   | 30 | 5.8 h |
   | 100 | 3.1 h |
   | 300 or more | 2 h |

   At p95 it stays at 38 to 60 h at every size, because reviewers are part-time.
3. **Safety is about 1.5x worse below about 300 people**: about 10-11 shipped backdoors per 10k merges at
   N = 10 to 100 (400 seeds), against 6-7 at N ≥ 3,000. Two causes:
   - Calibration ranking has little choice when 2 to 16 people are online: the careless review share is
     13 to 17 %, against 4 % at scale.
   - Each attacker is a large share of a small project: 50 to 70 attempts per 10k merges, against 35.

   In absolute terms it stays small: about 0.4 shipped backdoors per 90 days at N = 10, and an attacker was
   present in 75 % of those runs.
4. **No cheap rule fixes the small-project gap.**

   | Rule tested | Effect |
   |---|---|
   | 2 reviewers on green | No change beyond noise; median goes up to 12-26 h |
   | 100 % post-merge reviews | No change beyond noise |
   | Minimum calibration score 0.6 | Careless share falls to 6 % but shipped backdoors do not; median goes up to 52 h at N = 10 |

   Recommendation: below about 100 members, keep the maintainers in the reviewer pool and as escalation
   auditors, as founder members. any%5 pays off as people arrive.
5. **Trust levels are not needed.** The engine as coded (`flat`, no levels) is as fast and as safe as the
   published simulation that kept them, and its review backlog is smaller at small sizes.
6. **Coordinator load (git mode).**

   | People | Commands per hour | Per 5-minute run |
   |---|---|---|
   | 10,000 | about 740 | about 60 |
   | 100,000 | about 7,300 | about 610 |

   Every command is an issue comment posted by its sender, so posting counts against each contributor's
   own GitHub rate limit. The coordinator reads 100 comments per API call, so even at 100k it needs about
   75 read calls per hour, far below the 1,000 to 5,000 requests per hour allowed to an Actions or App token.
   API limits are therefore not the bottleneck. These are, and the simulation does not model them:
   - **Merges into one branch:** 2,400 per day at 10k, about 100 per hour. Conflicts and CI would need a
     merge queue.
   - **Ledger size:** one commit per run rewrites the boxes that changed.
   - **Run time:** one Actions run must process up to about 600 commands in 5 minutes.
7. **Simulator cost:** 25 s per run at 10k and 250 s at 100k. Simulating very large projects is cheap.
