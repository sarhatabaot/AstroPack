#!/usr/bin/env python3
"""Tests for the benchmark comparator. Run: python3 ci/bench/test_compare.py

The comparator decides whether a code change is allowed to land, so its own
logic needs a gate. These run in milliseconds with no MATLAB.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare import (  # noqa: E402
    IMPROVEMENT, MISSING, NEW, OK, REGRESSION,
    classify, compare_case, main, record,
)

ANY = {"abs": 0.1, "rel": 0.0, "direction": "any"}
LOWER = {"abs": 0.1, "rel": 0.0, "direction": "lower_is_better"}
HIGHER = {"abs": 0.1, "rel": 0.0, "direction": "higher_is_better"}


class TestClassify(unittest.TestCase):
    def test_within_absolute_tolerance_passes(self):
        self.assertEqual(classify(1.05, 1.0, ANY)[0], OK)

    def test_beyond_absolute_tolerance_fails_either_way(self):
        self.assertEqual(classify(1.2, 1.0, ANY)[0], REGRESSION)
        self.assertEqual(classify(0.8, 1.0, ANY)[0], REGRESSION)

    def test_relative_tolerance_scales_with_baseline(self):
        tol = {"abs": 0.0, "rel": 0.10, "direction": "any"}
        self.assertEqual(classify(105.0, 100.0, tol)[0], OK)     # 5%
        self.assertEqual(classify(120.0, 100.0, tol)[0], REGRESSION)  # 20%

    def test_looser_of_the_two_tolerances_wins(self):
        tol = {"abs": 5.0, "rel": 0.01, "direction": "any"}
        # 1% of 100 is 1.0, but abs=5.0 is looser and should govern.
        self.assertEqual(classify(104.0, 100.0, tol)[0], OK)

    def test_lower_is_better_treats_a_drop_as_improvement(self):
        self.assertEqual(classify(0.5, 1.0, LOWER)[0], IMPROVEMENT)
        self.assertEqual(classify(1.5, 1.0, LOWER)[0], REGRESSION)

    def test_higher_is_better_is_the_mirror_image(self):
        self.assertEqual(classify(1.5, 1.0, HIGHER)[0], IMPROVEMENT)
        self.assertEqual(classify(0.5, 1.0, HIGHER)[0], REGRESSION)

    def test_zero_tolerance_demands_exactness(self):
        tol = {"abs": 0.0, "rel": 0.0, "direction": "any"}
        self.assertEqual(classify(1.0, 1.0, tol)[0], OK)
        self.assertEqual(classify(1.0000001, 1.0, tol)[0], REGRESSION)

    def test_zero_baseline_with_relative_tolerance_only(self):
        # rel * |0| == 0, so only an exact match passes. Guards against a
        # divide-by-zero style bug where everything silently passes.
        tol = {"abs": 0.0, "rel": 0.5, "direction": "any"}
        self.assertEqual(classify(0.0, 0.0, tol)[0], OK)
        self.assertEqual(classify(0.001, 0.0, tol)[0], REGRESSION)

    def test_unknown_direction_is_rejected_loudly(self):
        with self.assertRaises(ValueError):
            classify(1.0, 1.0, {"abs": 1, "direction": "sideways"})


class TestCompareCase(unittest.TestCase):
    def _entry(self, metrics, tolerances=None):
        return {
            "status": "ok",
            "metrics": metrics,
            "tolerances": tolerances or {k: dict(ANY) for k in metrics},
        }

    def _baseline(self, metrics):
        return {"metrics": metrics}

    def test_clean_pass(self):
        r = compare_case("c", self._entry({"a": 1.0}), self._baseline({"a": 1.0}))
        self.assertEqual(r["status"], OK)

    def test_regression_marks_case_failed(self):
        r = compare_case("c", self._entry({"a": 9.0}), self._baseline({"a": 1.0}))
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["metrics"][0]["verdict"], REGRESSION)

    def test_improvement_does_not_fail_the_case(self):
        entry = self._entry({"a": 0.1}, {"a": dict(LOWER)})
        r = compare_case("c", entry, self._baseline({"a": 1.0}))
        self.assertEqual(r["status"], OK)
        self.assertEqual(r["metrics"][0]["verdict"], IMPROVEMENT)

    def test_new_metric_is_reported_but_does_not_fail(self):
        r = compare_case("c", self._entry({"a": 1.0, "b": 2.0}),
                         self._baseline({"a": 1.0}))
        self.assertEqual(r["status"], OK)
        verdicts = {m["metric"]: m["verdict"] for m in r["metrics"]}
        self.assertEqual(verdicts["b"], NEW)

    def test_disappearing_metric_fails(self):
        # A case that quietly stops measuring something must not look green.
        r = compare_case("c", self._entry({"a": 1.0}),
                         self._baseline({"a": 1.0, "b": 2.0}))
        self.assertEqual(r["status"], "fail")
        verdicts = {m["metric"]: m["verdict"] for m in r["metrics"]}
        self.assertEqual(verdicts["b"], MISSING)

    def test_errored_case_fails(self):
        r = compare_case("c", {"status": "error", "message": "boom"}, None)
        self.assertEqual(r["status"], "error")

    def test_missing_baseline_is_flagged_not_failed(self):
        r = compare_case("c", self._entry({"a": 1.0}), None)
        self.assertEqual(r["status"], "unbaselined")
        self.assertEqual(r["metrics"][0]["verdict"], NEW)

    def test_tolerances_come_from_the_case_not_the_baseline(self):
        # Baseline says exact; the case now allows +/-0.1. The case wins,
        # so retuning a gate does not require re-recording numbers.
        entry = self._entry({"a": 1.05}, {"a": dict(ANY)})
        baseline = {"metrics": {"a": 1.0},
                    "tolerances": {"a": {"abs": 0.0, "direction": "any"}}}
        self.assertEqual(compare_case("c", entry, baseline)["status"], OK)


class TestEndToEnd(unittest.TestCase):
    """Drive the real CLI over a temp tree: record, pass, then regress."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.metrics = root / "metrics.json"
        self.baselines = root / "baselines"
        self.report = root / "report.md"
        self.run = {
            "schemaVersion": 1,
            "generated": "2026-08-09T00:00:00Z",
            "gitSha": "abc123",
            "matlabVersion": "9.14",
            "seed": 0,
            "cases": {
                "bench_demo": {
                    "status": "ok",
                    "durationSeconds": 0.5,
                    "meta": {"description": "demo"},
                    "metrics": {"rms": 1.0, "count": 100.0},
                    "tolerances": {
                        "rms": {"abs": 0.0, "rel": 0.05,
                                "direction": "lower_is_better"},
                        "count": {"abs": 2, "rel": 0.0, "direction": "any"},
                    },
                }
            },
        }
        self._write(self.run)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, payload):
        self.metrics.write_text(json.dumps(payload), encoding="utf-8")

    def _cli(self, *extra):
        return main([
            "--metrics", str(self.metrics),
            "--baselines", str(self.baselines),
            "--report", str(self.report), *extra,
        ])

    def test_full_cycle(self):
        # No baseline yet -> flagged, but not a failure by default.
        self.assertEqual(self._cli(), 0)
        # ...and a failure when the caller demands baselines.
        self.assertEqual(self._cli("--fail-on-unbaselined"), 1)

        # Record, then an unchanged run is green.
        self.assertEqual(self._cli("--record"), 0)
        self.assertTrue((self.baselines / "bench_demo.json").is_file())
        self.assertEqual(self._cli(), 0)

        # rms grows 20% with a 5% gate -> regression.
        run = json.loads(json.dumps(self.run))
        run["cases"]["bench_demo"]["metrics"]["rms"] = 1.2
        self._write(run)
        self.assertEqual(self._cli(), 1)

        # rms halves -> improvement, still green.
        run["cases"]["bench_demo"]["metrics"]["rms"] = 0.5
        self._write(run)
        self.assertEqual(self._cli(), 0)

        # count drifts past its absolute gate -> regression.
        run["cases"]["bench_demo"]["metrics"] = {"rms": 1.0, "count": 105.0}
        self._write(run)
        self.assertEqual(self._cli(), 1)

        # A report is written every time and names the case.
        self.assertIn("bench_demo", self.report.read_text(encoding="utf-8"))

    def test_record_skips_errored_cases(self):
        run = json.loads(json.dumps(self.run))
        run["cases"]["bench_broken"] = {"status": "error", "message": "boom"}
        self._write(run)
        self.assertEqual(self._cli("--record"), 0)
        self.assertTrue((self.baselines / "bench_demo.json").is_file())
        self.assertFalse((self.baselines / "bench_broken.json").is_file())

    def test_baseline_carries_provenance(self):
        self._cli("--record")
        saved = json.loads(
            (self.baselines / "bench_demo.json").read_text(encoding="utf-8")
        )
        self.assertEqual(saved["sourceRun"]["gitSha"], "abc123")
        self.assertEqual(saved["sourceRun"]["matlabVersion"], "9.14")
        self.assertIn("recorded", saved)

    def test_missing_metrics_file_is_a_clean_error(self):
        self.metrics.unlink()
        self.assertEqual(self._cli(), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
