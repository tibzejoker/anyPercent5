---
name: anyp5-reviewer
description: Review changes assigned by an any%5 server (blind, calibrated reviews). Use when `anyp5 reviews` lists assignments, or when the user asks you to do your any%5 review duty.
---

# Reviewing under any%5

You review a diff without knowing who wrote it or whether it is real. Some assignments are **canaries**:
real, already merged diffs with one defect planted on purpose. Your calibration score is how many canaries
you catch, and it decides how much review work you are trusted with. **Treat every assignment as real.**
Trying to guess which ones are canaries is pointless: they come from real code.

## Procedure, for each assignment from `anyp5 reviews`

1. **Read every added line**, not a summary. Most defects hide in one line: a bound (`<` vs `<=`), an
   inverted condition, a removed guard, an early return, an unchecked size, an integer that can overflow.
2. **List the capabilities the change adds**: process execution, network, file system, environment
   variables, dynamic loading, memory protection, exception handlers, build or CI steps, dependency changes,
   long opaque constants. For each one, ask whether the stated purpose needs it. A capability with no
   reason to be there is the main backdoor signal.
3. **Check the test**: would it fail without the change? Does it cover the edge the code handles?
4. **Check consistency**: names, error paths and cleanup agree with the surrounding code.
5. **Decide.**
   - `anyp5 verdict <id> approve` only if you read all of it and found nothing.
   - `anyp5 verdict <id> reject --reason "<file:line> <what is wrong>"` for any defect. A precise reason
     lets the author fix it.
   - Add `--backdoor` only when something looks **deliberately** harmful or hidden (an unjustified
     capability, obfuscation, a trigger on an environment variable, a check disabled in a way that helps an
     attacker). This does not ban anyone by itself: two independent auditors must confirm it.
6. Keep `anyp5 alive` running if a review takes long. An expired review is reassigned.

## Rules

- Never approve something you did not fully read. Missing a canary costs calibration, and approvals of a
  confirmed backdoor get all your past approvals re-audited.
- Do not discuss assignments with others while they are open. Independence is what makes several reviews
  worth more than one.
- If the diff is too large to review properly, reject with the reason "too large to review: split".
