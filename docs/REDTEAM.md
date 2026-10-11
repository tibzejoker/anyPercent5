# Red team (v0.3)

Five fresh AI agents were given only this repository and the instruction to break it. Each had about 15
minutes and wrote its own proofs of concept against the real code. One attacked freely. The other four were
each given a lens, which they could drop:
- an organised adversary;
- the protocol and implementation;
- the evidence;
- the human side and the AI-agent side.

Everything they broke is fixed and covered by tests. Their proofs of concept were then replayed against the
fixed code.

## What broke, and what changed

| Severity | Attack (found by) | Fix | After |
|---|---|---|---|
| critical | **Rejecting everything wins calibration.** A catch was any reject, and wrong rejects cost nothing. 3-5 accounts reached the top, took 39-68 % of reviews, rejected 52-78 % of honest changes, and as top-ranked auditors got honest members banned (4 of 5) | Two-sided score: clean canaries are scored on approval, and a planted defect counts only if the reject cites its line. Reviews are drawn at random among the better-calibrated half. A lone reject asks a second opinion. Auditors are drawn at random, and below 100 members a maintainer confirms bans | Reject-all ring: 3.4 % of reviews (its share of members is 4.8 %), 1 % of honest changes rejected, no ban. Stealth ring: 6.7 % of reviews, 0.3 % rejected |
| critical | **Reviewers see one commit, `git am` applies several.** A multi-mail patch hid a second commit with `system()` and a workflow (1, 3) | Refuse more than one mail. After `git am`, check that exactly one commit landed and that it changes the reviewed lines | Refused |
| critical | **Renames and binary patches are tier green.** `git mv` into `.github/workflows/` and binary literals had no `+++` lines (1, 3) | Paths are read from `diff --git` headers. Renames, copies, mode changes, symlinks, submodules and binary content are red. More red paths and patterns (`Dockerfile`, `build.rs`, `conftest.py`, `execl`, `fork`…) | Red |
| critical | **One anonymous comment stops the coordinator.** A garbage sealed body raised an uncaught error before the inbox moved on, and a zip bomb inflated to about 50 MB (3) | Contain every command error, always advance the inbox, cap decompression at 8 MiB | Test passes |
| critical | **Approved CI changes.** They freeze `main`, because `GITHUB_TOKEN` cannot push workflows. With a stronger token they could read the coordinator key (1, 3) | Changes under `.github/` wait for a maintainer. A refused push no longer blocks the other changes | Test passes |
| high | **Holder checks missing.** Any member could hand over or finish anyone's task (1, 5) | Holder-only handover. A task finishes only when its change merges, or by an admin | Test passes |
| high | **Fake inbox issues.** Anyone can open an issue titled "anyp5 inbox" (1) | Only issues opened by the bot or an admin are read | Test passes |
| medium | **Canaries recognised by waiting.** A reviewer keeps the copy until the original lands, then compares (3, 4) | A canary is withdrawn unscored when its original leaves review. An expired canary counts as missed | Test passes |
| medium | **Calibration visible in each member's box.** Watching it change reveals which reviews were canaries (2) | Never shown to members | Test passes |
| medium | **Canary mutations too recognisable** (one fixed `getenv("CI_DEBUG")` string) (2, 4, 5) | Eight mutation kinds with varied names. Projects can add their own recipes, sealed in the ledger, drawn first | Recipes exist; their quality is up to each project |
| medium | **Comment size and timing leak who submitted what** (3) | Sealed sizes padded to powers of two. Within a pass, reviews are drawn (canary included) and boxes and log entries written in an order set by the hidden seed; boxes carry only the pass time | The order of commits pushed to `main` in one pass still follows submission; who posted a comment, and when, is public on the forge |
| medium | **Prompt injection in diffs aimed at reviewer agents** (5) | The reviewer skill treats the diff as untrusted, and text aimed at the reviewer is a reason to reject | Depends on each agent |
| medium | **Server mode defaulted to merged-diff canaries** (4, 5) | Pending copies everywhere | Fixed |
| low | **Forged `From:` in a patch.** Rejoining could re-key a member. Commands were not bound to a project (3) | Commits are attributed to the sender. Re-keying is refused. Commands carry the project | Fixed |

## What the evidence review changed (4)

- **The headline was too strong.** "Shipped per 10k merges" rewards keeping attackers busy reviewing. Per
  attempt, any%5 leaks 13.6 % against 27.5 % for the trust-level baseline: about 2x, not 3x.
- **The simulation's attacker is noisy.** It plants a backdoor in half its changes, each one independently
  visible. With correlated difficulty and one-shot attackers (xz-like), every design ships about 43 % of
  attempts. any%5's advantage over the other designs disappears.
- **Rejecting was free in the model**, so the main break above could not show up in simulation. The engine
  PoCs above are the evidence for the fix. The simulator does not model false rejects yet.
- **What held:**
  - throughput and latency;
  - pending-copy canaries over merged-diff canaries;
  - the 14-day soak (2-3x fewer shipped in every design);
  - leases, away limits and the hidden-seed draws, in every agent's review;
  - the cryptography (X25519, ChaCha20-Poly1305, Ed25519, replay protection): no agent could forge or replay a
    command.

## Still open

- **A subtle, one-shot backdoor that most reviewers miss is not stopped by any review scheme measured here.**
  Smaller diffs, capability-based tiers, sandboxed builds and evidence bundles raise the per-look catch rate.
  Nothing here proves how much.
- **Rings recognise their own diffs.** A ring can watermark its patches, for example with an HMAC in a
  comment. Blind review does not hide a diff from the people who wrote it. The random draw keeps such rings
  near their share of reviews.
- Built-in canaries are generic. Projects can now add their own recipes (hand-made or mined from fix commits
  by an agent), but a project that adds none, or only a few, still serves recognisable canaries. Nothing
  measures how much project recipes help.
- **Direction is only steered at the task level.** Optional `architects` approve or close member task
  proposals, but a change is reviewed against its diff, not against a design: nothing stops an approved
  change from drifting from the task it claims, or a series of small changes from reshaping the architecture.
  Architects are a trust point (any one of them can close every member proposal), their effect on throughput is
  not measured, and projects without architects still have no direction owner.
