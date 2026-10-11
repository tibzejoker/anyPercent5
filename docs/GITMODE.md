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
                           ├──> main: approved changes (and reverts)
                           └──> anyp5-ledger branch: boxes/<login>.sealed, tasks.json, status.json,
                                                      log.jsonl, members/, state.sealed
contributor + agent <──(anyp5 reviews / me: read and open your own sealed box)──┘
```

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
- **Only trusted inbox issues are read.** These are issues opened by the bot or an admin; anyone can open
  an issue with the same title.
- Commands carry the project name, and a login that already has keys cannot be re-keyed by posting a new
  join.
- **The Action never runs contributor code.** Comments are parsed as data, and patches are applied with
  `git am`.
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
```

If `main` is protected, give the Action a bot token that can push to it.

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

## Limits

- Latency: each command triggers a coordinator run (about 20 s on GitHub-hosted runners). There is also a
  schedule every 15 minutes, for leases and spare reviewers.
- One comment holds 65,000 characters, so a change of more than about 45 KB compressed must be split. Smaller
  changes are also easier to review well.
- Rate limits: each run reads the inbox with paginated API calls (100 comments per call). The scaling run in
  [sim/eval/scaling.md](../sim/eval/scaling.md) estimates the command rate per project size.
- Changes stay private until they are approved, so contributors cannot discuss a pending change in public. Put
  that discussion in the task, not in the diff.
