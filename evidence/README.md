# evidence: proof bundles for a pull request

Builds the PR's tests twice: on the base, with only the PR's test files applied, and on the branch. It runs
them, classifies the diff by risk and writes `evidence.json`, `SUMMARY.md` and logs. A signature is
optional (`ssh-keygen`). `verify` redoes the collection independently and compares the results.

```sh
python evidence.py collect <branch> [--base origin/main] [--sign ~/.ssh/key] --out out/ --profile example-cmake
python evidence.py verify out/evidence.json --allowed-signers allowed_signers --identity <name>
python evidence.py scan <ref>...      # risk classification only, seconds
```

| Level | When |
|-------|------|
| GREEN | a changed test fails on the base and passes on the branch, conventions pass, no sensitive surface |
| AMBER | no test, or the test only fails to *compile* on the base (weak proof), large diff, sensitive APIs (lines listed) |
| RED   | CI, build scripts, dependencies, binaries, network, process, registry, URLs or opaque blobs in code, failing tests |

Profiles (`profiles/*.json`) hold the build, test and convention commands and the risk rules. Run `verify`
in a sandbox without network or secrets: it executes the PR's code.
