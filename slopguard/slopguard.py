#!/usr/bin/env python3
"""Turn repo conventions into failing checks.

The talk's point: a convention written in a comment (or CLAUDE.md) is a suggestion an
agent ignores; the same convention as a check is a wall it cannot walk through. This is
the layer no linter ships for you, because the rules are yours.

    ./slopguard.py [--rules FILE] [paths...]      # exit 1 on any deny

Rule file lines:  severity | path globs | forbidden regex | message
      path globs:  comma-separated, a leading ! excludes:  *.py,!test_*.py
Suppress one line in code with a trailing:  # slopguard: ok <why>
"""
import fnmatch, re, subprocess, sys
from pathlib import Path

SUPPRESS = re.compile(r"(#|//|--)\s*slopguard:\s*ok\b")


def load_rules(path: Path):
    rules = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # The regex field may itself contain "|", so bite the ends off, not the middle.
        try:
            sev, glob, rest = (p.strip() for p in line.split("|", 2))
            pattern, msg = (p.strip() for p in rest.rsplit("|", 1))
        except ValueError:
            sys.exit(f"{path}:{n}: need 4 fields (severity|glob|regex|message)")
        if sev not in ("deny", "warn"):
            sys.exit(f"{path}:{n}: severity must be deny or warn, got {sev!r}")
        rules.append((sev, glob, re.compile(pattern), msg))
    return rules


def tracked_files(paths):
    if paths:
        return [Path(p) for p in paths if Path(p).is_file()]
    # git-tracked files under cwd: never lint vendored or generated trees.
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True)
    files = [Path(p) for p in out.stdout.split("\n") if p] if out.returncode == 0 else []
    if files:
        return files
    # Not a repo, or nothing committed yet (a fresh `git init`, or a parent repo that
    # tracks none of this): walk instead, minus the usual generated trees.
    skip = {".git", ".venv", "node_modules", "__pycache__", "dist", "build"}
    return [p for p in Path(".").rglob("*")
            if p.is_file() and not skip & set(p.parts)]


def matches(path: Path, glob: str) -> bool:
    """Comma-separated globs; a leading ! excludes. `*.py,!test_*.py` = python, not tests.

    Tests break production rules on purpose (bare asserts, fixtures full of the very
    patterns you ban), so a rule set without exclusions is one you will switch off.
    """
    p = path.as_posix()
    hit = False
    for g in (g.strip() for g in glob.split(",")):
        neg, g = (True, g[1:]) if g.startswith("!") else (False, g)
        if fnmatch.fnmatch(p, g) or fnmatch.fnmatch(p, f"*/{g}"):
            if neg:
                return False
            hit = True
    return hit


def check(rules, files):
    findings = []
    for f in files:
        applicable = [r for r in rules if matches(f, r[1])]
        if not applicable:
            continue
        try:
            lines = f.read_text().splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(lines, 1):
            if SUPPRESS.search(line):
                continue
            for sev, _glob, pattern, msg in applicable:
                if pattern.search(line):
                    findings.append((sev, f, i, msg, line.strip()[:100]))
    return findings


def main(argv):
    rules_file = Path(__file__).parent / "slopguard.rules"
    if "--rules" in argv:
        i = argv.index("--rules")
        rules_file = Path(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    findings = check(load_rules(rules_file), tracked_files(argv))
    for sev, f, line, msg, src in findings:
        print(f"{f}:{line}: {sev}: {msg}\n    {src}")
    denied = sum(1 for f in findings if f[0] == "deny")
    warned = len(findings) - denied
    print(f"slopguard: {denied} deny, {warned} warn", file=sys.stderr)
    return 1 if denied else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
