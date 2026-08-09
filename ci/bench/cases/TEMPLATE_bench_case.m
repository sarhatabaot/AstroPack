function Result = TEMPLATE_bench_case()
    % TEMPLATE_bench_case  Contract for a quality benchmark case. Not run.
    %
    % Copy this to ci/bench/cases/bench_<name>.m, rename the function to
    % match the filename, and fill in the three sections. runBench.m skips
    % this template by name.
    %
    % WHAT A CASE IS
    %   A case runs a piece of the pipeline over a FIXED input and reduces
    %   the output to a handful of scalar quality numbers. It answers "how
    %   good", not "did it error" -- that is what tests/ is for.
    %
    % RULES
    %   1. Deterministic. Fixed inputs, fixed seed. If a case can return a
    %      different number on an unchanged tree, its baseline is noise and
    %      the gate is worthless.
    %   2. Metrics are real finite numeric SCALARS. Names must be valid
    %      MATLAB identifiers (they become JSON keys).
    %   3. Inputs are committed fixtures or a path from an environment
    %      variable -- never a hardcoded absolute path.
    %   4. Fast enough to run often. Push a multi-hour reduction into a
    %      scheduled job, not the per-change gate.
    %
    % See ci/README.md for how baselines are recorded and compared.

    % --- 1. Arrange: fixed input ----------------------------------------
    % RepoRoot = fileparts(fileparts(fileparts(fileparts(mfilename('fullpath')))));
    % DataFile = fullfile(RepoRoot, 'data', 'test_images', 'fits_fv_sample_data', ...
    %                     'sample.image.fits');

    % --- 2. Act: run the thing under measurement -------------------------
    % AI = AstroImage(DataFile);
    % AI = imProc.sources.findMeasureSources(AI);

    % --- 3. Reduce to scalar quality metrics -----------------------------
    Result.metrics = struct( ...
        'exampleResidualRmsArcsec', 0.0, ...
        'exampleSourceCount',       0.0);

    % Tolerances drive the gate. Per metric:
    %   abs       - absolute tolerance (same units as the metric)
    %   rel       - relative tolerance, fraction of |baseline|
    %               A metric passes if it is within EITHER tolerance.
    %   direction - 'lower_is_better'  improvements never fail, however large
    %               'higher_is_better' likewise, in the other direction
    %               'any'              any move beyond tolerance fails
    %                                  (use for values that must stay stable)
    Result.tolerances = struct( ...
        'exampleResidualRmsArcsec', struct('abs', 0.01, 'rel', 0.05, ...
                                           'direction', 'lower_is_better'), ...
        'exampleSourceCount',       struct('abs', 5, 'rel', 0.02, ...
                                           'direction', 'any'));

    % Provenance for whoever reads a regression report six months from now.
    Result.meta = struct( ...
        'description', 'One line: what this measures and why it matters.', ...
        'dataset',     'Which fixed input, and where it lives.', ...
        'owner',       'Who to ask when this goes red.');
end
