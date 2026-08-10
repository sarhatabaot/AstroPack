# AstroPack CI

Two independent systems, answering two different questions.

| | **Does it run?** | **Does it perform well?** |
|---|---|---|
| Question | Did anything break? | Did results get worse? |
| Verdict | pass / fail | metric vs recorded baseline |
| Where | GitHub Actions, every push | local, on demand |
| Entry point | `.github/workflows/ci.yml` | `ci/bench/` |
| Speed | seconds (lint) to minutes (MATLAB) | as slow as the science |

A unit test tells you `fitPhotCalibTrans` did not throw. It does not tell you
the zero-point scatter doubled. That is the gap the second system fills.

> **[applied-fixes.md](applied-fixes.md)** — changes made outside `ci/` as a
> result of CI findings. `suite.json` records what is *excluded* and why; that
> file records what was *repaired*.

---

## What you need to know first

A survey of `tests/` at the time this was built found:

- **286** files named `test*.m`
- **67** of them are real function-based suites that `runtests` can discover
- **202** declare a primary function called `unitTest`, which does not match
  their filename — MATLAB dispatches by filename, so these are legacy
  `matlab/**/unitTest.m` copies, not tests
- **14** are plain scripts, **3** are `classdef`
- **56** hardcoded absolute paths (`~/matlab/AstroPack/...`, `C:\Temp`,
  `D:\Ultrasat\...`) pin tests to one person's machine

So roughly **77% of what looks like a test suite never runs.** CI is built
around that fact rather than pretending otherwise: it enforces a *green tier*
of files known to pass, and quarantines the rest with a recorded reason.

Regenerate these numbers any time:

```bash
python3 ci/lint/check_conventions.py --no-baseline
python3 ci/triage.py --dry-run
```

---

## System 1 — functional CI

### Layout

```
.github/workflows/ci.yml       per-push gate: lint job + MATLAB job
.github/workflows/triage.yml   manual: discover which tests pass
ci/suite.json                  green tier + quarantine (committed)
ci/lint/check_conventions.py   static checks, no MATLAB needed
ci/lint/baseline.json          accepted pre-existing violations
ci/matlab/ciSetupPaths.m       deterministic path + RNG setup
ci/matlab/runCITests.m         runs the green tier, writes JUnit XML
ci/matlab/ciRunOneTestFile.m   triage worker (one file, one process)
ci/triage.py                   triage driver
ci/hooks/pre-push              local fast gate
ci/run-local.sh                run the gates locally
```

### The lint (no MATLAB, ~1 s)

Catches the failures that make a test *silently disappear* — worse than a red
test, because nobody notices.

| Rule | Why it matters |
|---|---|
| `name-mismatch` | Primary function ≠ filename → never discovered |
| `no-suite` | No `functiontests(localfunctions)` → contributes no tests |
| `no-test-functions` | Builds an empty suite that always "passes" |
| `abs-path` | Hardcoded absolute path → passes on one machine only |

It runs as a **ratchet**: the 456 existing violations are recorded in
`ci/lint/baseline.json` and do not fail the build; anything *new* does. Fix a
legacy one and drop its entry to lock the gain in.

```bash
python3 ci/lint/check_conventions.py                    # check
python3 ci/lint/check_conventions.py --no-baseline      # show everything
python3 ci/lint/check_conventions.py --update-baseline  # accept current state
```

### The MATLAB job

Runs the files listed under `green` in `ci/suite.json`, writes JUnit XML and a
JSON summary to `ci/results/`.

`ci/suite.json` is now **enforcing**, calibrated against a real run
(367 tests: 244 passed, 111 failed, 12 skipped):

| Tier | Files | Meaning |
|---|---:|---|
| green | 37 | Passed or skipped cleanly. A failure here blocks the merge. |
| quarantine | 30 | Excluded, each with the measured reason recorded. |

The 111 failures are **not** 111 problems. They collapse into a handful:

| Root cause | Tests | Files | Nature |
|---|---:|---:|---|
| Hardcoded `~/matlab/AstroPack/tests/relativeData/` | 45 | 8 | Fixture is committed here; only the path is wrong |
| Missing INPOP ephemeris data | 27 | 5 | Needs `Installer.install()` data on the runner |
| Missing Mapping Toolbox (`reckon`) | 3 | 2 | Now installed; re-triage to promote |
| Never-generated regression fixture | 1 | 1 | Generator has never been run |
| **Genuine code or test defects** | **35** | **13** | See the quarantine reasons |

To promote a file out of quarantine: fix the cause, re-run
`.github/workflows/triage.yml`, then move it to `green`.

