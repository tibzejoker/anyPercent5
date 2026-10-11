# When a task others depend on goes silent

Question: what happens when the people who took a part that others depend on stop responding?

## Model

The any%5 synth simulator (`anyp5_sim.py`, engine-faithful variant with no trust levels) was extended with a
task graph. Work arrives in **epics of 20 tasks**. The 2 root "hub" tasks are depended on by most of the epic:
60 % of tasks depend on a hub, and each task has 1 to 3 prerequisites. A task can be claimed only once all its
prerequisites have merged. Ready tasks are claimed oldest epic first, and a new epic opens whenever fewer
ready tasks than half the people online are waiting.

Claims can go wrong in two ways:

- **Silent**: the holder stops, with no heartbeat and no release. Half of these claims never start; the
  others stop part way through.
- **Zombie**: the holder's tooling keeps sending heartbeats, but the task is never finished. Under the
  current rule (any activity renews the lease) such a lease lasts until the person leaves the project, about
  20 days on average.

Rules compared:

| Rule | Lease |
|---|---|
| R0 (published) | 72 h, renewed by any heartbeat |
| R1 | 24 h, renewed by any heartbeat |
| R2 | 72 h, renewed only by pushed progress (a commit on the task branch) |
| R3 | R0 + a 7-day total cap, then the task is reassigned with its branch |
| R4 | R2 + 7-day cap + ready hub tasks claimed first, with a 72 h cap on hubs |
| R5 | R4 + the first commit must be pushed within 24 h of the claim |

Setup: 1,000 people (and 10,000 for one row), 90 days, 5 seeds (2 at 10,000). Epic and hub statistics cover
epics opened before day 60. "Blocked wait" is the time from creation until all prerequisites have merged.
Under R2 to R5, partial work is on a pushed branch by construction, so lost leases waste nothing. Under R0
and R1, half of the partial work is assumed to be pushed.

`python dependencies.py && python report_scaling.py deps`

## Results (N = 1,000)

| Silent | Zombie | Rule | Merged/day | Blocked wait med / p95 (d) | Epic duration med / p95 (d) | Epics still open after 30 d | Hub ready→merged med / p95 (d) | Wasted work | Honest leases lost /1k claims |
|---|---|---|---|---|---|---|---|---|---|
| 0 % | 0 % | R0 published | 238 | 7.7 / 20.8 | 22.8 / 35.2 | 1.2 % | 4.5 / 11.4 | 4.4 % | 51 |
| 0 % | 0 % | R1 24 h lease | 198 | 8.2 / 21.2 | 22.3 / 34.7 | 0.8 % | 4.9 / 12.8 | 18.0 % | 474 |
| 0 % | 0 % | R5 | 240 | 6.2 / 18.3 | 20.6 / 31.7 | 0.4 % | 2.9 / 8.1 | 0 % | 78 |
| 20 % | 0 % | R0 published | 233 | 9.4 / 26.6 | 28.4 / 44.4 | 4.3 % | 5.1 / 14.2 | 5.8 % | 50 |
| 20 % | 0 % | R1 24 h lease | 195 | 9.3 / 24.5 | 25.5 / 39.6 | 2.6 % | 5.3 / 14.9 | 18.9 % | 468 |
| 20 % | 0 % | R2 progress renews | 241 | 9.2 / 25.5 | 27.4 / 42.0 | 3.5 % | 5.1 / 14.0 | 0 % | 48 |
| 20 % | 0 % | R4 | 241 | 8.0 / 24.2 | 26.4 / 41.1 | 2.6 % | 3.6 / 11.1 | 0 % | 77 |
| 20 % | 0 % | R5 | 241 | 7.2 / 21.8 | 24.2 / 37.3 | 1.6 % | 3.3 / 10.1 | 0 % | 77 |
| 50 % | 0 % | R0 published | 227 | 15.5 / 43.7 | 43.5 / 71.8 | 22.4 % | 7.2 / 27.0 | 9.5 % | 47 |
| 50 % | 0 % | R1 24 h lease | 188 | 12.9 / 35.3 | 35.2 / 56.6 | 9.2 % | 6.8 / 23.4 | 22.3 % | 457 |
| 50 % | 0 % | R2 progress renews | 240 | 14.8 / 42.0 | 42.0 / 68.6 | 19.7 % | 7.2 / 25.7 | 0 % | 46 |
| 50 % | 0 % | R4 | 236 | 13.6 / 41.3 | 42.3 / 67.9 | 19.3 % | 6.0 / 20.7 | 0 % | 74 |
| 50 % | 0 % | R5 | 231 | 10.8 / 32.7 | 34.6 / 54.0 | 10.0 % | 4.9 / 15.2 | 0 % | 74 |
| 5 % | 5 % | R0 published | 237 | 8.8 / 40.4 | 35.1 / 75.6 | 22.8 % | 4.8 / 18.3 | 4.6 % | 51 |
| 5 % | 5 % | R1 24 h lease | 196 | 9.1 / 48.6 | 41.9 / 82.8 | 34.5 % | 5.1 / 25.9 | 18.1 % | 473 |
| 5 % | 5 % | R2 progress renews | 240 | 8.3 / 22.3 | 24.3 / 37.2 | 1.7 % | 4.7 / 12.3 | 0 % | 49 |
| 5 % | 5 % | R3 7-day cap | 243 | 8.6 / 24.3 | 26.1 / 40.7 | 2.6 % | 4.8 / 12.7 | 0 % | 55 |
| 5 % | 5 % | R5 | 243 | 6.6 / 19.4 | 21.5 / 33.2 | 0.5 % | 3.0 / 8.7 | 0 % | 78 |
| 20 % | 5 % | R0 published | 235 | 10.5 / 47.0 | 40.5 / 81.4 | 30.3 % | 5.4 / 23.2 | 5.8 % | 51 |
| 20 % | 5 % | R1 24 h lease | 195 | 10.4 / 54.0 | 46.2 / 89.3 | 41.3 % | 5.4 / 31.8 | 19.2 % | 469 |
| 20 % | 5 % | R2 progress renews | 240 | 9.8 / 27.5 | 29.2 / 45.1 | 4.6 % | 5.2 / 15.5 | 0 % | 48 |
| 20 % | 5 % | R3 7-day cap | 242 | 10.1 / 29.4 | 31.4 / 49.0 | 6.1 % | 5.4 / 15.8 | 0 % | 55 |
| 20 % | 5 % | R4 | 240 | 8.6 / 26.1 | 28.6 / 43.8 | 4.4 % | 3.8 / 12.3 | 0 % | 78 |
| 20 % | 5 % | R5 | 240 | 7.6 / 22.7 | 25.0 / 38.8 | 1.9 % | 3.4 / 10.3 | 0 % | 78 |

