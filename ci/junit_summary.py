#!/usr/bin/env python3
"""Render a JUnit XML file as a readable summary.

MATLAB's XMLPlugin writes standards-compliant JUnit XML, which is precise and
unreadable. Downloading a zip to squint at raw XML is not a workflow, so this
turns it into a table: printed to the terminal locally, and appended to the
GitHub Actions job summary in CI so results appear on the run page itself with
nothing to download.

Stdlib only, and no third-party reporting action -- one less thing with write
access to the repo.

Usage
-----
    python3 ci/junit_summary.py                       # ci/results/junit.xml
    python3 ci/junit_summary.py path/to/junit.xml
    python3 ci/junit_summary.py --markdown            # force markdown output
    python3 ci/junit_summary.py --fail-on-failure     # exit 1 if any failed
"""

from __future__ import annotations

import argparse
import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_XML = REPO_ROOT / "ci" / "results" / "junit.xml"
MAX_DETAIL_CHARS = 1500


@dataclass
class Case:
    suite: str
    name: str
    time: float
    status: str  # passed | failed | error | skipped
    message: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.suite}.{self.name}" if self.suite else self.name


@dataclass
class Suite:
    name: str
    cases: list[Case] = field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(1 for c in self.cases if c.status == status)

    @property
    def time(self) -> float:
        return sum(c.time for c in self.cases)


def _float(value: str | None) -> float:
    try:
        return float(value) if value else 0.0
    except ValueError:
        return 0.0


def _detail(node: ET.Element) -> str:
    parts = [node.get("message") or "", (node.text or "").strip()]
    text = "\n".join(p for p in parts if p).strip()
    if len(text) > MAX_DETAIL_CHARS:
        text = text[:MAX_DETAIL_CHARS] + "\n...(truncated)"
    return text


def parse(path: Path) -> list[Suite]:
    """Parse JUnit XML. Accepts <testsuites> or a bare <testsuite> root."""
    root = ET.parse(path).getroot()
    suite_nodes = (
        [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    )

    suites: list[Suite] = []
    for sn in suite_nodes:
        suite = Suite(name=sn.get("name") or "(unnamed)")
        for cn in sn.findall("testcase"):
            status, message = "passed", ""
            # A testcase carries at most one outcome child; absence = passed.
            for tag, label in (("failure", "failed"), ("error", "error"),
                               ("skipped", "skipped")):
                child = cn.find(tag)
                if child is not None:
                    status, message = label, _detail(child)
                    break
            suite.cases.append(Case(
                suite=cn.get("classname") or suite.name,
                name=cn.get("name") or "(unnamed)",
                time=_float(cn.get("time")),
                status=status,
                message=message,
            ))
        suites.append(suite)
    return suites


def totals(suites: list[Suite]) -> dict[str, int]:
    out = {k: 0 for k in ("passed", "failed", "error", "skipped")}
    for s in suites:
        for c in s.cases:
            out[c.status] += 1
    out["total"] = sum(len(s.cases) for s in suites)
    return out


def render_markdown(suites: list[Suite], t: dict[str, int]) -> str:
    bad = t["failed"] + t["error"]
    verdict = "all green" if bad == 0 else f"**{bad} not passing**"
    lines = [
        "## MATLAB test results",
        "",
        f"{t['total']} tests across {len(suites)} file(s) — {verdict}",
        "",
        f"| Passed | Failed | Errors | Skipped | Time |",
        f"|---:|---:|---:|---:|---:|",
        f"| {t['passed']} | {t['failed']} | {t['error']} | {t['skipped']} | "
        f"{sum(s.time for s in suites):.1f}s |",
        "",
    ]

    problems = [c for s in suites for c in s.cases
                if c.status in ("failed", "error")]
    if problems:
        lines += [f"### Not passing ({len(problems)})", ""]
        for c in problems:
            lines += [f"<details><summary><code>{c.full_name}</code> "
                      f"— {c.status}</summary>", "", "```",
                      c.message or "(no diagnostic recorded)", "```", "",
                      "</details>", ""]

    # Per-file table, worst first so the interesting rows are at the top.
    lines += ["### Per file", "",
              "| File | Tests | Passed | Failed | Errors | Skipped | Time |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for s in sorted(suites,
                    key=lambda s: (-(s.count("failed") + s.count("error")),
                                   s.name)):
        lines.append(
            f"| `{s.name}` | {len(s.cases)} | {s.count('passed')} | "
            f"{s.count('failed')} | {s.count('error')} | "
            f"{s.count('skipped')} | {s.time:.1f}s |"
        )
    return "\n".join(lines) + "\n"


def render_text(suites: list[Suite], t: dict[str, int]) -> str:
    lines = [
        "=== MATLAB test results ===",
        f"{t['total']} tests across {len(suites)} file(s)",
        f"  passed {t['passed']}   failed {t['failed']}   "
        f"errors {t['error']}   skipped {t['skipped']}",
        "",
    ]
    problems = [c for s in suites for c in s.cases
                if c.status in ("failed", "error")]
    if problems:
        lines.append(f"Not passing ({len(problems)}):")
        for c in problems:
            lines.append(f"  {c.status.upper():<7} {c.full_name}")
            first = (c.message or "").strip().splitlines()
            if first:
                lines.append(f"          {first[0][:160]}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("xml", nargs="?", type=Path, default=DEFAULT_XML)
    ap.add_argument("--markdown", action="store_true",
                    help="Print markdown instead of plain text")
    ap.add_argument("--fail-on-failure", action="store_true",
                    help="Exit 1 when any test failed or errored")
    args = ap.parse_args(argv)

    if not args.xml.is_file():
        print(f"No JUnit XML at {args.xml}. Run the MATLAB tests first.")
        return 2

    try:
        suites = parse(args.xml)
    except ET.ParseError as exc:
        print(f"Malformed JUnit XML at {args.xml}: {exc}")
        return 2

    t = totals(suites)
    print(render_markdown(suites, t) if args.markdown
          else render_text(suites, t))

    # In CI, put the same table on the run page so nothing needs downloading.
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(render_markdown(suites, t))

    if args.fail_on_failure and (t["failed"] or t["error"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
