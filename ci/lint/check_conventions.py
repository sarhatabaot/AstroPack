#!/usr/bin/env python3
"""Static conventions check for AstroPack MATLAB tests -- needs no MATLAB.

Catches the failures that silently remove a test from the suite (so it never
runs and never reports red) and the machine-specific paths that make a test
pass on one laptop and fail everywhere else.

Rules
-----
name-mismatch     Primary function/classdef name differs from the filename.
                  MATLAB's ``runtests`` / ``TestSuite.fromFolder`` discover by
                  filename; a mismatch means the file is silently skipped.
                  (tests/CLAUDE.md sec.2, rule 5.)
no-suite          A ``test*.m`` file whose primary function never calls
                  ``functiontests(localfunctions)`` -- contributes no tests.
no-test-functions A file that builds a suite but defines no ``test*`` local
                  functions -- an empty suite that always "passes".
abs-path          A hardcoded absolute path literal in code. Root CLAUDE.md:
                  "No hardcoded absolute paths -- use @Configuration or
                  environment variables."

Ratchet
-------
Known pre-existing violations live in a baseline file and do not fail the
build. Anything new does. Fix a legacy violation and drop its baseline entry
to lock the improvement in.

Usage
-----
    python3 ci/lint/check_conventions.py                    # check (CI default)
    python3 ci/lint/check_conventions.py --update-baseline  # accept current state
    python3 ci/lint/check_conventions.py --github           # + GH annotations
    python3 ci/lint/check_conventions.py --json out.json    # machine-readable
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from matlab_lex import lex  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASELINE = REPO_ROOT / "ci" / "lint" / "baseline.json"
DEFAULT_PATHS = ["tests"]

ERROR = "error"
WARNING = "warning"

# function [a, b] = name(args)  |  function out = name  |  function name(args)
_FUNCTION_RE = re.compile(
    r"^\s*function\s+"
    r"(?:(?:\[[^\]]*\]|[A-Za-z]\w*)\s*=\s*)?"
    r"([A-Za-z]\w*)\s*(?:\(|$)"
)
_CLASSDEF_RE = re.compile(r"^\s*classdef\s+(?:\([^)]*\)\s*)?([A-Za-z]\w*)")
_FUNCTIONTESTS_RE = re.compile(r"\bfunctiontests\s*\(\s*localfunctions\s*\)")
_LOCAL_TEST_FN_RE = re.compile(r"^\s*function\s+(test\w*)\s*\(", re.IGNORECASE)

# Absolute-path shapes. URLs never match: they start with a scheme letter.
_DRIVE_RE = re.compile(r"^[A-Za-z]:[/\\]")
_UNC_RE = re.compile(r"^\\\\[^\\]")
_HOME_RE = re.compile(r"^~[/\\]")
_POSIX_ROOTS = (
    "home", "Users", "mnt", "media", "opt", "srv", "euclid",
    "data", "usr", "var", "tmp", "scratch", "work", "raid", "storage",
)


@dataclass(frozen=True)
class Violation:
    file: str  # repo-relative, forward slashes
    line: int
    rule: str
    severity: str
    message: str
    key: str  # stable identity for baselining (survives line moves)

    def fingerprint(self) -> tuple[str, str, str]:
        return (self.file, self.rule, self.key)


def is_absolute_path_literal(value: str) -> bool:
    v = value.strip()
    if not v or "\n" in v:
        return False
    # '~/' on its own is the single most common offender in this repo
    # (fullfile('~/','matlab','AstroPack',...)), so it must match at len 2.
    if _HOME_RE.match(v):
        return True
    if len(v) < 3:
        return False
    if _DRIVE_RE.match(v) or _UNC_RE.match(v):
        return True
    if v.startswith("/"):
        first = v[1:].split("/", 1)[0]
        return first in _POSIX_ROOTS
    return False


def _primary_name(code_lines: list[str]) -> tuple[str | None, str | None]:
    """Return (name, kind) of the first function/classdef, or (None, None)."""
    for line in code_lines:
        m = _CLASSDEF_RE.match(line)
        if m:
            return m.group(1), "classdef"
        m = _FUNCTION_RE.match(line)
        if m:
            return m.group(1), "function"
    return None, None


def check_file(path: Path, repo_root: Path) -> list[Violation]:
    rel = path.relative_to(repo_root).as_posix()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:  # unreadable file is itself worth reporting
        return [Violation(rel, 1, "unreadable", ERROR, f"Cannot read: {exc}", "")]

    result = lex(text)
    stem = path.stem
    is_test_file = stem.lower().startswith("test")
    violations: list[Violation] = []

    name, kind = _primary_name(result.code_lines)
    if name is not None and name != stem:
        line = next(
            (i for i, l in enumerate(result.code_lines, 1)
             if _CLASSDEF_RE.match(l) or _FUNCTION_RE.match(l)),
            1,
        )
        violations.append(Violation(
            rel, line, "name-mismatch", ERROR,
            f"Primary {kind} is '{name}' but the file is '{stem}.m'. "
            f"MATLAB discovers tests by filename, so this file is skipped.",
            key=name,
        ))

    if is_test_file and kind == "function":
        code = result.code_text()
        has_suite = bool(_FUNCTIONTESTS_RE.search(code))
        if not has_suite:
            violations.append(Violation(
                rel, 1, "no-suite", ERROR,
                "Test file never calls functiontests(localfunctions); it "
                "contributes no tests to the suite.",
                key="",
            ))
        else:
            locals_found = [
                m.group(1) for line in result.code_lines
                if (m := _LOCAL_TEST_FN_RE.match(line))
            ]
            # The primary function itself matches test*, so require >1.
            if len([n for n in locals_found if n != name]) == 0:
                violations.append(Violation(
                    rel, 1, "no-test-functions", WARNING,
                    "Builds a suite but defines no test* local functions; "
                    "the suite is empty and always passes.",
                    key="",
                ))

    for lit in result.strings:
        if is_absolute_path_literal(lit.value):
            violations.append(Violation(
                rel, lit.line, "abs-path", WARNING,
                f"Hardcoded absolute path {lit.quote}{lit.value}{lit.quote}. "
                f"Use a path derived from mfilename('fullpath'), @Configuration, "
                f"or an environment variable.",
                key=lit.value,
            ))

    return violations


def collect(paths: list[str], repo_root: Path) -> list[Violation]:
    files: list[Path] = []
    for p in paths:
        target = (repo_root / p) if not Path(p).is_absolute() else Path(p)
        if target.is_file():
            files.append(target)
        else:
            files.extend(sorted(target.rglob("*.m")))
    out: list[Violation] = []
    for f in files:
        out.extend(check_file(f, repo_root))
    return out


def load_baseline(path: Path) -> set[tuple[str, str, str]]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return {(v["file"], v["rule"], v.get("key", "")) for v in data.get("violations", [])}


def save_baseline(path: Path, violations: list[Violation]) -> None:
    payload = {
        "_comment": (
            "Known pre-existing convention violations, accepted so CI is not "
            "red on day one. New violations fail the build. Remove entries as "
            "you fix them -- never add by hand, run --update-baseline."
        ),
        "violations": [
            {"file": v.file, "rule": v.rule, "key": v.key, "message": v.message}
            for v in sorted(violations, key=lambda v: (v.file, v.rule, v.key))
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="*", default=DEFAULT_PATHS,
                    help="Files or directories to scan (default: tests)")
    ap.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    ap.add_argument("--no-baseline", action="store_true",
                    help="Report every violation, ignoring the baseline")
    ap.add_argument("--update-baseline", action="store_true",
                    help="Rewrite the baseline to accept the current state")
    ap.add_argument("--github", action="store_true",
                    help="Emit GitHub Actions annotations")
    ap.add_argument("--json", type=Path, help="Write full results as JSON")
    ap.add_argument("--fail-on", choices=[ERROR, WARNING], default=WARNING,
                    help="Minimum severity of a NEW violation that fails "
                         "(default: warning)")
    args = ap.parse_args(argv)

    paths = args.paths or DEFAULT_PATHS
    violations = collect(paths, args.repo_root)

    if args.update_baseline:
        save_baseline(args.baseline, violations)
        print(f"Baseline updated: {args.baseline.relative_to(args.repo_root)} "
              f"({len(violations)} violations accepted)")
        return 0

    baseline = set() if args.no_baseline else load_baseline(args.baseline)
    new = [v for v in violations if v.fingerprint() not in baseline]
    known = len(violations) - len(new)
    seen = {v.fingerprint() for v in violations}
    stale = sorted(baseline - seen)

    by_rule: dict[str, int] = {}
    for v in violations:
        by_rule[v.rule] = by_rule.get(v.rule, 0) + 1

    print(f"Scanned {', '.join(paths)}: {len(violations)} violations "
          f"({known} known, {len(new)} new)")
    for rule, count in sorted(by_rule.items()):
        print(f"  {rule:<18} {count}")

    if new:
        print("\nNew violations:")
        for v in sorted(new, key=lambda v: (v.severity != ERROR, v.file, v.line)):
            print(f"  {v.severity.upper():<7} {v.file}:{v.line}  [{v.rule}]")
            print(f"          {v.message}")
            if args.github:
                msg = v.message.replace("\n", " ")
                print(f"::{v.severity} file={v.file},line={v.line},"
                      f"title={v.rule}::{msg}")

    if stale:
        print(f"\n{len(stale)} baseline entries no longer apply (fixed?). "
              f"Run --update-baseline to drop them:")
        for f, rule, key in stale[:20]:
            print(f"  {f}  [{rule}]  {key}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({
            "total": len(violations),
            "known": known,
            "new": [asdict(v) for v in new],
            "all": [asdict(v) for v in violations],
            "stale_baseline": [
                {"file": f, "rule": r, "key": k} for f, r, k in stale
            ],
        }, indent=2) + "\n", encoding="utf-8")

    threshold = {ERROR: (ERROR,), WARNING: (ERROR, WARNING)}[args.fail_on]
    failing = [v for v in new if v.severity in threshold]
    if failing:
        print(f"\nFAILED: {len(failing)} new violation(s) at or above "
              f"'{args.fail_on}'.")
        return 1
    print("\nOK: no new violations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
