# Git mode: any%5 without a server

Nothing to host. A branch of the project is the ledger, one issue is the inbox, and a GitHub Action is the
coordinator. It was tested end to end on GitHub. A private patch was reviewed blind, approved, then applied and
pushed to `main` by the Action, with the author kept.

```
contributor + agent ──(anyp5 CLI: sealed, signed issue comment)──> "anyp5 inbox" issue
                                                                      │ triggers
                                                                      ▼
                         GitHub Action "any%5 coordinator" (holds the coordinator key as a secret)
                           │ reads commands, runs the engine, applies approved patches with git am
                           ├──> anyp5-queue: a batch of approved changes on top of main
                           │      └─> workflow "anyp5-ci" runs the project's tests on it
                           ├──> main: fast-forwarded to a green batch (and reverts)
                           └──> anyp5-ledger branch: boxes/<login>.sealed, tasks.json, status.json,
                                                      log.jsonl, members/, state.sealed
contributor + agent <──(anyp5 reviews / me: read and open your own sealed box)──┘
```

## Merge queue: nothing reaches `main` without CI

Reviewers check intent; tests check behaviour. With the CI workflow installed, approved changes go through a
merge queue run by the coordinator itself (git mode has no pull requests, so GitHub's merge queue does not
apply). There is one lane and it advances once per coordinator pass:

1. Approved changes are batched, up to `ci.batch` (default 16, see [bench/RUNNERS.md](../bench/RUNNERS.md)).
   The batch is applied on top of `main`, one commit per change with the same checks as before (exactly one
   commit, same lines as reviewed, author rewritten). The result is force-pushed to `anyp5-queue`, and the
   coordinator starts `anyp5-ci.yml` there with `workflow_dispatch`. A push made with the Action's own token
   starts no workflow; a dispatch does.
2. A later pass reads the `anyp5-ci` check run of that exact commit (`gh api .../commits/<sha>/check-runs`,
   GitHub Actions runs only).
   - **Green**: `main` is fast-forwarded to the candidate. What lands is the tree that passed.
   - **Red**: the batch is split in halves and the first half is tested first, then the second, down to the
     change that fails alone. That change goes to state `ci_failed`. Its author sees it in their box with the
     failing commit and the run link. It is not a ban and costs no calibration: an honest bug never costs
     membership. The author fixes it and submits again. The other changes land.
   - **No result** after `ci.timeout_minutes` (default 60), or a cancelled run: the batch is rebuilt and
     tested again.
   - **Stale**: if `main` moved, the queue branch was overwritten, or a change of the batch was withdrawn
     (its author was banned), the remaining changes are rebuilt on the new `main` and tested again.
3. Everything a pass moves (`main` and `anyp5-queue`) goes in one atomic push. If it is refused, nothing is
   recorded and the next pass starts over. Reverts after a ban are still pushed to `main` directly: they undo
   code that should not be there, and waiting for CI would keep it there longer.

Changes under `.github/` still wait for a maintainer. So a batch never changes the workflow that tests it:
`anyp5-ci.yml` always runs as it is on `main`.

**Default: `ci.required` is `auto`.** The queue is on when `main` holds `.github/workflows/anyp5-ci.yml`, and
off otherwise. Off means approved changes are pushed to `main` directly, as before. A project with tests gets
the gate as soon as it installs the workflow. A project without tests gets no queue: no result would ever come
and nothing would land. Set `ci.required` to `true` to stop instead of pushing directly if the workflow
disappears, or to `false` to turn the queue off. Settings go in `anyp5.json` under `config.ci`: `required`,
`batch`, `timeout_minutes`, `branch` (`anyp5-queue`), `workflow` (`anyp5-ci.yml`), `check` (`anyp5-ci`).
`status.json` shows the candidate under test and how many changes wait.

Cost: one CI run per batch while it stays green, plus about 2 log2(batch) runs to isolate one culprit. The
model in [bench/RUNNERS.md](../bench/RUNNERS.md) gives 0.29-0.37 runs per landed change at 3 % breakage. One
lane lands about `batch x runs/day` changes. Past that, shard by area.

