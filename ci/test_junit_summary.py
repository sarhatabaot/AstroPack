#!/usr/bin/env python3
"""Tests for the JUnit renderer. Run: python3 ci/test_junit_summary.py"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from junit_summary import main, parse, render_markdown, totals  # noqa: E402

# Shape MATLAB's XMLPlugin.producingJUnitFormat emits.
NESTED = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites name="MATLAB Test Results" tests="4" failures="1" errors="1">
  <testsuite name="celestial.coo.test_sphere_dist" tests="2">
    <testcase classname="celestial.coo.test_sphere_dist" name="testBasic" time="0.10"/>
    <testcase classname="celestial.coo.test_sphere_dist" name="testEdge" time="0.20">
      <failure message="Expected 1 but got 2" type="verifyEqual">stack trace here</failure>
    </testcase>
  </testsuite>
  <testsuite name="celestial.time.test_barycentricJD" tests="2">
    <testcase classname="celestial.time.test_barycentricJD" name="testSym" time="0.30">
      <error message="Undefined function 'sym'" type="MATLAB:UndefinedFunction">at chebyshevFun</error>
    </testcase>
    <testcase classname="celestial.time.test_barycentricJD" name="testMex" time="0.05">
      <skipped message="MEX not compiled"/>
    </testcase>
  </testsuite>
</testsuites>
"""

# Some writers emit a bare <testsuite> root instead.
FLAT = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="solo" tests="1">
  <testcase classname="solo" name="testOnly" time="1.5"/>
</testsuite>
"""


class TestParsing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text, name="junit.xml"):
        p = self.dir / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_nested_root_yields_every_suite(self):
        suites = parse(self.write(NESTED))
        self.assertEqual(len(suites), 2)
        self.assertEqual(sum(len(s.cases) for s in suites), 4)

    def test_bare_testsuite_root_is_accepted(self):
        suites = parse(self.write(FLAT))
        self.assertEqual(len(suites), 1)
        self.assertEqual(suites[0].cases[0].status, "passed")

    def test_each_outcome_is_classified(self):
        suites = parse(self.write(NESTED))
        got = {c.name: c.status for s in suites for c in s.cases}
        self.assertEqual(got, {
            "testBasic": "passed", "testEdge": "failed",
            "testSym": "error", "testMex": "skipped",
        })

    def test_a_testcase_with_no_children_counts_as_passed(self):
        suites = parse(self.write(FLAT))
        self.assertEqual(totals(suites)["passed"], 1)

    def test_message_and_body_are_both_captured(self):
        suites = parse(self.write(NESTED))
        edge = [c for s in suites for c in s.cases if c.name == "testEdge"][0]
        self.assertIn("Expected 1 but got 2", edge.message)
        self.assertIn("stack trace here", edge.message)

    def test_totals_add_up(self):
        t = totals(parse(self.write(NESTED)))
        self.assertEqual(t, {"passed": 1, "failed": 1, "error": 1,
                             "skipped": 1, "total": 4})

    def test_durations_are_summed_per_suite(self):
        suites = parse(self.write(NESTED))
        by_name = {s.name: s for s in suites}
        self.assertAlmostEqual(
            by_name["celestial.coo.test_sphere_dist"].time, 0.30, places=6)

    def test_missing_time_attribute_does_not_crash(self):
        p = self.write('<testsuite name="s"><testcase name="t"/></testsuite>')
        self.assertEqual(parse(p)[0].cases[0].time, 0.0)


class TestRendering(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "junit.xml"
        self.path.write_text(NESTED, encoding="utf-8")
        self.suites = parse(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_markdown_names_every_problem_case(self):
        md = render_markdown(self.suites, totals(self.suites))
        self.assertIn("testEdge", md)
        self.assertIn("testSym", md)
        self.assertIn("Not passing (2)", md)

    def test_markdown_reports_green_when_nothing_failed(self):
        p = Path(self.tmp.name) / "clean.xml"
        p.write_text(FLAT, encoding="utf-8")
        suites = parse(p)
        self.assertIn("all green", render_markdown(suites, totals(suites)))

    def test_files_sort_worst_first(self):
        # Three suites with 2, 1 and 0 problems: the per-file table must put
        # the worst at the top so the interesting rows need no scrolling.
        xml = """<testsuites>
          <testsuite name="zzz_two_bad">
            <testcase name="a"><failure message="x"/></testcase>
            <testcase name="b"><error message="y"/></testcase>
          </testsuite>
          <testsuite name="aaa_clean"><testcase name="c"/></testsuite>
          <testsuite name="mmm_one_bad">
            <testcase name="d"><failure message="z"/></testcase>
          </testsuite>
        </testsuites>"""
        p = Path(self.tmp.name) / "sorted.xml"
        p.write_text(xml, encoding="utf-8")
        suites = parse(p)
        table = render_markdown(suites, totals(suites)).split("### Per file")[1]
        self.assertLess(table.index("zzz_two_bad"), table.index("mmm_one_bad"))
        self.assertLess(table.index("mmm_one_bad"), table.index("aaa_clean"))


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.xml = self.dir / "junit.xml"
        self.xml.write_text(NESTED, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("GITHUB_STEP_SUMMARY", None)

    def test_reporting_alone_does_not_fail(self):
        self.assertEqual(main([str(self.xml)]), 0)

    def test_fail_on_failure_flags_problems(self):
        self.assertEqual(main([str(self.xml), "--fail-on-failure"]), 1)

    def test_fail_on_failure_stays_green_when_clean(self):
        clean = self.dir / "clean.xml"
        clean.write_text(FLAT, encoding="utf-8")
        self.assertEqual(main([str(clean), "--fail-on-failure"]), 0)

    def test_missing_file_is_a_clean_error(self):
        self.assertEqual(main([str(self.dir / "nope.xml")]), 2)

    def test_malformed_xml_is_a_clean_error(self):
        bad = self.dir / "bad.xml"
        bad.write_text("<testsuite><unclosed>", encoding="utf-8")
        self.assertEqual(main([str(bad)]), 2)

    def test_github_step_summary_is_appended_to(self):
        summary = self.dir / "summary.md"
        summary.write_text("existing\n", encoding="utf-8")
        os.environ["GITHUB_STEP_SUMMARY"] = str(summary)
        main([str(self.xml)])
        text = summary.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("existing\n"), "must append")
        self.assertIn("MATLAB test results", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
