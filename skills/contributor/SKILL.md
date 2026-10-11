---
name: anyp5-contributor
description: Work on a project that uses any%5 (leased tasks, jury-duty reviews, soak-gated releases). Use when the user asks you to pick up, continue or submit work on such a project, or when `anyp5` is configured (~/.anyp5.json).
---

# Contributing under any%5

any%5 replaces "trust the author" with "check every change, blindly, by calibrated reviewers, before it ships".
You will be asked to review as often as you write. That is the price of entry, and it is what keeps the
project fast for everyone.

## Loop

1. **Jury duty first.** Run `anyp5 reviews`. If anything is listed, do those reviews first (use the
   `anyp5-reviewer` skill). You cannot claim new work while reviews are assigned to you.
2. **Pick a task.** `anyp5 tasks` lists open tasks with their risk tier. Prefer tasks that fit in one working
   session. `anyp5 claim <id>` gives you a lease.
3. **Keep the lease with pushed work.** Push a first commit within 24 h and record it with
   `anyp5 handover <id> <branch>`. Each handover renews the lease for 72 h, up to 7 days after the claim.
   `anyp5 alive` keeps you in the reviewer pool but does not keep a task. If you stop, the task goes back to
   the pool with your branch. That is expected, not a failure.
4. **Push partial work early.** Push to a branch named `task/<id>-<slug>` and record it:
   `anyp5 handover <id> <branch>`. Whoever takes the task after you resumes from that branch, so commit in
   small, explained steps.
5. **Prove the change.** Add or change a test that **fails without your change and passes with it**. A test
   that only compiles with your change proves nothing. Run the project's evidence tool if it has one
   (`evidence.py collect`), and put its summary in the PR.
6. **Keep the risk tier honest and low.** Check your diff with `git diff origin/main... | anyp5 tier`.
   Avoid touching build scripts, CI, dependencies, network, process or file APIs unless the task is about
   them. Those make the change red, which needs more and better-calibrated reviewers and a mandatory audit.
   Never hide such a call to dodge the tier: reviewers are trained on exactly that.
7. **Open the PR** with a plain description of what changed and why, the test that proves it, and the tier
   output. State that it is AI-assisted.

## What happens next

- Reviewers are drawn by the server (you cannot pick them), ranked by how reliably they catch planted
  defects. They see your diff without your name.
- Green changes need 1 approval, amber 3, red 2 plus a post-merge audit. A spare reviewer is added if one
  is slow.
- After merging, your change **soaks** on main for 14 days, during which part of it is checked again. Only
  then does it reach a release.
- If a reviewer rejects your change, read the reasons, fix them and push again (a new head is a new review).
- A change confirmed as a backdoor by two independent auditors bans its author, reverts all their merged
  work and re-checks what their approvers passed. Honest bugs never lead to a ban.
