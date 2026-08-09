#!/usr/bin/env python3
"""Compare benchmark metrics against recorded baselines, and gate on the diff.

Second half of the quality system. ``runBench.m`` measures; this decides
whether the measurement is acceptable. Splitting it this way means the gate
logic is testable, tunable, and re-runnable without MATLAB -- you can replay
an old metrics.json against new tolerances without recomputing anything.

Decision rule, per metric::

    delta   = current - baseline
    allowed = max(abs_tol, rel_tol * |baseline|)

    direction='any'              fail when |delta| > allowed
    direction='lower_is_better'  fail when  delta > allowed
    direction='higher_is_better' fail when -delta > allowed

A move beyond tolerance in the *good* direction is reported as an improvement,
never a failure -- but it is surfaced loudly, because an unexplained tenfold
improvement usually means the case stopped measuring what you thought.

Tolerances come from the case file (the current run), not from the baseline,
so you can retune a gate without re-recording numbers.

Usage
-----
    python3 ci/bench/compare.py                  # compare, exit 1 on regression
    python3 ci/bench/compare.py --record         # adopt current as baseline
    python3 ci/bench/compare.py --record --only bench_photcal
    python3 ci/bench/compare.py --report out.md
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_METRICS = REPO_ROOT / "ci" / "bench" / "results" / "metrics.json"
DEFAULT_BASELINES = REPO_ROOT / "ci" / "bench" / "baselines"
DEFAULT_REPORT = REPO_ROOT / "ci" / "bench" / "results" / "report.md"

VALID_DIRECTIONS = {"any", "lower_is_better", "higher_is_better"}

OK = "ok"
REGRESSION = "regression"
IMPROVEMENT = "improvement"
NEW = "new"
MISSING = "missing"


def _rel(path: Path) -> str:
    """Repo-relative display path, falling back to absolute when outside."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def allowed_delta(baseline: float, tol: dict) -> float:
    abs_tol = float(tol.get("abs", 0.0) or 0.0)
    rel_tol = float(tol.get("rel", 0.0) or 0.0)
    return max(abs_tol, rel_tol * abs(baseline))


def classify(current: float, baseline: float, tol: dict) -> tuple[str, float, float]:
    """Return (verdict, delta, allowed) for one metric."""
    direction = tol.get("direction", "any")
    if direction not in VALID_DIRECTIONS:
        raise ValueError(
            f"Unknown direction {direction!r}; expected one of "
            f"{sorted(VALID_DIRECTIONS)}"
        )
    delta = current - baseline
    allowed = allowed_delta(baseline, tol)

    if direction == "any":
        if abs(delta) > allowed:
            return REGRESSION, delta, allowed
        return OK, delta, allowed

    # Directional: a move the good way is never a failure.
    worse = delta if direction == "lower_is_better" else -delta
    if worse > allowed:
        return REGRESSION, delta, allowed
    if -worse > allowed:
        return IMPROVEMENT, delta, allowed
    return OK, delta, allowed


def compare_case(name: str, entry: dict, baseline: dict) -> dict:
    """Compare one case; returns a result dict with per-metric verdicts."""
    out = {"case": name, "status": OK, "metrics": [], "notes": []}

    if entry.get("status") != "ok":
        out["status"] = "error"
        out["notes"].append(entry.get("message") or "Case failed to run.")
        return out

    current = entry.get("metrics") or {}
    tolerances = entry.get("tolerances") or {}
    base_metrics = (baseline or {}).get("metrics") or {}

    if baseline is None:
        out["status"] = "unbaselined"
        out["notes"].append(
            "No baseline recorded. Run with --record to adopt these numbers."
        )
        for key in sorted(current):
            out["metrics"].append({
                "metric": key, "current": current[key], "baseline": None,
                "delta": None, "allowed": None, "verdict": NEW,
                "direction": (tolerances.get(key) or {}).get("direction", "any"),
            })
        return out

    for key in sorted(set(current) | set(base_metrics)):
        tol = tolerances.get(key) or {}
        direction = tol.get("direction", "any")

        if key not in base_metrics:
            out["metrics"].append({
                "metric": key, "current": current[key], "baseline": None,
                "delta": None, "allowed": None, "verdict": NEW,
                "direction": direction,
            })
            out["notes"].append(f"New metric '{key}' is not in the baseline.")
            continue

        if key not in current:
            out["metrics"].append({
                "metric": key, "current": None, "baseline": base_metrics[key],
                "delta": None, "allowed": None, "verdict": MISSING,
                "direction": direction,
            })
            out["notes"].append(
                f"Metric '{key}' disappeared; the case stopped measuring it."
            )
            out["status"] = "fail"
            continue

        cur, base = float(current[key]), float(base_metrics[key])
        if not (math.isfinite(cur) and math.isfinite(base)):
            verdict, delta, allowed = REGRESSION, float("nan"), 0.0
        else:
            verdict, delta, allowed = classify(cur, base, tol)

        out["metrics"].append({
            "metric": key, "current": cur, "baseline": base,
            "delta": delta, "allowed": allowed, "verdict": verdict,
            "direction": direction,
        })
        if verdict == REGRESSION:
            out["status"] = "fail"

    return out


