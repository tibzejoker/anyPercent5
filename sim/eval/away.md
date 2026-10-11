# "away": freezing a lease during a declared absence

Question: should a holder be able to declare an absence (`anyp5 away 5d`) that freezes their lease instead of
losing it? If so, within which limits, given that zombies and fake pushers will abuse it?

## Model

The base is `capsim.py` (built by `make_capsim.py` from `lease_sim.py`), run with the recommended lease from
[lease_sweep.md](lease_sweep.md): first push due 24 h after the claim, each push renews 72 h, cap 5 days plus
extension. The project has 1,000 people over 90 days, with features of 20 dependent tasks. Results are 20 seeds
per cell; ± is the 95 % CI.

**Honest holders.** When going offline, a holder knows how long they will be away: the offline gap is sampled in
advance from the same presence process. If the gap would outlast the lease, they declare away 90 % of the time.
The lease clock is then frozen for the declared time, within these limits:
- **max:** at most this many days per declaration;
- **budget:** at most this many days per 30 days;
- **dependents rule**, one of:
  - none;
  - **hubs:** no away on the core tasks most others depend on;
  - **takeover:** on a task with dependents, the freeze stops after 48 h and someone else may take over with the
    branch;
- **needs a push:** away is allowed only after a first pushed commit;
- **cap:** the away either pauses the cap clock or not ("the cap keeps running");
- **progress check:** at the cap, the extension needs a push in the last 48 h. Away time can be left out of
  that window.

**Abusers.** All zombies (heartbeats, no push) and fake pushers (token commits, never finish) declare their
whole budget. So the results are a worst case.

**Security** is measured with the same review and attacker model as the scaling and canary sims.

Reproduce:

```
python make_capsim.py
python caps_away.py run
python caps_away.py extra
python caps_away.py extra2
python caps_away.py report
```

## Results

| World | Rule | Merged/day | Feature p95 (d) | Unfinished after 30 d | Honest leases lost /1k claims | Abuser hold, median (h) | Careless reviews | Shipped /10k |
|---|---|---|---|---|---|---|---|---|
| honest | no away (24 h / 72 h / 5 d + ext) | 236 ± 2 | 31.6 ± 0.3 | 0.5 % | 118 | 0 | 6.2 % | 8.2 |
| honest | A: away up to 5 d, 14 d/month, no limits | 240 ± 2 | 32.6 ± 0.2 | 0.6 % | 18 | 0 | 6.3 % | 7.6 |
| honest | B: A at 7 d/month, none on hub tasks | 239 ± 2 | 32.3 ± 0.3 | 0.6 % | 33 | 0 | 6.5 % | 8.3 |
| honest | C: A at 7 d/month, takeover after 48 h if 2+ dependents | 242 ± 2 | 32.4 ± 0.2 | 0.5 % | 23 | 0 | 6.5 % | 9.7 |
| honest | D: C with 1+ dependent, only after a first push | 239 ± 1 | 31.9 ± 0.2 | 0.5 % | 84 | 0 | 6.4 % | 9.1 |
| honest | E: D, the cap keeps running | 238 ± 2 | 31.5 ± 0.4 | 0.5 % | 93 | 0 | 6.6 % | 8.2 |
| honest | **F: E at 21 d/month, away time not counted in the progress check** | 238 ± 2 | 31.5 ± 0.2 | 0.4 % | 84 | 0 | 6.5 % | 8.2 |
| silent 20 % + zombie 5 % | no away (24 h / 72 h / 5 d + ext) | 228 ± 2 | 40.8 ± 0.4 | 2.7 % | 119 | 24 | 6.1 % | 7.7 |
| silent 20 % + zombie 5 % | A: away up to 5 d, 14 d/month, no limits | 234 ± 2 | 57.9 ± 0.4 | 12.8 % | 18 | 360 | 6.1 % | 9.0 |
| silent 20 % + zombie 5 % | B: A at 7 d/month, none on hub tasks | 235 ± 2 | 46.7 ± 0.4 | 5.4 % | 33 | 192 | 6.2 % | 9.1 |
| silent 20 % + zombie 5 % | C: A at 7 d/month, takeover after 48 h if 2+ dependents | 234 ± 2 | 45.2 ± 0.4 | 4.5 % | 24 | 192 | 6.3 % | 9.8 |
| silent 20 % + zombie 5 % | D: C with 1+ dependent, only after a first push | 234 ± 2 | 40.2 ± 0.3 | 2.7 % | 85 | 24 | 6.2 % | 9.2 |
| silent 20 % + zombie 5 % | E: D, the cap keeps running | 233 ± 2 | 40.0 ± 0.4 | 2.6 % | 93 | 24 | 5.9 % | 9.2 |
| silent 20 % + zombie 5 % | **F: E at 21 d/month, away time not counted in the progress check** | 233 ± 2 | 40.2 ± 0.3 | 2.3 % | 84 | 24 | 6.0 % | 9.5 |
| weekend-only half | no away (24 h / 72 h / 5 d + ext) | 186 ± 2 | 38.7 ± 0.4 | 1.5 % | 214 | 0 | 7.8 % | 11.9 |
| weekend-only half | A: away up to 5 d, 14 d/month, no limits | 196 ± 1 | 40.9 ± 0.3 | 2.4 % | 45 | 0 | 7.8 % | 12.0 |
| weekend-only half | B: A at 7 d/month, none on hub tasks | 193 ± 2 | 39.7 ± 0.5 | 1.9 % | 99 | 0 | 7.7 % | 10.8 |
| weekend-only half | C: A at 7 d/month, takeover after 48 h if 2+ dependents | 192 ± 2 | 40.1 ± 0.3 | 1.7 % | 95 | 0 | 7.7 % | 11.9 |
| weekend-only half | D: C with 1+ dependent, only after a first push | 192 ± 2 | 39.6 ± 0.4 | 1.8 % | 147 | 0 | 7.4 % | 11.6 |
| weekend-only half | E: D, the cap keeps running | 188 ± 2 | 39.3 ± 0.6 | 2.0 % | 184 | 0 | 7.7 % | 11.4 |
| weekend-only half | **F: E at 21 d/month, away time not counted in the progress check** | 191 ± 2 | 38.8 ± 0.5 | 1.8 % | 132 | 0 | 7.5 % | 10.1 |
| fake pushers 5 % | no away (24 h / 72 h / 5 d + ext) | 234 ± 2 | 39.8 ± 0.4 | 2.5 % | 119 | 120 | 6.4 % | 8.2 |
| fake pushers 5 % | A: away up to 5 d, 14 d/month, no limits | 239 ± 3 | 59.5 ± 0.7 | 13.6 % | 18 | 456 | 6.3 % | 7.0 |
| fake pushers 5 % | B: A at 7 d/month, none on hub tasks | 238 ± 1 | 48.2 ± 0.5 | 6.0 % | 32 | 288 | 6.6 % | 8.7 |
| fake pushers 5 % | C: A at 7 d/month, takeover after 48 h if 2+ dependents | 239 ± 2 | 46.6 ± 0.4 | 5.3 % | 23 | 288 | 6.0 % | 9.3 |
| fake pushers 5 % | D: C with 1+ dependent, only after a first push | 238 ± 1 | 45.0 ± 0.4 | 4.3 % | 85 | 288 | 6.5 % | 9.3 |
| fake pushers 5 % | E: D, the cap keeps running | 235 ± 2 | 39.6 ± 0.3 | 2.2 % | 93 | 120 | 6.3 % | 8.8 |
| fake pushers 5 % | **F: E at 21 d/month, away time not counted in the progress check** | 237 ± 2 | 39.3 ± 0.4 | 2.1 % | 84 | 120 | 6.3 % | 8.6 |
| big tasks 10 % (40-120 h) | no away (24 h / 72 h / 5 d + ext) | 151 ± 2 | 77.5 ± 1.1 | 25.3 % | 146 | 0 | 7.1 % | 11.0 |
| big tasks 10 % (40-120 h) | D: C with 1+ dependent, only after a first push | 160 ± 2 | 72.1 ± 0.8 | 21.6 % | 96 | 0 | 7.0 % | 14.3 |
| big tasks 10 % (40-120 h) | E: D, the cap keeps running | 160 ± 2 | 72.8 ± 0.9 | 22.4 % | 109 | 0 | 7.0 % | 14.0 |
| big tasks 10 % (40-120 h) | **F: E at 21 d/month, away time not counted in the progress check** | 160 ± 3 | 71.1 ± 0.7 | 21.4 % | 93 | 0 | 6.9 % | 13.6 |

