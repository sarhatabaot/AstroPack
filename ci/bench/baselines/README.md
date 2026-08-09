# Benchmark baselines

One JSON file per benchmark case, named after the case (`bench_<name>.json`).
Each records the metric values that were accepted as correct, plus the
provenance of the run that produced them (git SHA, MATLAB version, seed).

**These files are committed.** They are the reference the quality gate
compares against, so a baseline change must be reviewable in a diff — that is
the whole point. A pull request that moves a baseline is asserting "the new
numbers are better, and here is why".

Record or update them with:

```bash
matlab -batch "addpath('ci/matlab'); addpath('ci/bench'); runBench();"
python3 ci/bench/compare.py --record            # all cases
python3 ci/bench/compare.py --record --only bench_photcal   # one case
```

Never hand-edit the `metrics` block. Tolerances are read from the case file at
compare time, not from here, so retuning a gate does not need a re-record.
