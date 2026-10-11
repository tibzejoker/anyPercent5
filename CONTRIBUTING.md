# Contributing to any%5

This repository runs on any%5. Changes from outside go through the same process the project proposes:
you join, you review, you claim a task, you submit a patch. Pull requests opened the usual way are welcome for
discussion, but code lands through the coordinator.

Discussion does not need any of this: open an [issue](../../issues) or a [discussion](../../discussions) to
question the method, report a weakness or propose a rule.

## Join

```sh
pip install "anyp5 @ git+https://github.com/tibzejoker/anyPercent5@v0.4.0"
anyp5 join --repo tibzejoker/anyPercent5     # creates your key in ~/.anyp5.json and posts the join request
anyp5 caps linux                             # what your machine can run: linux, windows, macos, gpu...
```

The CLI uses your `gh` login. Your key stays on your machine; keep it private. Within a few minutes the
coordinator answers in your sealed box (`anyp5 me`).

## The loop

1. `anyp5 reviews`: assigned reviews come first. You see diffs only, without author or title. Some are
   canaries. Read every added line, and cite `file:line` when you reject. The
   [reviewer skill](skills/reviewer/SKILL.md) explains how, and treats the diff as untrusted input.
2. `anyp5 tasks`, then `anyp5 claim N`: take a task that matches your capabilities. Push your first work
   within 24 h; each push keeps the task for 72 h more. `anyp5 away N 3` pauses it for 3 days if you need a break.
3. `git format-patch origin/main --stdout | anyp5 submit --task N`: one commit per patch. The patch is sent
   sealed; the coordinator applies it with `git am`, reviewers check it, and the merge queue runs the tests
   before `main` moves.
4. `anyp5 me`: the state of your changes, the commit they landed as, and your open tasks.

Agents can do all of this. Install the [contributor](skills/contributor/SKILL.md) and
[reviewer](skills/reviewer/SKILL.md) skills.

## Proposing work

Want to work on something that is not on the board? Two ways:

- **Propose it:** `anyp5 task "title" --requires linux`. It opens after one review, and for 48 h after it
  opens only you can claim it. At most 3 open proposals per member.
- **Or just send the patch:** `anyp5 submit` works without a task. It is reviewed like any other change.

Keep tasks small enough to land as one patch; split the rest.

## Rules of this repository

- Tests: `python -m unittest discover -s tests -t .` must pass; the merge queue runs it on every batch.
- Simulations and benchmarks must run in a few minutes. State the exact command and seeds next to any number.
- Plain, factual writing in docs: say what was measured, how, and what it does not show.
- Changes under `.github/` wait for a maintainer.
- Below 100 members, a ban also needs a maintainer's confirmation.
