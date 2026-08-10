#!/usr/bin/env bash
# Run the CI gates locally, in the order CI runs them.
#
#   ci/run-local.sh              lint + tooling self-tests (no MATLAB, ~1 s)
#   ci/run-local.sh tests        + the MATLAB green-tier unit tests
#   ci/run-local.sh bench        + the quality benchmark vs recorded baselines
#   ci/run-local.sh all          everything
#
# MATLAB stages are skipped with a clear message when MATLAB is not on PATH,
# so this stays useful on a machine without a licence.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

MODE="${1:-lint}"
FAILURES=0

hr() { printf '\n=== %s ===\n' "$1"; }

step() {
    local name="$1"; shift
    hr "$name"
    if "$@"; then
        printf '[PASS] %s\n' "$name"
    else
        printf '[FAIL] %s\n' "$name"
        FAILURES=$((FAILURES + 1))
    fi
}

have_matlab() { command -v matlab >/dev/null 2>&1; }

# --- always: the cheap gates ------------------------------------------------
step "Test conventions lint"   python3 ci/lint/check_conventions.py
step "Lint self-tests"         python3 ci/lint/test_check_conventions.py
step "Comparator self-tests"   python3 ci/bench/test_compare.py
step "JUnit renderer tests"    python3 ci/test_junit_summary.py
step "Secret-summary tests"    python3 ci/test_gitleaks_summary.py

# --- MATLAB unit tests ------------------------------------------------------
if [[ "$MODE" == "tests" || "$MODE" == "all" ]]; then
    if have_matlab; then
        step "MATLAB green-tier tests" \
            matlab -batch "addpath('ci/matlab'); runCITests();"
    else
        hr "MATLAB green-tier tests"
        echo "[SKIP] matlab not on PATH."
    fi
    # Readable summary of whatever junit.xml exists, pass or fail.
    if [[ -f ci/results/junit.xml ]]; then
        python3 ci/junit_summary.py
    fi
fi

# --- quality benchmark ------------------------------------------------------
if [[ "$MODE" == "bench" || "$MODE" == "all" ]]; then
    if have_matlab; then
        step "Quality benchmark (measure)" \
            matlab -batch "addpath('ci/matlab'); addpath('ci/bench'); runBench();"
        step "Quality benchmark (compare)" \
            python3 ci/bench/compare.py
    else
        hr "Quality benchmark"
        echo "[SKIP] matlab not on PATH."
        if [[ -f ci/bench/results/metrics.json ]]; then
            echo "Re-comparing the last metrics.json without re-measuring:"
            step "Quality benchmark (compare only)" python3 ci/bench/compare.py
        fi
    fi
fi

hr "Summary"
if (( FAILURES > 0 )); then
    echo "$FAILURES stage(s) failed."
    exit 1
fi
echo "All stages passed."
