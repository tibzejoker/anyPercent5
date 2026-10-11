---
name: anyp5-maintainer
description: Install any%5 (git mode) on a repository the user maintains, then seed starter tasks and explain how to invite contributors. Use when the user asks to "install any%5", "set up anyp5", "run this repository on any%5", or to open it to outside contributors with any%5.
---

# Installing any%5 on a repository

You install any%5 git mode on the user's repository with one command, `anyp5 setup`. It creates the inbox
issue, the `anyp5-ledger` branch, the `ANYP5_COORDINATOR_KEY` secret and the workflows, and commits the
workflows locally. Ask the human only for what you cannot find out yourself.

## 1. Check prerequisites (do not ask, check)

Run these from the repository's checkout and stop with a plain explanation if one fails:

- `anyp5 --help` works. If not: `pip install "anyp5 @ git+https://github.com/tibzejoker/anyPercent5@v0.4.2"`
  (use the latest release tag).
- `gh auth status` shows a login, and `gh repo view --json viewerPermission -q .viewerPermission` prints
  `ADMIN`. Without admin rights the secret cannot be set: the human must run setup themselves or get the role.
- `git status --porcelain` is empty, and the current branch is the default branch
  (`gh repo view --json defaultBranchRef -q .defaultBranchRef.name`). If the tree is dirty, ask the human to
  commit or stash their work; never stash or discard it yourself.
- `git ls-remote --exit-code origin refs/heads/anyp5-ledger` fails (exit code 2). If the branch exists,
  any%5 is already installed: report that and stop. Never pass `--force-new-key` on your own: it starts over
  with no members, tasks or calibration.

## 2. Find the test command

The merge queue runs the project's tests on every batch before `main` moves. Look for the command in CI
workflows (`.github/workflows/*.yml`), `Makefile`, `package.json` scripts, `pyproject.toml`/`tox.ini`,
`Cargo.toml`, `CMakeLists.txt`/`ctest`, `CONTRIBUTING.md` or the README.

- One obvious command (for example `npm test`, `cargo test`, `python -m pytest -q`): use it, and tell the
  human which one you chose.
- Several, none, or tests that need a toolchain the runner lacks: ask the human. Without `--test` there is no
  merge queue and approved changes land on `main` directly.
- If the tests need setup (a language runtime, system packages, a build), you will add those steps to
  `.github/workflows/anyp5-ci.yml` after setup, before its Test step, copying them from the project's
  existing CI.

## 3. Run setup

1. `anyp5 setup --dry-run --test "<command>"` and show the human the steps it prints.
2. `anyp5 setup --test "<command>"`. Add `--architects login ...` only if the human named direction owners,
   and `--admin login` for each extra maintainer they named.
3. Show the commit it made (`git show --stat HEAD`) and the setup steps you added to `anyp5-ci.yml`, if any.
4. **Ask before pushing `main`.** On a yes: `git push origin <default branch>`. Pushing workflow files needs the
   `workflow` scope (`gh auth refresh -s workflow`). If `main` is protected, tell the human the coordinator
   needs a bot token that can push to it.

The coordinator key goes straight from setup to the secret. Never print it, copy it, or ask for it.

## 4. Seed starter tasks (only if the human agrees)

Propose 3 to 8 small tasks, each one patch in size, from open issues (`gh issue list --label "good first
issue"`, then other open issues), TODOs in the README, or gaps you noticed. Show the list and wait for a yes.
Then join and open them:

```sh
anyp5 join --repo owner/name
anyp5 caps linux                     # what your machine can run
anyp5 task "Fix the off-by-one in parse_header" --requires linux
```

Tasks from admins open directly. Use `--requires` only for what the task really needs (`windows`, `gpu`...).

## 5. Explain how to invite contributors

Tell the human, in plain words:

- Contributors install the CLI, then run `anyp5 join --repo owner/name` and `anyp5 caps ...`. Their agents use
  the [contributor](../contributor/SKILL.md) and [reviewer](../reviewer/SKILL.md) skills.
- Link the README's wanted board (filled daily between the `anyp5:wanted` markers) from wherever newcomers
  arrive.
- While the maintainer is alone, their own changes have no reviewer: they keep pushing to `main` directly.
  any%5 handles outside contributions from the first newcomer on. A newcomer can contribute and review from
  day one; a change never needs more reviewers than the team has members who can review it.
- Below 100 members, a ban also needs a maintainer's confirmation.

## Rules

- Do not change the project's code, history or settings beyond what `anyp5 setup` does and the CI setup steps.
- Do not push without the human's yes. Do not create tasks without it.
- If a step fails, show the exact error and the step, and stop. Do not work around a missing permission.
