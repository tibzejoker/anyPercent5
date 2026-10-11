"""Calibration canaries: a copy of a pending change with one known defect planted in an added line, or left
clean (the right answer is then to approve).

Reviewers get canaries through the same blind interface as real changes. A planted canary counts as caught
only when the reject names the planted line (`path:line`, a few lines of slack), so rejecting everything earns
nothing; clean canaries measure false rejects. These mutations are a zero-effort default: projects should add
hand-made or agent-made canaries that look like their own defects (project recipes, `load_recipes`).

A project recipe is a dict:
  {"name": "unchecked length", "glob": "src/*.c", "match": REGEX, "replace": REPLACEMENT, "defect": "..."}
or "drop": true instead of "replace" (the matching added line is removed), or "hook": "module:function", a
function (line, path, seed) -> (mutated line, defect) or None. Recipes that apply to the pending change are
drawn before the built-in mutations.
"""
import fnmatch
import hashlib
import importlib
import json
import re
from pathlib import Path

IDENT = ["DEBUG_HOOK", "TRACE_CMD", "DIAG_RUN", "PROFILE_EXEC", "TEST_SHELL", "LOG_PIPE"]

MUTATIONS = [
    ("off-by-one bound", re.compile(r"(\b\w+\s*)<(\s*[\w.\->\[\]()]+)"), r"\1<=\2", "loosened a bound from < to <="),
    ("off-by-one bound", re.compile(r"(\b\w+\s*)>=(\s*[\w.\->\[\]()]+)"), r"\1>\2", "tightened a check from >= to >"),
    ("inverted check", re.compile(r"(\bif\s*\(.*?)==(.*\))"), r"\1!=\2", "inverted an equality check"),
    ("inverted check", re.compile(r"(\bif\s*\(.*?)!=(.*\))"), r"\1==\2", "inverted an inequality check"),
    ("dropped guard", re.compile(r"^\s*if\s*\(.*\)\s*(return|throw|break|continue|goto)\b.*$"), None,
     "removed a guard clause"),
    ("wrong size", re.compile(r"\bsizeof\s*\(\s*(\w+)\s*\)"), r"sizeof(\1) - 1", "shrank a size by one"),
    ("signedness", re.compile(r"\bunsigned\s+(int|long|char)\b"), r"\1", "made an unsigned value signed"),
    ("hidden exec", re.compile(r"^(\s*)(\S.*;)\s*$"), None, "added a command execution driven by the environment"),
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


def _where(lines, owner, i):
    """File path and new-file line number of diff line i."""
    path = None
    for line in lines[:i]:
        if line.startswith("+++ b/"):
            path = line[6:]
    m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", lines[owner[i]])
    number = int(m.group(1)) if m else 0
    number += sum(1 for line in lines[owner[i] + 1:i] if not line.startswith("-"))
    return path, number


def load_recipes(directory, key=None, hooks=False):
    """Recipes from a directory: *.json (one recipe or a list) and, given the coordinator key, *.sealed (the
    same JSON sealed to the coordinator, so reviewers cannot read which defects the project plants)."""
    root = Path(directory)
    if not root.is_dir():
        return []
    out = []
    for path in sorted(root.iterdir()):
        if path.suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
        elif path.suffix == ".sealed" and key is not None:
            from . import seal
            data = json.loads(seal.unseal(key, path.read_text(encoding="utf-8")))
        else:
            continue
        out.extend(check_recipe(r, hooks) for r in (data if isinstance(data, list) else [data]))
    return out


def check_recipe(recipe, hooks=True):
    """A recipe is data; a script hook runs code, so it is accepted only where the caller allows it."""
    if not isinstance(recipe, dict) or not recipe.get("name"):
        raise ValueError(f"canary recipe needs a name: {recipe!r}")
    if "hook" in recipe:
        if not hooks:
            raise ValueError(f"canary recipe {recipe['name']!r}: script hooks are not allowed here")
        return recipe
    if not recipe.get("defect") or "match" not in recipe or ("replace" not in recipe and not recipe.get("drop")):
        raise ValueError(f"canary recipe {recipe['name']!r} needs match, defect and replace (or drop)")
    re.compile(recipe["match"])
    return recipe


def _hook(spec):
    module, _, name = spec.partition(":")
    return getattr(importlib.import_module(module), name)


def _project(lines, owner, candidates, recipes, seed, pick):
    """Every (added line, recipe) pair a project recipe applies to; the seed draws one."""
    options = []
    for i in candidates:
        path, number = _where(lines, owner, i)
        body = lines[i][1:]
        for recipe in recipes:
            if not fnmatch.fnmatch(path or "", recipe.get("glob", "*")):
                continue
            if "hook" in recipe:
                result = _hook(recipe["hook"])(body, path, seed)
                if not result or result[0] == body:
                    continue
                mutated, defect = result
            elif not re.search(recipe["match"], body):
                continue
            elif recipe.get("drop"):
                mutated, defect = None, recipe["defect"]
            else:
                mutated, defect = re.sub(recipe["match"], recipe["replace"], body, count=1), recipe["defect"]
                if mutated == body:
                    continue
            options.append((i, path, number, recipe["name"], mutated, defect))
    if not options:
        return None
    i, path, number, name, mutated, defect = options[pick % len(options)]
    out = list(lines)
    if mutated is None:
        del out[i]
        out[owner[i]] = _bump(out[owner[i]], -1)
    else:
        out[i] = "+" + mutated
    return "\n".join(out) + "\n", f"{name}: {defect} ({path}:{number})"


def plant(diff, seed, recipes=()):
    """Plant one defect in an added line: a project recipe if one applies, else a built-in mutation."""
    lines = diff.splitlines()
    owner = _hunks(lines)
    candidates = [i for i, l in enumerate(lines) if l.startswith("+") and not l.startswith("+++") and owner[i] is not None]
    if not candidates:
        return None
    pick = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    if recipes:
        planted = _project(lines, owner, candidates, recipes, seed, pick)
        if planted is not None:
            return planted
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
            path, number = _where(lines, owner, i)
            if name == "dropped guard":
                del out[i]
                out[owner[i]] = _bump(out[owner[i]], -1)
            elif name == "hidden exec":
                var = IDENT[pick % len(IDENT)]
                out[i] = "+" + pattern.sub(rf'\1\2 if (getenv("{var}")) system(getenv("{var}"));', body, count=1)
            else:
                mutated = pattern.sub(replacement, body, count=1)
                if mutated == body:
                    continue
                out[i] = "+" + mutated
            return "\n".join(out) + "\n", f"{name}: {defect} ({path}:{number})"
    return None


def names_line(defect, reason, slack=3):
    """A reject catches a planted canary only if its reason points at the planted line."""
    m = re.search(r"\((.*):(\d+)\)$", defect or "")
    if not m:
        return False
    target = int(m.group(2))
    return any(abs(int(n) - target) <= slack for n in re.findall(r"\b\d+\b", reason or ""))