The full 25-rule grid is printed by `caps_away.py report`: max away 3, 5 or 7 days × budget 7 or 14 days ×
dependents rule. Max length (3/5/7 d) barely matters; budget and abuse do.

## Conclusion

1. **A naive away (A) helps honest people but hands abusers a free hold.**
   - Honest leases lost fall from 118 to 18 per 1,000 claims, and weekend contributors lose 45 instead of 214.
     Throughput rises 2-5 %.
   - But zombies and fake pushers hold tasks for 15-19 days instead of 1-5. p95 feature delay rises 41 → 58
     days with silent and zombie holders, and 40 → 60 with fake pushers.
   - Hub and takeover limits (B, C) cut that damage by about two thirds, but don't remove it.
2. **Two limits remove the abuse:**
   - **Away only after a first push.** Zombies never push, so they can't use it.
   - **The cap keeps running during an away.** Fake pushers gain nothing beyond the cap they already had.
   With these (E), abusers hold exactly as without away: p95 is 40.0 and 39.6 days, against 40.8 and 39.8
   without away.
3. **One more fix for honest people:** away time does not count against the 48 h "recent progress" window
   checked at the cap (F). Without it, a weekend contributor who is away when the cap arrives loses the task.
   F is the best rule:
   - honest leases lost 118 → 84 per 1,000 claims, weekend contributors 214 → 132;
   - big-task world: +6 % throughput (151 → 160 merged/day) and p95 77.5 → 71.1 days;
   - no abuse: fake-pusher world p95 39.3 against 39.8; zombies still hold 24 h.
4. **Security does not move.** Careless review share is 6-8 % and shipped backdoors stay at about 8-14 per 10k
   in every world. Differences between rules are within the noise of 20 seeds.
5. **Recommended rule:**
   - `anyp5 away <days>`: at most 5 days per declaration and 21 days per 30 days;
   - only on a task with at least one pushed commit;
   - it freezes the lease, not the 5-day cap;
   - the away time is not counted in the cap's progress check;
   - on a task that other tasks wait on, the freeze lasts 48 h, then another member may take over with the
     branch.

   Remaining weekend losses (132 per 1,000) are claims abandoned before any push. That is cheap: no work is
   lost.
