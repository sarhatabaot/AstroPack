#!/usr/bin/env python3
"""Render a gitleaks JSON report as a summary that is safe to publish.

This repository is public. A gitleaks report contains the secrets it found, so
uploading one as a workflow artifact, or echoing it into a job summary, would
hand every finding to anyone who opens the run page -- turning a scan meant to
protect the repo into a second disclosure.

So this renderer works from an allowlist, not a denylist. It emits only fields
that are structurally incapable of holding secret material:

    rule id, file path, line number, commit sha, date, author name

It never emits `Secret`, `Match`, `Email`, or the commit message, even though
the report carries them and even when gitleaks was run with --redact. --redact
is the first line of defence; this is the second.

Usage
-----
    python3 ci/gitleaks_summary.py /tmp/gitleaks.json
    python3 ci/gitleaks_summary.py /tmp/gitleaks.json --fail-on-finding
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# The only keys ever read out of a finding. Anything not listed here is
# unreachable by construction, so a future gitleaks field cannot leak by
# being added upstream.
SAFE_FIELDS = ("RuleID", "File", "StartLine", "Commit", "Date", "Author")


def safe_view(finding: dict) -> dict:
    """Project a finding down to publishable fields only."""
    out = {k: finding.get(k) for k in SAFE_FIELDS}
    out["RuleID"] = out.get("RuleID") or "(unknown-rule)"
    out["File"] = out.get("File") or "(unknown-file)"
    out["Commit"] = (out.get("Commit") or "")[:12]
    out["Date"] = (out.get("Date") or "")[:10]
    return out


def load(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("expected a JSON array of findings")
    return [safe_view(f) for f in data if isinstance(f, dict)]


def render_markdown(findings: list[dict]) -> str:
    if not findings:
        return ("## Secret scan\n\nNo secrets found.\n")

    by_rule: dict[str, int] = {}
    by_file: dict[str, int] = {}
    for f in findings:
        by_rule[f["RuleID"]] = by_rule.get(f["RuleID"], 0) + 1
        by_file[f["File"]] = by_file.get(f["File"], 0) + 1

    lines = [
        "## Secret scan",
        "",
        f"**{len(findings)} finding(s)** across {len(by_file)} file(s).",
        "",
        "> Values are deliberately omitted: this page is public. "
        "Reproduce locally with "
        "`gitleaks git . --config .gitleaks.toml --log-opts=\"--all\"` "
        "(drop `--redact` only on a trusted machine).",
        "",
        "### By rule",
        "",
        "| Rule | Count |",
        "|---|---:|",
    ]
    for rule, n in sorted(by_rule.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| `{rule}` | {n} |")

    lines += ["", "### Findings", "",
              "| Rule | File | Line | Commit | Date | Author |",
              "|---|---|---:|---|---|---|"]
    for f in sorted(findings, key=lambda f: (f["RuleID"], f["File"])):
        lines.append(
            f"| `{f['RuleID']}` | `{f['File']}` | {f['StartLine']} | "
            f"`{f['Commit']}` | {f['Date']} | {f['Author']} |"
        )

    lines += [
        "",
        "### If these are real",
        "",
        "1. **Rotate the credential.** A secret in a public history is "
        "compromised from the moment it was pushed; deleting it afterwards "
        "does not un-publish it.",
        "2. Check the provider's audit log for use of the old value.",
        "3. **This project does not scrub history** — rewriting refs cannot "
        "reach forks, clones or caches, so rotation is the remedy and the "
        "history entry becomes a record of a credential that no longer works.",
        "4. Retire the finding in the `.gitleaks.toml` rotation ledger, with "
        "the date and who did it, so the scan stays actionable.",
        "",
    ]
    return "\n".join(lines) + "\n"


def render_text(findings: list[dict]) -> str:
    if not findings:
        return "=== Secret scan ===\nNo secrets found.\n"
    lines = [f"=== Secret scan: {len(findings)} finding(s) ===",
             "(values omitted by design)", ""]
    for f in sorted(findings, key=lambda f: (f["RuleID"], f["File"])):
        lines.append(f"  {f['RuleID']:<28} {f['File']}:{f['StartLine']}"
                     f"  {f['Commit']}  {f['Date']}  {f['Author']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("report", type=Path)
    ap.add_argument("--fail-on-finding", action="store_true",
                    help="Exit 1 when anything was found")
    args = ap.parse_args(argv)

    if not args.report.is_file():
        # gitleaks omits the report entirely when it finds nothing.
        findings: list[dict] = []
    else:
        try:
            findings = load(args.report)
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"Could not parse gitleaks report {args.report}: {exc}")
            return 2

    print(render_text(findings), end="")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(render_markdown(findings))

    if findings and args.fail_on_finding:
        print(f"\nFAILED: {len(findings)} secret(s) introduced by this change.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