Triage runs each file in **its own MATLAB process** with a timeout, so a hang
(a blocking network call, a prompt waiting on input) or a segfault isolates to
one file instead of killing the run.

```bash
python3 ci/triage.py --dry-run                        # list candidates
python3 ci/triage.py --jobs 4                         # full triage
python3 ci/triage.py --only tests/astro/+celestial    # one subtree
python3 ci/triage.py --promote                        # write ci/suite.json
```

Verdicts: `passed` and `skipped` → green; `failed`, `error`, `empty`,
`timeout`, `crash` → quarantine, each with the reason attached.

### Local hooks

```bash
ci/install-hooks.sh      # sets core.hooksPath, so hooks stay version-controlled
```

`pre-push` runs only the MATLAB-free lint, so pushing stays fast. Bypass with
`git push --no-verify`.

---

## System 2 — quality CI

Measurement and judgement are deliberately separated:

- **`ci/bench/runBench.m`** (MATLAB) runs each case and writes raw metrics.
- **`ci/bench/compare.py`** (Python) compares them to the baseline and decides.

That split means you can re-judge an old run against new tolerances without
recomputing anything, and the gate logic is unit-tested without MATLAB
(`ci/bench/test_compare.py`, 21 tests).

### Writing a case

Copy `ci/bench/cases/TEMPLATE_bench_case.m` to `bench_<name>.m`. A case runs a
piece of the pipeline over a **fixed** input and reduces the output to scalar
quality numbers:

```matlab
Result.metrics = struct('zpScatterMag', 0.021, 'nCalibrators', 1873);
Result.tolerances = struct( ...
    'zpScatterMag',  struct('abs', 0.002, 'rel', 0.05, 'direction', 'lower_is_better'), ...
    'nCalibrators',  struct('abs', 20,    'rel', 0.01, 'direction', 'any'));
Result.meta = struct('description', ..., 'dataset', ..., 'owner', ...);
```

`bench_harness_selfcheck.m` is a working case using only core MATLAB. If it
goes red, the problem is the plumbing, not the science — keep it.

### The decision rule

```
delta   = current - baseline
allowed = max(abs_tol, rel_tol * |baseline|)

direction='any'               fail when |delta| > allowed
direction='lower_is_better'   fail when  delta > allowed
direction='higher_is_better'  fail when -delta > allowed
```

A move beyond tolerance in the *good* direction is reported as an
**improvement**, never a failure — but reported loudly, because an unexplained
10× improvement usually means the case stopped measuring what you thought.

Other verdicts: a **new** metric is noted, not failed. A metric that
**disappears** fails — a case that quietly stops measuring something must not
look green.

Tolerances are read from the **case file**, not the baseline, so you can
retune a gate without re-recording numbers.

### Running it

```bash
matlab -batch "addpath('ci/matlab'); addpath('ci/bench'); runBench();"
python3 ci/bench/compare.py             # compare; exit 1 on regression
python3 ci/bench/compare.py --record    # adopt current numbers as baseline
ci/run-local.sh bench                   # both steps
```

Baselines live in `ci/bench/baselines/*.json` and **are committed** — a pull
request that moves a baseline is asserting "the new numbers are better, and
here is why", and that belongs in a reviewable diff.

### Choosing what to measure

Still open. Candidates, each needing a frozen input set and agreed metrics:

| Subject | Plausible metrics |
|---|---|
| LAST pipeline | astrometric residual RMS, photometric ZP scatter, source counts, false-positive rate |
| `fitPhotCalibTrans` | fitted transmission residual, ZP scatter, calibrator count |
| `usim` / ELOPsim | PSF FWHM, noise σ, ADU levels, header conformance |
| `uplanner` | targets scheduled, slew time, constraint violations |

The harness does not care which you pick — but pick inputs that are committed
or reachable from an environment variable, never a hardcoded path.

---

## MATLAB release

Defined in **one place**: `MATLAB_RELEASE` in `.github/workflows/ci.yml`
(triage takes a `release` input, same default).

Currently **R2021a**, because `matlab-actions/setup-matlab` supports R2021a and
later only, while the lab machines run **R2020b**. R2021a is the closest
supported release.

The residual gap is real: code using an R2021a-only feature will pass CI and
fail on the lab machines. Two ways to close it properly —

1. Upgrade the lab machines to a release CI can also run.
2. Add a self-hosted runner with R2020b and give it the MATLAB job. Exact
   parity, plus access to `/mnt/euclid`, at the cost of maintaining a machine.

