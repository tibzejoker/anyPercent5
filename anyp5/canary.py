"""Calibration canaries: a real, already merged diff with one known defect planted in an added line.

Reviewers get canaries through the same blind interface as real changes. Catching them (rejecting) is what
ranks reviewers. Projects can also drop hand-made or agent-made canaries into the ledger; these mutations are
only the zero-effort default.
"""
import hashlib
import re

MUTATIONS = [
    ("off-by-one bound", re.compile(r"(\b\w+\s*)<(\s*[\w.\->\[\]()]+)"), r"\1<=\2", "loosened a bound from < to <="),
    ("inverted check", re.compile(r"(\bif\s*\(.*?)==(.*\))"), r"\1!=\2", "inverted an equality check"),
    ("dropped guard", re.compile(r"^\s*if\s*\(.*\)\s*(return|throw|break|continue|goto)\b.*$"), None,
     "removed a guard clause"),
    ("hidden exec", re.compile(r"^(\s*)(\S.*;)\s*$"), r'\1\2 if (getenv("CI_DEBUG")) system(getenv("CI_DEBUG"));',
     "added a command execution driven by an environment variable"),
]


def _hunks(lines):
    """Index of each '@@' header for every line."""
    owner, current = [], None
    for i, line in enumerate(lines):
        if line.startswith("@@"):
            current = i
        owner.append(current)
    return owner


def _bump(header, delta):
    m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)", header)
    if not m:
        return header
    old_start, old_n, new_start, new_n, rest = m.groups()
    new_n = int(new_n if new_n is not None else 1) + delta
    old = f"-{old_start}" + (f",{old_n}" if old_n is not None else "")
    return f"@@ {old} +{new_start},{new_n} @@{rest}"


def plant(diff, seed):
    lines = diff.splitlines()
    owner = _hunks(lines)
    candidates = [i for i, l in enumerate(lines) if l.startswith("+") and not l.startswith("+++") and owner[i] is not None]
    if not candidates:
        return None
    pick = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    order = sorted(range(len(MUTATIONS)), key=lambda k: (k - pick) % len(MUTATIONS))
    for k in order:
        name, pattern, replacement, defect = MUTATIONS[k]
        start = pick % len(candidates)
        for j in range(len(candidates)):
            i = candidates[(start + j) % len(candidates)]
            body = lines[i][1:]
            if not pattern.search(body):
                continue
            out = list(lines)
            if replacement is None:
                del out[i]
                out[owner[i]] = _bump(out[owner[i]], -1)
                where = f"removed line {i}: {body.strip()}"
            else:
                mutated = pattern.sub(replacement, body, count=1)
                if mutated == body:
                    continue
                out[i] = "+" + mutated
                where = f"line {i}: {mutated.strip()}"
            return "\n".join(out) + "\n", f"{name}: {defect} ({where})"
    return None
