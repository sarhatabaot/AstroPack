#!/usr/bin/env python3
"""Tests for the gitleaks summary renderer.
Run: python3 ci/test_gitleaks_summary.py

The renderer's whole job is to publish findings to a PUBLIC page without
publishing the secrets. Most of these tests exist to prove it cannot leak.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gitleaks_summary import (  # noqa: E402
    main, load, render_markdown, render_text, safe_view,
)

CANARY = "AKIAIOSFODNN7EXAMPLE_CANARY_VALUE"

FINDING = {
    "Description": "AWS Access Key",
    "StartLine": 12, "EndLine": 12, "StartColumn": 1, "EndColumn": 40,
    "Match": f"aws_key = {CANARY}",
    "Secret": CANARY,
    "File": "aws/admin/KeyPair1.pem",
    "Commit": "30dbc9363aaaabbbbccccddddeeeeffff00001111",
    "Entropy": 4.2,
    "Author": "Chen Tishler",
    "Email": "private.person@example.com",
    "Date": "2021-05-09T10:11:12Z",
    "Message": f"oops committed {CANARY}",
    "Tags": [],
    "RuleID": "aws-access-token",
    "Fingerprint": f"aws/admin/KeyPair1.pem:aws-access-token:12:{CANARY}",
}


class TestRedaction(unittest.TestCase):
    """Nothing sensitive may survive into any rendered output."""

    def _assert_clean(self, text):
        for forbidden, label in [
            (CANARY, "the secret value"),
            ("private.person@example.com", "the author email"),
            ("oops committed", "the commit message"),
            ("Fingerprint", "the fingerprint field"),
        ]:
            self.assertNotIn(forbidden, text, f"{label} leaked into output")

    def test_safe_view_drops_every_sensitive_field(self):
        v = safe_view(FINDING)
        for key in ("Secret", "Match", "Email", "Message", "Fingerprint",
                    "Description", "Entropy"):
            self.assertNotIn(key, v, f"{key} survived projection")

    def test_markdown_is_clean(self):
        self._assert_clean(render_markdown([safe_view(FINDING)]))

    def test_text_is_clean(self):
        self._assert_clean(render_text([safe_view(FINDING)]))

    def test_unknown_future_field_cannot_leak(self):
        # A field gitleaks adds in some later version must not appear just
        # because it exists -- the projection is an allowlist, not a denylist.
        evolved = dict(FINDING, SomeNewFieldWithTheSecret=CANARY)
        self._assert_clean(render_markdown([safe_view(evolved)]))

    def test_commit_is_shortened_and_date_trimmed(self):
        v = safe_view(FINDING)
        self.assertEqual(v["Commit"], "30dbc9363aaa")
        self.assertEqual(v["Date"], "2021-05-09")


class TestRendering(unittest.TestCase):
    def test_publishable_facts_are_kept(self):
        md = render_markdown([safe_view(FINDING)])
        for wanted in ("aws-access-token", "aws/admin/KeyPair1.pem", "12",
                       "30dbc9363aaa", "Chen Tishler"):
            self.assertIn(wanted, md)

    def test_empty_report_says_so(self):
        self.assertIn("No secrets found", render_markdown([]))
        self.assertIn("No secrets found", render_text([]))

    def test_counts_group_by_rule(self):
        two = [safe_view(FINDING), safe_view(dict(FINDING, RuleID="private-key"))]
        md = render_markdown(two)
        self.assertIn("**2 finding(s)**", md)
        self.assertIn("private-key", md)

    def test_missing_fields_do_not_crash(self):
        md = render_markdown([safe_view({})])
        self.assertIn("(unknown-rule)", md)
        self.assertIn("(unknown-file)", md)

    def test_rotation_guidance_is_present(self):
        # Rotation is this project's remedy; scrubbing history is explicitly
        # not, so both halves of that stance must reach the reader.
        md = render_markdown([safe_view(FINDING)])
        self.assertIn("Rotate the credential", md)
        self.assertIn("does not scrub history", md)


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.report = self.dir / "gitleaks.json"

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("GITHUB_STEP_SUMMARY", None)

    def write(self, payload):
        self.report.write_text(json.dumps(payload), encoding="utf-8")

    def test_findings_fail_when_asked(self):
        self.write([FINDING])
        self.assertEqual(main([str(self.report), "--fail-on-finding"]), 1)

    def test_findings_alone_do_not_fail(self):
        self.write([FINDING])
        self.assertEqual(main([str(self.report)]), 0)

    def test_clean_report_passes(self):
        self.write([])
        self.assertEqual(main([str(self.report), "--fail-on-finding"]), 0)

    def test_absent_report_is_treated_as_clean(self):
        # gitleaks writes no file when it finds nothing.
        self.assertEqual(main([str(self.dir / "nope.json"), "--fail-on-finding"]), 0)

    def test_malformed_report_is_a_clean_error(self):
        self.report.write_text("{not json", encoding="utf-8")
        self.assertEqual(main([str(self.report)]), 2)

    def test_non_array_report_is_a_clean_error(self):
        self.write({"unexpected": "shape"})
        self.assertEqual(main([str(self.report)]), 2)

    def test_step_summary_is_appended_and_clean(self):
        self.write([FINDING])
        summary = self.dir / "summary.md"
        summary.write_text("existing\n", encoding="utf-8")
        os.environ["GITHUB_STEP_SUMMARY"] = str(summary)
        main([str(self.report)])
        text = summary.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("existing\n"), "must append")
        self.assertIn("Secret scan", text)
        self.assertNotIn(CANARY, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