## Why it keeps canaries blind

The [canary gaming simulation](../sim/eval/canary_gaming.md) shows the rule. Never build a canary from anything
public. If a canary comes from a merged diff, a reviewer finds it with `git log`. Such reviewers then rise in the
ranking, and the share of backdoor attempts that ship goes from 16 % to 36 %. So in git mode:

- Changes are sent **privately**: `git format-patch` output, sealed to the coordinator. Nothing about a change
  is public before it is approved.
- A canary is a **copy of a pending change with one planted defect**. Nobody receives two versions of the same
  change, and the author never receives a copy of their own change. Simulated gamers recognised 6-7 % of these
  canaries, even when all attackers pooled what they saw, and leaks returned to the no-gaming level.
- Everything public is sealed (`anyp5-sealed-v1:`). The issue comments, the boxes and the engine state are
  unreadable. The public log only holds joins, merges (commit, tier), reverts, bans, and the commitment
  and later reveal of each week's draw seed.

## Security model

Reviewed by five independent red-team agents ([docs/REDTEAM.md](REDTEAM.md)). The breaks they found are
fixed and covered by tests:

- **Identity**: joining is posted by the GitHub account it names, so GitHub vouches for the login. That same
  comment registers the member's keys. After that, every command is signed with the member's Ed25519 key and
  carries an increasing sequence number. Forged and replayed commands are refused (see `tests/test_gitmode.py`).
- **Confidentiality**: X25519 + HKDF-SHA256 + ChaCha20-Poly1305 (`anyp5/seal.py`, `cryptography` package).
- **What merges is what was reviewed.** A patch holding more than one commit is refused. After `git am`,
  exactly one commit must have landed, with the same files and lines as the reviewed diff. Renames, binary
  content, mode changes and symlinks are tier red. The commit is attributed to the member who sent it.
- **CI changes wait for a maintainer.** Approved changes under `.github/` are never pushed by the
  coordinator, so merged code cannot rewrite the workflow that holds the key. Never give the Action a token
  that can push workflows.
- **One bad comment cannot stop it.** Every command error is contained and the inbox always moves on. Sealed
  messages over 8 MiB uncompressed are refused, and sealed sizes are padded to powers of two, so a comment's
  length does not reveal its patch.
- **Order within a pass reveals nothing.** Reviews are drawn, boxes written and log entries appended in an
  order set by the hidden weekly seed, not by the order of the comments, and the canary is drawn among the
  real changes rather than last. A box carries only the time of the pass that wrote it.
- **Only trusted inbox issues are read.** These are issues opened by the bot or an admin; anyone can open
  an issue with the same title.
- Commands carry the project name, and a login that already has keys cannot be re-keyed by posting a new
  join.
- **The Action never runs contributor code.** Comments are parsed as data, and patches are applied with
  `git am`. Contributor code runs only in the separate `anyp5-ci` workflow, which has no secrets and a
  read-only token.
- **The coordinator key is the trust root.** Keep it only in the `ANYP5_COORDINATOR_KEY` secret. Whoever holds
  it can read the state, including which reviews are canaries.
- `admit` (maintainers only, listed in `anyp5.json`) adds a member who has no GitHub account, such as a bot.

## Set up (maintainer, about 5 minutes)

```sh
pip install "anyp5 @ git+https://github.com/tibzejoker/anyPercent5@v0.2.0"
gh issue create --title "anyp5 inbox" --body "Sealed any%5 commands."           # note its number N
git checkout --orphan anyp5-ledger && git rm -rq --cached . && git clean -fdq
anyp5 init-ledger --ledger . --project owner/name --issue N --admin your-login | tail -1 \
  | gh secret set ANYP5_COORDINATOR_KEY
git add anyp5.json && git commit -m "anyp5 ledger" && git push origin anyp5-ledger
git checkout main
cp <anyPercent5>/templates/anyp5-coordinator.yml .github/workflows/ && git commit -am "ci: any%5 coordinator"
# merge queue (recommended when the project has tests): set the test command in the last step first
cp <anyPercent5>/templates/anyp5-ci.yml .github/workflows/ && git add .github && git commit -m "ci: any%5 merge queue"
```