Until then, MATLAB sources under `ci/` are kept R2020b-compatible on purpose
(for example they avoid `jsonencode(..., 'PrettyPrint', true)`, which postdates
R2020b), so `ci/run-local.sh` works on the lab machines.

Licensing needs no secret: MathWorks licenses MATLAB automatically for public
repositories. If this repo goes private, add a batch licensing token as the
`MLM_LICENSE_TOKEN` secret and reference it in the setup-matlab step.

---

## Determinism

`ciSetupPaths.m` wraps the repo's own `matlab/startup/startup.m` so CI uses the
same path layout developers do, with two deliberate differences:

- **Seeded RNG.** `startup.m` calls `rng('shuffle')`. CI calls
  `startup(..., 'setRandomNumbers', false)` and then `rng(0, 'twister')`.
  Otherwise any test using random data is unreproducible between runs.
- **`ASTROPACK_PATH` / `ASTROPACK_CONFIG_PATH`** point at the checkout, not at
  whatever the host happens to have installed.

`UpdateTime` is off, keeping CI off the network.

---

## The CI tooling has its own tests

It gates every merge, so it is tested like anything else:

```bash
python3 ci/lint/test_check_conventions.py   # 22 tests: lexer + rules
python3 ci/bench/test_compare.py            # 21 tests: tolerance + gate logic
```

Both run in the `lint` job on every push. They found two real bugs during
development: a crash on paths outside the repo, and a length guard that made
the linter miss `'~/'` — the single most common bad path in this repo.

---

## Viewing test results

`runCITests` writes `ci/results/junit.xml` (standards-compliant JUnit, from
MATLAB's `XMLPlugin`) and `ci/results/summary.json`. Raw JUnit XML is not
something anyone should have to read, so `ci/junit_summary.py` renders it.

**In CI** — the "Publish test results to the job summary" step puts a table
straight on the run page: totals, every non-passing test with its diagnostic
in a collapsible block, and a per-file breakdown sorted worst-first. Nothing
to download. It runs under `if: always()`, so results appear even when the
MATLAB step goes red — which is when you need them most.

**Locally**

```bash
python3 ci/junit_summary.py                    # ci/results/junit.xml
python3 ci/junit_summary.py path/to/junit.xml  # any JUnit file
python3 ci/junit_summary.py --markdown         # the CI rendering
ci/run-local.sh tests                          # runs tests, then renders
```

The raw `junit.xml` and `summary.json` are still uploaded as the
`matlab-test-results` artifact if you want to feed them to another tool.

---

## Secret scanning

`.github/workflows/gitleaks.yml` runs [gitleaks](https://github.com/gitleaks/gitleaks)
(pinned, fetched from the upstream release) in two modes:

| Mode | Trigger | Scope | Gates? |
|---|---|---|---|
| `scan` | push, PR | only the commits the event introduced | **yes** — blocks the merge |
| `history` | weekly + manual | all commits, all refs | no — reports exposure |

The history job deliberately does not gate. Its findings are already public, so
failing every Monday would just train people to ignore it.

### Publishing safely

This repository is public, and **a gitleaks report contains the secrets it
found**. Uploading one as a workflow artifact would hand every finding to
anyone who opens the run page — turning a protective scan into a second
disclosure. So:

- every invocation passes `--redact`;
- no report is ever uploaded as an artifact;
- the job summary is rendered by `ci/gitleaks_summary.py`, which works from an
  **allowlist** of publishable fields (rule, file, line, commit, date, author)
  and can never emit `Secret`, `Match`, `Email`, or the commit message — not
  even if a future gitleaks version adds a new field carrying one.

`ci/test_gitleaks_summary.py` proves that with a canary secret pushed through
every rendering path.

### Config

`.gitleaks.toml` extends the default rules and allowlists only provable noise —
vendored `matlab/external/`, binary fixtures, and Lazarus `.lfm` form
resources, whose embedded base64 bitmaps accounted for 25 of 53 raw history
findings.

**Allowlisting policy:** an entry asserts "this is not a secret", or "this was
a secret, it has been rotated, and the historical hit is now noise". Never
allowlist a live credential to quiet the scan — rotate it first.

### Running it locally

```bash
curl -sSfL -o /tmp/gl.tar.gz \
  https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz
tar -xzf /tmp/gl.tar.gz -C /tmp gitleaks

/tmp/gitleaks git . --config .gitleaks.toml --redact --log-opts="--all" \
  --report-format json --report-path /tmp/gl.json --exit-code 0
python3 ci/gitleaks_summary.py /tmp/gl.json
```

Drop `--redact` only on a trusted machine, and never commit or upload the
resulting report.