def load_baselines(dirpath: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not dirpath.is_dir():
        return out
    for f in sorted(dirpath.glob("*.json")):
        try:
            out[f.stem] = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Corrupt baseline {f}: {exc}")
    return out


def record(report: dict, dirpath: Path, only: str | None) -> int:
    dirpath.mkdir(parents=True, exist_ok=True)
    written = 0
    for name, entry in (report.get("cases") or {}).items():
        if only and only not in name:
            continue
        if entry.get("status") != "ok":
            print(f"  skip {name}: case did not run cleanly")
            continue
        payload = {
            "case": name,
            "recorded": dt.datetime.now(dt.timezone.utc)
                          .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sourceRun": {
                "generated": report.get("generated"),
                "gitSha": report.get("gitSha"),
                "matlabVersion": report.get("matlabVersion"),
                "seed": report.get("seed"),
            },
            "meta": entry.get("meta") or {},
            "metrics": entry.get("metrics") or {},
            "tolerances": entry.get("tolerances") or {},
        }
        (dirpath / f"{name}.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        print(f"  recorded {name} ({len(payload['metrics'])} metrics)")
        written += 1
    return written


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        if v != 0 and (abs(v) < 1e-4 or abs(v) >= 1e6):
            return f"{v:.3e}"
        return f"{v:.6g}"
    return str(v)


def render_report(results: list[dict], report: dict) -> str:
    n_fail = sum(1 for r in results if r["status"] in ("fail", "error"))
    n_unbased = sum(1 for r in results if r["status"] == "unbaselined")
    n_improve = sum(
        1 for r in results for m in r["metrics"] if m["verdict"] == IMPROVEMENT
    )

    lines = [
        "# Quality benchmark report",
        "",
        f"- Run: `{report.get('generated', '?')}` "
        f"@ `{(report.get('gitSha') or '?')[:12]}` "
        f"(MATLAB {report.get('matlabVersion', '?')})",
        f"- Cases: **{len(results)}** | "
        f"failing: **{n_fail}** | "
        f"unbaselined: **{n_unbased}** | "
        f"improvements: **{n_improve}**",
        "",
    ]

    for r in results:
        badge = {
            OK: "PASS", "fail": "FAIL", "error": "ERROR",
            "unbaselined": "NO BASELINE",
        }.get(r["status"], r["status"].upper())
        lines += [f"## {r['case']} - {badge}", ""]

        if r["metrics"]:
            lines += [
                "| Metric | Current | Baseline | Delta | Allowed | Direction | Verdict |",
                "|---|---:|---:|---:|---:|---|---|",
            ]
            for m in r["metrics"]:
                lines.append(
                    f"| `{m['metric']}` | {_fmt(m['current'])} | "
                    f"{_fmt(m['baseline'])} | {_fmt(m['delta'])} | "
                    f"{_fmt(m['allowed'])} | {m['direction']} | "
                    f"{m['verdict']} |"
                )
            lines.append("")
        for note in r["notes"]:
            lines.append(f"> {note}")
        if r["notes"]:
            lines.append("")

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--metrics", type=Path, default=DEFAULT_METRICS)
    ap.add_argument("--baselines", type=Path, default=DEFAULT_BASELINES)
    ap.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    ap.add_argument("--only", help="Restrict to cases whose name contains this")
    ap.add_argument("--record", action="store_true",
                    help="Adopt the current metrics as the new baseline")
    ap.add_argument("--fail-on-unbaselined", action="store_true",
                    help="Treat a case with no baseline as a failure")
    args = ap.parse_args(argv)

    if not args.metrics.is_file():
        print(f"No metrics file at {args.metrics}.\n"
              f"Generate it first:  matlab -batch \"addpath('ci/matlab'); "
              f"addpath('ci/bench'); runBench();\"")
        return 2

    report = json.loads(args.metrics.read_text(encoding="utf-8"))

    if args.record:
        print(f"Recording baselines into {_rel(args.baselines)}/")
        n = record(report, args.baselines, args.only)
        print(f"\n{n} baseline(s) written.")
        return 0

    baselines = load_baselines(args.baselines)
    results = []
    for name, entry in sorted((report.get("cases") or {}).items()):
        if args.only and args.only not in name:
            continue
        results.append(compare_case(name, entry, baselines.get(name)))

    if not results:
        print("No benchmark cases in the metrics file.")
        return 0

    text = render_report(results, report)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(text, encoding="utf-8")

    print(f"{'CASE':<34} {'STATUS':<12} METRICS")
    for r in results:
        bad = sum(1 for m in r["metrics"] if m["verdict"] == REGRESSION)
        imp = sum(1 for m in r["metrics"] if m["verdict"] == IMPROVEMENT)
        detail = f"{len(r['metrics'])} checked"
        if bad:
            detail += f", {bad} regressed"
        if imp:
            detail += f", {imp} improved"
        print(f"{r['case']:<34} {r['status']:<12} {detail}")

    for r in results:
        for m in r["metrics"]:
            if m["verdict"] == REGRESSION:
                print(f"\nREGRESSION  {r['case']}.{m['metric']}")
                print(f"  baseline {_fmt(m['baseline'])} -> "
                      f"current {_fmt(m['current'])} "
                      f"(delta {_fmt(m['delta'])}, "
                      f"allowed {_fmt(m['allowed'])}, {m['direction']})")
            elif m["verdict"] == IMPROVEMENT:
                print(f"\nIMPROVEMENT {r['case']}.{m['metric']}: "
                      f"{_fmt(m['baseline'])} -> {_fmt(m['current'])}  "
                      f"(confirm this is real, then --record)")

    print(f"\nReport: {_rel(args.report)}")

    failed = [r for r in results if r["status"] in ("fail", "error")]
    if args.fail_on_unbaselined:
        failed += [r for r in results if r["status"] == "unbaselined"]
    if failed:
        print(f"\nFAILED: {len(failed)} case(s).")
        return 1
    print("\nOK: no regressions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