If `main` is protected, give the Action a bot token that can push to it.

To give the project direction owners, add `"architects": ["login", ...]` to `config` in `anyp5.json`. Their
box then lists the task proposals waiting for them, and they answer with `anyp5 direct TASK approve` or
`anyp5 direct TASK close --reason "..."` ([METHOD.md](METHOD.md#1-work-is-leased-never-owned)).

## Contribute

```sh
anyp5 join --repo owner/name          # creates your key in ~/.anyp5.json, posts the join request
anyp5 reviews                         # jury duty first: diffs only, no author, no title
anyp5 verdict 12 reject --reason "src/parse.c:88 bound should be < len"
anyp5 tasks && anyp5 claim 3
git format-patch origin/main --stdout | anyp5 submit
anyp5 me                              # your changes, their state and commit, your calibration
```

The CLI uses your `gh` login to post comments and read the ledger branch.

## Project canaries

The built-in mutations (`anyp5/canary.py`) are generic, and a reviewer who has seen a few can recognise them.
A project can add its own **recipes**. When a recipe applies to the pending change, it is drawn before the
built-in mutations. The canary is still a copy of a pending change, the defect is still reported as
`(path:line)`, and clean canaries are unchanged.

```json
[{"name": "short copy length", "glob": "core/libs/*.cpp",
  "match": "(\\w+)\\.size\\(\\)\\s*\\*\\s*sizeof\\((\\w+)\\)", "replace": "(\\1.size() - 1) * sizeof(\\2)",
  "defect": "the copy drops the last element"},
 {"name": "missing queue fence", "glob": "*/Queue*.cpp", "match": "^\\s*(\\w+)->WaitIdle\\(\\);\\s*$",
  "drop": true, "defect": "removed the wait before the buffer is reused"}]
```

A recipe has a `glob` on the file path (`*` also matches `/`), a regex `match` on an added line, and either a
`replace` (regex replacement) or `"drop": true` (the line is removed). In server mode, a recipe can instead
name a Python `hook` (`"module:function"`, called with the line, the path and a seed, returning the mutated
line and the defect). Git mode refuses hooks, because the Action never runs code from the ledger. A fuller
example is in [templates/canaries/example.json](../templates/canaries/example.json).

Keep recipes private. A public recipe tells reviewers what to look for, and tells a ring which canaries to
skip. `anyp5 seal-canaries recipes.json --ledger .` checks the file and seals it to the coordinator as
`canaries/recipes.sealed` on the ledger branch. Plain `canaries/*.json` files also load, and so does
`"canaries": [...]` in `anyp5.json` (`config`) or in a server `--config` file.

**Writing good canaries.** A good canary looks like a bug this project really had:
- Mine the fix commits (`git log --grep '^fix'`, reverts, regression tests). For each one, write the recipe
  that turns the fixed line back into the bug. An agent can do this: give it the fix commits and ask for one
  recipe per bug class, with a glob as narrow as the files where the bug lived.
- Prefer defects that change behaviour on one line: a bound, a missing wait or lock, a wrong mask, a dropped
  error check, a swapped argument. A reviewer must be able to name the line.
- Have many recipes and replace them over time, so that no single one becomes familiar. With one recipe that
  applies to most changes, every planted canary carries the same defect.
- Never use a recipe that makes the diff fail to compile or reads like a joke. Those are found from their
  look, not from review.

## Limits

- Latency: each command triggers a coordinator run (about 20 s on GitHub-hosted runners). There is also a
  schedule every 15 minutes, for leases and spare reviewers.
- One comment holds 65,000 characters, so a change of more than about 45 KB compressed must be split. Smaller
  changes are also easier to review well.
- Rate limits: each run reads the inbox with paginated API calls (100 comments per call). The scaling run in
  [sim/eval/scaling.md](../sim/eval/scaling.md) estimates the command rate per project size.
- Changes stay private until they are approved, so contributors cannot discuss a pending change in public. Put
  that discussion in the task, not in the diff.
