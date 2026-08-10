#!/usr/bin/env python3
"""Tests for the MATLAB conventions lint.
Run: python3 ci/lint/test_check_conventions.py

The lexer is the risky part: in MATLAB a single quote is both a string
delimiter and the transpose operator, so a naive scanner either misses real
violations or reports paths that live inside comments.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from matlab_lex import lex  # noqa: E402
from check_conventions import check_file, is_absolute_path_literal  # noqa: E402


class TestLexer(unittest.TestCase):
    def values(self, src):
        return [s.value for s in lex(src).strings]

    def test_plain_string(self):
        self.assertEqual(self.values("x = 'hello';"), ["hello"])

    def test_transpose_after_identifier_is_not_a_string(self):
        self.assertEqual(self.values("y = A' * B;"), [])

    def test_transpose_after_bracket_and_paren(self):
        self.assertEqual(self.values("y = [1 2 3]' + f(x)';"), [])

    def test_transpose_then_a_real_string(self):
        self.assertEqual(self.values("y = A' + 'tag';"), ["tag"])

    def test_doubled_quote_is_an_escape(self):
        self.assertEqual(self.values("s = 'it''s';"), ["it's"])

    def test_double_quoted_string(self):
        self.assertEqual(self.values('s = "hello";'), ["hello"])

    def test_comment_content_is_ignored(self):
        self.assertEqual(self.values("x = 1;  % see '~/matlab/data'"), [])

    def test_percent_inside_a_string_is_not_a_comment(self):
        # '%%' is two literal characters to the lexer; only fprintf collapses
        # it later. What matters is that the string does not end at the '%'.
        self.assertEqual(self.values("fprintf('100%% done');"), ["100%% done"])
        self.assertEqual(self.values("s = '50% off';  % trailing"), ["50% off"])

    def test_block_comment_is_ignored(self):
        src = "\n".join(["%{", "x = '~/secret';", "%}", "y = 'kept';"])
        self.assertEqual(self.values(src), ["kept"])

    def test_text_after_line_continuation_is_a_comment(self):
        self.assertEqual(self.values("x = 1 + ...  '~/nope'\n2;"), [])

    def test_line_numbers_are_preserved(self):
        src = "a = 1;\nb = 'x';\n"
        self.assertEqual(lex(src).strings[0].line, 2)


class TestAbsolutePathDetection(unittest.TestCase):
    def test_flags_home_and_drive_and_unc(self):
        for p in ["~/matlab/AstroPack", r"C:\Temp\x", r"D:\Ultrasat\y",
                  "/home/eran/matlab", "/mnt/euclid/catsHTM", r"\\server\share"]:
            self.assertTrue(is_absolute_path_literal(p), p)

    def test_flags_a_bare_tilde_segment(self):
        # fullfile('~','matlab',...) is the same bug as fullfile('~/',...)
        # but has no separator, and an earlier version of the rule missed it.
        self.assertTrue(is_absolute_path_literal("~"))
        self.assertTrue(is_absolute_path_literal(" ~ "))

    def test_ignores_relative_paths_urls_and_format_strings(self):
        for p in ["tests/relativeData", "../data", "http://example.com/a/b",
                  "%d items\n", "/", "", "a", "*.fits", "/n"]:
            self.assertFalse(is_absolute_path_literal(p), p)


class TestFileChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, body):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        return p

    def rules(self, path):
        return {v.rule for v in check_file(path, self.root)}

    def test_well_formed_test_file_is_clean(self):
        p = self.write("test_good.m", (
            "function tests = test_good\n"
            "    tests = functiontests(localfunctions);\n"
            "end\n"
            "function testOne(testCase)\n"
            "    testCase.verifyTrue(true);\n"
            "end\n"
        ))
        self.assertEqual(self.rules(p), set())

    def test_name_mismatch_is_caught(self):
        # The exact shape of the 202 legacy files in this repo.
        p = self.write("test_thing_01.m", (
            "function Result = unitTest()\n"
            "    Result = true;\n"
            "end\n"
        ))
        self.assertIn("name-mismatch", self.rules(p))

    def test_missing_functiontests_is_caught(self):
        p = self.write("test_thing.m", (
            "function tests = test_thing\n"
            "    tests = [];\n"
            "end\n"
        ))
        self.assertIn("no-suite", self.rules(p))

    def test_suite_without_test_functions_is_caught(self):
        p = self.write("test_empty.m", (
            "function tests = test_empty\n"
            "    tests = functiontests(localfunctions);\n"
            "end\n"
            "function helper(x)\n"
            "end\n"
        ))
        self.assertIn("no-test-functions", self.rules(p))

    def test_hardcoded_path_is_caught_in_code_only(self):
        p = self.write("test_paths.m", (
            "function tests = test_paths\n"
            "    tests = functiontests(localfunctions);\n"
            "end\n"
            "function testOne(testCase)\n"
            "    % Reference: '~/matlab/AstroPack/docs'\n"
            "    F = fullfile('~/', 'matlab', 'data');\n"
            "    testCase.verifyTrue(ischar(F));\n"
            "end\n"
        ))
        violations = [v for v in check_file(p, self.root) if v.rule == "abs-path"]
        self.assertEqual(len(violations), 1, "comment must not be flagged")
        self.assertEqual(violations[0].key, "~/")

    def test_classdef_name_must_match_filename(self):
        p = self.write("MyHelper.m", "classdef OtherName\nend\n")
        self.assertIn("name-mismatch", self.rules(p))

    def test_non_test_file_is_not_required_to_build_a_suite(self):
        p = self.write("helperThing.m", "function helperThing()\nend\n")
        self.assertEqual(self.rules(p), set())

    def test_violation_key_is_stable_against_line_moves(self):
        # Baselining relies on this: adding a line must not resurrect a
        # violation as "new".
        a = self.write("a/test_x.m", (
            "function tests = test_x\n"
            "    tests = functiontests(localfunctions);\n"
            "end\n"
            "function testOne(t)\n    p = 'C:\\Temp';\nend\n"
        ))
        b = self.write("b/test_x.m", (
            "function tests = test_x\n"
            "\n\n\n"
            "    tests = functiontests(localfunctions);\n"
            "end\n"
            "function testOne(t)\n    p = 'C:\\Temp';\nend\n"
        ))
        ka = [v.key for v in check_file(a, self.root) if v.rule == "abs-path"]
        kb = [v.key for v in check_file(b, self.root) if v.rule == "abs-path"]
        self.assertEqual(ka, kb)


class TestAgainstRealRepo(unittest.TestCase):
    """Guard the headline numbers this CI design is built on."""

    def test_known_legacy_file_is_still_undiscoverable(self):
        repo = Path(__file__).resolve().parents[2]
        target = repo / "tests" / "astro" / "+astro" / "+cosmo" / "test_cosmo_01.m"
        if not target.is_file():
            self.skipTest("repo layout changed")
        rules = {v.rule for v in check_file(target, repo)}
        self.assertIn("name-mismatch", rules)
        self.assertIn("no-suite", rules)


if __name__ == "__main__":
    unittest.main(verbosity=2)
