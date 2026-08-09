#!/usr/bin/env python3
"""Discover which AstroPack tests actually pass, and write ci/suite.json.

The repository has 286 files named ``test*.m``, but only ~82 are real
function-based suites, and an unknown subset of those pass on a clean machine
(others need MEX builds, ~/matlab/data, catsHTM mounts, a database, or the
network). Guessing that subset is how you get a CI that is red forever and
therefore ignored.

So we measure it. Each candidate runs in its own MATLAB process with a
timeout, so a crash or an infinite loop isolates to one file. The verdicts are
turned into a green tier (enforced by CI) and a quarantine list (each entry
carrying the reason it was excluded).

This is a bootstrap step, not a per-push step. Run it when you first set CI
up, and re-run it periodically to promote files out of quarantine.

Usage
-----
    python3 ci/triage.py                       # full triage, writes suite.json
    python3 ci/triage.py --jobs 4              # parallel MATLAB processes
    python3 ci/triage.py --only tests/astro    # restrict to a subtree
    python3 ci/triage.py --dry-run             # list candidates, run nothing
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lint"))
from matlab_lex import lex  # noqa: E402
from check_conventions import (  # noqa: E402
    _FUNCTIONTESTS_RE,
    _primary_name,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SUITE_JSON = REPO_ROOT / "ci" / "suite.json"
GENERATED_JSON = REPO_ROOT / "ci" / "suite.generated.json"
REPORT_MD = REPO_ROOT / "ci" / "triage-report.md"

# Verdicts that earn a place in the enforced tier.
GREEN_STATUSES = {"passed", "skipped"}


def _rel(path: Path) -> str:
    """Repo-relative display path, falling back to absolute when outside."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def discover_candidates(root: Path, only: str | None) -> list[str]:
    """Test files that MATLAB will actually build into a suite."""
    tests_dir = root / "tests"
    candidates: list[str] = []
    for path in sorted(tests_dir.rglob("*.m")):
        rel = path.relative_to(root).as_posix()
        if only and not rel.startswith(only.rstrip("/")):
            continue
        if not path.stem.lower().startswith("test"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        result = lex(text)
        name, kind = _primary_name(result.code_lines)
        if kind != "function" or name != path.stem:
            continue  # not discoverable by filename
        if not _FUNCTIONTESTS_RE.search(result.code_text()):
            continue  # not a function-based suite
        candidates.append(rel)
    return candidates


def run_one(rel: str, matlab: str, timeout: int, workdir: Path) -> dict:
    """Run a single test file in its own MATLAB process."""
    safe = rel.replace("/", "__").replace("\\", "__")
    out_json = workdir / f"{safe}.json"
    ci_matlab = (REPO_ROOT / "ci" / "matlab").as_posix()
    command = (
        f"addpath('{ci_matlab}'); "
        f"ciRunOneTestFile('{rel}', '{out_json.as_posix()}');"
    )
    cmd = [matlab, "-batch", command]

    try:
        proc = subprocess.run(
            cmd, cwd=REPO_ROOT, timeout=timeout,
            capture_output=True, text=True,
        )
    except subprocess.TimeoutExpired:
        return {
            "file": rel, "status": "timeout", "total": 0, "passed": 0,
            "failed": 0, "skipped": 0, "durationSeconds": float(timeout),
            "message": f"Exceeded {timeout}s -- likely a hang, a blocking "
                       f"network call, or a prompt waiting on input.",
        }
    except FileNotFoundError:
        raise SystemExit(
            f"MATLAB executable not found: {matlab!r}. "
            f"Pass --matlab /path/to/matlab."
        )

    if out_json.exists():
        try:
            return json.loads(out_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return {
                "file": rel, "status": "crash", "total": 0, "passed": 0,
                "failed": 0, "skipped": 0, "durationSeconds": 0.0,
                "message": f"Unparseable verdict JSON: {exc}",
            }

    tail = (proc.stderr or proc.stdout or "").strip()[-1500:]
    return {
        "file": rel, "status": "crash", "total": 0, "passed": 0,
        "failed": 0, "skipped": 0, "durationSeconds": 0.0,
        "message": f"MATLAB exited {proc.returncode} without writing a "
                   f"verdict.\n{tail}",
    }


def build_suite_config(verdicts: list[dict], today: str) -> dict:
    green, quarantine = [], []
    for v in sorted(verdicts, key=lambda x: x["file"]):
        if v["status"] in GREEN_STATUSES:
            green.append(v["file"])
        else:
            quarantine.append({
                "file": v["file"],
                "status": v["status"],
                "since": today,
                "reason": (v.get("message") or "").strip()[:500]
                          or f"Triage verdict: {v['status']}",
            })
    return {
        "_comment": (
            "Generated by ci/triage.py. 'green' is the enforced tier -- CI "
            "fails when any of these fail. 'quarantine' files are excluded "
            "with a recorded reason; fix one and re-run triage to promote it. "
            "Set 'enforce' to true once the green tier is stable."
        ),
        "generated": today,
        "enforce": False,
        "green": green,
        "quarantine": quarantine,
    }


def write_report(verdicts: list[dict], path: Path, today: str) -> None:
    by_status: dict[str, list[dict]] = {}
    for v in verdicts:
        by_status.setdefault(v["status"], []).append(v)

    total_cases = sum(v.get("total", 0) for v in verdicts)
    passed_cases = sum(v.get("passed", 0) for v in verdicts)

    lines = [
        "# Test triage report",
        "",
        f"Generated {today} by `ci/triage.py`.",
        "",
        f"- Candidate files: **{len(verdicts)}**",
        f"- Test cases built: **{total_cases}** ({passed_cases} passed)",
        "",
        "## Verdicts",
        "",
        "| Status | Files | Meaning |",
        "|---|---:|---|",
    ]
    meanings = {
        "passed": "Every case passed. Enforced by CI.",
        "skipped": "All cases skipped via assumeTrue. Enforced (harmless).",
        "failed": "Built and ran, but assertions failed.",
        "error": "Could not build or threw before assertions.",
        "empty": "Built zero test cases.",
        "timeout": "Hung past the per-file timeout.",
        "crash": "Killed MATLAB without producing a verdict.",
    }
    for status in ("passed", "skipped", "failed", "error", "empty",
                   "timeout", "crash"):
        items = by_status.get(status, [])
        if items:
            lines.append(f"| `{status}` | {len(items)} | "
                         f"{meanings.get(status, '')} |")

    for status in ("failed", "error", "empty", "timeout", "crash"):
        items = sorted(by_status.get(status, []), key=lambda x: x["file"])
        if not items:
            continue
        lines += ["", f"## {status} ({len(items)})", ""]
        for v in items:
            msg = " ".join((v.get("message") or "").split())[:300]
            lines.append(f"- `{v['file']}`")
            if msg:
                lines.append(f"  - {msg}")

    green = sorted(by_status.get("passed", []) + by_status.get("skipped", []),
                   key=lambda x: x["file"])
    if green:
        lines += ["", f"## green tier ({len(green)})", ""]
        for v in green:
            lines.append(f"- `{v['file']}` "
                         f"({v.get('passed', 0)}/{v.get('total', 0)} passed)")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--matlab", default=shutil.which("matlab") or "matlab",
                    help="MATLAB executable (default: first on PATH)")
    ap.add_argument("--jobs", type=int, default=2,
                    help="Concurrent MATLAB processes (default: 2). Raise "
                         "only if your licence allows it.")
    ap.add_argument("--timeout", type=int, default=300,
                    help="Per-file timeout in seconds (default: 300)")
    ap.add_argument("--only", help="Restrict to a path prefix, "
                                   "e.g. tests/astro/+celestial")
    ap.add_argument("--dry-run", action="store_true",
                    help="List candidates and exit without running MATLAB")
    ap.add_argument("--out", type=Path, default=GENERATED_JSON,
                    help=f"Where to write the suite config "
                         f"(default: {GENERATED_JSON.name})")
    ap.add_argument("--promote", action="store_true",
                    help="Also write ci/suite.json directly (overwrites it)")
    args = ap.parse_args(argv)

    today = dt.date.today().isoformat()
    candidates = discover_candidates(REPO_ROOT, args.only)
    print(f"Discovered {len(candidates)} runnable test file(s).")

    if args.dry_run:
        for c in candidates:
            print(f"  {c}")
        return 0
    if not candidates:
        print("Nothing to triage.")
        return 1

    verdicts: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="astropack-triage-") as tmp:
        workdir = Path(tmp)
        with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
            futures = {
                pool.submit(run_one, rel, args.matlab, args.timeout, workdir): rel
                for rel in candidates
            }
            for i, fut in enumerate(
                concurrent.futures.as_completed(futures), start=1
            ):
                v = fut.result()
                verdicts.append(v)
                print(f"[{i}/{len(candidates)}] {v['status']:<8} {v['file']}",
                      flush=True)

    config = build_suite_config(verdicts, today)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    write_report(verdicts, REPORT_MD, today)

    if args.promote:
        SUITE_JSON.write_text(json.dumps(config, indent=2) + "\n",
                              encoding="utf-8")

    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v["status"]] = counts.get(v["status"], 0) + 1
    print("\n=== Triage summary ===")
    for status in sorted(counts):
        print(f"  {status:<8} {counts[status]}")
    print(f"\nGreen tier   : {len(config['green'])}")
    print(f"Quarantined  : {len(config['quarantine'])}")
    print(f"Suite config : {_rel(args.out)}")
    print(f"Report       : {_rel(REPORT_MD)}")
    if not args.promote:
        print(f"\nReview it, then copy over ci/suite.json to adopt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