**N = 10,000, 20 % silent + 5 % zombie:** the shape is the same.

| Rule | Merged/day | Epic duration med / p95 (d) | Epics still open after 30 d |
|---|---|---|---|
| R0 | 2,327 | 40.7 / 80.7 | 30.5 % |
| R2 | 2,388 | 29.2 / 44.9 | 4.8 % |
| R5 | 2,386 | 24.8 / 38.2 | 1.8 % |

The full table, all rules and the 5 % silent rows are in `dependencies_results.json`.

## Conclusion

1. **Throughput barely moves**, even with 50 % silent claims. Other work is always available, so the cost is
   **latency on dependent work**: features take longer, while the project as a whole does not slow down.
2. **Zombie leases are the real danger of the published rule.** If only 5 % of claims are zombies, epics get
   twice as long at p95 (35 → 76 days), and 23 to 30 % of features stay unfinished after a month. Any
   heartbeat renews the lease, so the task stays held until the person leaves.
3. **Renewing only on pushed progress (R2) removes the zombie problem** (p95 76 → 37 days). It costs nothing
   in throughput, and it also means abandoned work is always on a branch the next person can resume.
4. **Short leases (R1, 24 h) are a bad fix.** Part-time people lose about 470 leases per 1,000 claims, waste
   18 to 22 % of their work, and lower throughput by 17 %. They also do not stop zombies.
5. **Recommended rule (R5):**
   - leases are renewed by pushed commits, not by heartbeats;
   - the first commit is due within 24 h of the claim;
   - leases are capped at 7 days, after which the task is reassigned with its branch;
   - ready hub tasks (those many others depend on) are claimed first, with a 72 h cap.

   Under 20 % silent and 5 % zombie claims, R5 brings epic p95 back from 81 days to 39 days, close to the
   38 days with nobody failing. It costs about 30 more lost leases per 1,000 claims (honest hub holders who
   hit the 72 h hub cap, whose work is handed over).
6. Even with nobody failing, a 20-task epic takes about 3 weeks. Contributors are online about 17 % of the
   time, so each level of the graph costs about 2.5 days. Splitting hub tasks small matters more than any
   lease rule.
