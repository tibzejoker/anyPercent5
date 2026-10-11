# Canary gaming

Question: canaries made from merged diffs can be spotted with a `git log` lookup. Does that break any%5, and do
other canary sources fix it? Reproduce with `python canary_gaming.py 90 5` (any%5 = scenario `synth`, fair-eval
harness of `crosseval.py`, 90 days, 5 seeds, 3 attacker profiles).

Gamers: every attacker and colluder, plus 50 % of lazy reviewers. A gamer rejects the canaries it recognises and
approves everything else without looking (reviews, post-merge reviews, audits), so recognising canaries lifts it
to the top of the calibration ranking. Canary sources:

- **merged**: a merged diff with a planted defect; always recognised (history lookup).
- **pending-variant**: a copy of a real pending change with a planted defect, served only through the blind
  channel. Recognised if the gamer, or a gamer of its group (its ring, or all attackers), authored or was assigned
  the original; otherwise 5 %.
- **synthetic**: an agent-made change; recognised 30 % of the time.

Counters tested: 25 % of real review slots go to a random idle member instead of the best-ranked one; an approver
of a caught backdoor loses its calibration record.

Raw shipped counts are confounded: top-ranked gamers spend their time on jury duty and author fewer backdoors
(attempts column). A real attacker approves blindly in seconds and keeps authoring, so the fair comparison is the
leak per attempt, shown also rescaled to the reference attempt count.

| Scenario | Merged/day | Median h | Shipped / 10k merges, raw (eager / standard / patient) | Backdoor attempts (std) | Leak per attempt (std) | Shipped / 10k merges at reference attempt count | Look quality per real review | Real reviews by gamers | Canaries recognised by gamers |
|---|---:|---:|---|---:|---:|---|---:|---:|---:|
| no gaming (reference) | 2370 | 2 | 8.2 / 6.6 / 2.8 | 755 | 15.7 % | 8.2 / 6.6 / 2.8 | 0.65 | 0.0 % | 0 % |
| merged canaries | 2423 | 2 | 9.5 / 6.7 / 0.9 | 346 | 35.5 % | 16.5 / 14.6 / 6.4 | 0.47 | 30.9 % | 100 % |
| pending-variant canaries (rings coordinate) | 2360 | 2 | 10.8 / 8.8 / 4.2 | 1065 | 14.8 % | 7.6 / 6.2 / 2.8 | 0.65 | 0.8 % | 6 % |
| pending-variant canaries (all attackers coordinate) | 2358 | 2 | 9.2 / 8.1 / 3.7 | 1022 | 14.2 % | 6.6 / 6.0 / 2.6 | 0.65 | 0.9 % | 7 % |
| synthetic canaries (30% recognised) | 2360 | 2 | 9.6 / 8.3 / 4.2 | 996 | 15.0 % | 7.3 / 6.3 / 3.0 | 0.63 | 4.0 % | 30 % |
| merged + random reviewer slot 25% | 2423 | 2 | 9.7 / 7.5 / 1.2 | 386 | 35.7 % | 15.6 / 14.6 / 6.9 | 0.46 | 30.1 % | 100 % |
| merged + approver recalibration | 2415 | 2 | 9.6 / 6.6 / 0.9 | 337 | 35.9 % | 16.6 / 14.8 / 6.7 | 0.47 | 31.1 % | 100 % |
| merged + both counters | 2416 | 2 | 9.2 / 6.6 / 1.5 | 373 | 32.6 % | 14.8 / 13.4 / 8.3 | 0.45 | 30.6 % | 100 % |
| pending-variant (all) + both counters | 2375 | 2 | 10.5 / 9.6 / 4.3 | 1034 | 16.7 % | 7.7 / 7.0 / 3.1 | 0.63 | 2.5 % | 6 % |
| synthetic + both counters | 2366 | 2 | 10.6 / 9.0 / 4.2 | 1043 | 15.6 % | 7.9 / 6.5 / 3.0 | 0.62 | 4.9 % | 30 % |
| no gaming + both counters | 2360 | 2 | 9.1 / 7.4 / 3.1 | 801 | 16.5 % | 8.5 / 6.9 / 3.0 | 0.63 | 0.0 % | 0 % |

"Look quality" is skill × diligence averaged over real pre-merge reviews, a gamer's blind approval counting 0.

## Conclusion

1. The leak is real: with merged-diff canaries, gamers recognise all of them, rise to the top and do 31 % of real
   reviews. Look quality drops from 0.65 to 0.47 and the leak per backdoor attempt doubles (15.7 % to 35.5 %).
2. Pending-variant canaries fix it: gamers recognise 6-7 % of them even when all attackers pool what they see,
   they do under 1 % of reviews, and the leak per attempt matches the no-gaming reference (14-15 %).
3. Synthetic canaries recognised 30 % of the time also hold (15.0 %), so pending variants need not be perfect.
4. The cheap counters (random reviewer slot, approver recalibration) do not repair merged canaries
   (32.6-35.9 %) and cost a little without gaming (16.5 %). Drop them; fix the canary source instead.
5. Rule: never build a canary from anything public. Serve pending variants (or synthetic changes) through the
   blind channel only, and keep real changes unpublished until their review is done.
