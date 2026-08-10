# Applied fixes

Changes made **outside `ci/`** as a result of CI findings. The CI system's job
is to surface problems; when one gets fixed rather than quarantined, the fix is
recorded here so a reviewer can see what was touched, why, and how it was
verified.

Fixes listed here are separate from `ci/suite.json`, which records what is
*excluded* and why. This file records what was *repaired*.

---

## 2026-08-10 — `tests/relativeData` fixtures unreachable outside one machine

**Measured impact:** 45 of the 111 failures in CI run #4 — the largest single
group, and the only one needing no new data, toolbox, or algorithm work.

### Symptom

Nine test files in `tests/astro/+celestial/+coo/` failed in `setupOnce`, taking
every test in the file down with them:

```
Unable to read file '/home/runner/matlab/AstroPack/tests/relativeData/expected_refraction_results.mat'
Unable to find or open '~/matlab/AstroPack/tests/relativeData/expected_proper_motion_results.csv'
```

### Root cause

Each file built the fixture path from the home directory:

```matlab
dataFilePath = fullfile('~/','matlab','AstroPack','tests','relativeData', 'expected_refraction_results.mat');
```

**The fixtures were never missing.** All nine are committed in this repository
under `tests/relativeData/`. Only the path was wrong — it assumed the working
copy lived at `~/matlab/AstroPack`, true for the original author and nobody
else.

Three distinct defects were tangled together:

1. **Wrong anchor.** `~` is the user's home, not the repository. Any clone
   outside `~/matlab/AstroPack` fails, including every CI checkout.
2. **Inconsistent `~` expansion.** The two error messages above come from the
   same wrong path: `load()` expanded `~` to `/home/runner/...`, while
   `readtable()` passed it through literally. Behaviour depended on which
   function consumed the path.
3. **Not portable to Windows.** `~` is not a home shortcut there;
   `fullfile('~','matlab',...)` yields a literal `~\matlab\...` resolved
   against the current folder. The files were not even self-consistent — seven
   used `fullfile('~/', ...)` and two `fullfile('~', ...)`.

### Fix

Added `tests/astro/+celestial/+coo/CooTestHelper.m`, mirroring the existing
`HealpixTestHelper` precedent, and pointed all nine call sites at it:

```matlab
dataFilePath = CooTestHelper.dataFile('expected_refraction_results.mat');
```

The helper anchors on `mfilename('fullpath')`, which resolves to where the file
physically sits — independent of the current folder, of `HOME`, of the MATLAB
path, and of which checkout `ASTROPACK_PATH` points at. That is what makes the
result identical in CI and on a developer machine. The same idiom already
appears in `tests/CLAUDE.md` Template D.

Only the helper knows how deep `+coo` sits beneath `tests/`, so the fragile
`fileparts` counting lives in exactly one place instead of nine.

`CooTestHelper.dataFile` also errors with a clear message when a fixture is
absent, since these files are committed — a miss means a broken checkout, not
an ordinary test failure. That let `test_spherical_triangle_circum_circle` drop
its hand-rolled existence check.

### Files changed

| File | Change |
|---|---|
| `tests/astro/+celestial/+coo/CooTestHelper.m` | **New** — path resolution helper |
| `test_convert_coo.m` | fixture path → helper |
| `test_convertdms.m` | fixture path → helper |
| `test_coo2box.m` | fixture path → helper |
| `test_coo_resolver.m` | fixture path → helper |
| `test_proper_motion.m` | fixture path → helper (`readtable` call) |
| `test_refraction.m` | fixture path → helper |
| `test_sky_area_above_am.m` | fixture path → helper; stale path in header comment |
| `test_spherical_tri_area.m` | fixture path → helper (also dropped a stray `;;`) |
| `test_spherical_triangle_circum_circle.m` | fixture path → helper; removed now-redundant existence check |

### Preventing recurrence

The `abs-path` lint rule already covered this class, and its baseline shrank
from 456 to 449 as the fixed sites dropped out. Any reintroduction now fails
the build.

Closing the loop exposed a hole in the rule itself: it required a separator
(`~/`), so `fullfile('~','matlab',...)` — the form two of these files used —
slipped through silently. A bare `~` is now flagged too, with a regression test
(`test_flags_a_bare_tilde_segment`). Verified by reintroducing both forms in a
scratch file: 2 new violations, exit 1.

### Status

These files are **still quarantined**, deliberately. The fix removes the load
error; it does not prove the assertions pass, because they have never run.
`ci/suite.json` records that, and a triage run promotes them once confirmed.

`test_sky_area_above_am` stays quarantined regardless: its other four cases
fail because `celestial.coo.sky_area_above_am` returns `NaN` for nominal
inputs, both poles, and high airmass — a product bug this fix does not touch.

### Note on repository conventions

`tests/CLAUDE.md` says not to modify existing test files. That rule governs
*generating new tests*; these edits were explicitly authorised as repairs to a
known-broken path. No test logic, expected value, or tolerance was changed —
only where the fixtures are looked up.
