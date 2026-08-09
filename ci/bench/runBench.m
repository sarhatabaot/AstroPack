function Report = runBench(Args)
    % runBench  Run the quality benchmark cases and emit a metrics file.
    %
    % This is the "does it perform well?" half of CI. Unit tests answer
    % yes/no; a benchmark case answers "how good is the result now, compared
    % with a recorded baseline". It measures scientific quality -- residuals,
    % scatter, completeness, throughput -- not just absence of errors.
    %
    % A case is a function ci/bench/cases/bench_<name>.m returning a struct
    % with fields 'metrics', 'tolerances', and 'meta'. See
    % ci/bench/cases/TEMPLATE_bench_case.m for the contract.
    %
    % This function only MEASURES. Comparison against the baseline and the
    % pass/fail decision live in ci/bench/compare.py, so the gate can be
    % re-run, tuned, and tested without MATLAB.
    %
    % Input  : ...,key,val,...
    %          'CaseDir' - Folder of bench_*.m cases. Default ci/bench/cases.
    %          'Out'     - Metrics JSON to write. Default
    %                      ci/bench/results/metrics.json.
    %          'Only'    - Substring filter on case names. Default '' (all).
    %          'Seed'    - RNG seed. Default is 0.
    %
    % Output : Report - the struct that was serialised to JSON.
    %
    % Example: runBench();
    %          runBench('Only', 'photcal');

    arguments
        Args.CaseDir = ''
        Args.Out     = ''
        Args.Only    = ''
        Args.Seed (1,1) double = 0
    end

    RepoRoot = ciSetupPaths('Seed', Args.Seed);

    if isempty(Args.CaseDir)
        Args.CaseDir = fullfile(RepoRoot, 'ci', 'bench', 'cases');
    end
    if isempty(Args.Out)
        Args.Out = fullfile(RepoRoot, 'ci', 'bench', 'results', 'metrics.json');
    end
    addpath(Args.CaseDir);

    Files = dir(fullfile(Args.CaseDir, 'bench_*.m'));
    Names = setdiff({Files.name}, {'TEMPLATE_bench_case.m'});
    if ~isempty(Args.Only)
        Names = Names(contains(Names, Args.Only));
    end

    fprintf('\n=== AstroPack CI: quality benchmark ===\n');
    fprintf('Case dir : %s\n', Args.CaseDir);
    fprintf('Cases    : %d\n\n', numel(Names));

    Report = struct();
    Report.schemaVersion = 1;
    Report.generated     = utcStamp();
    Report.gitSha        = gitSha(RepoRoot);
    Report.matlabVersion = version();
    Report.seed          = Args.Seed;
    Report.cases         = struct();

    for I = 1:numel(Names)
        [~, CaseName] = fileparts(Names{I});
        fprintf('--- %s ---\n', CaseName);
        Entry = runCase(CaseName);
        Report.cases.(CaseName) = Entry;

        if strcmp(Entry.status, 'ok')
            MetricNames = fieldnames(Entry.metrics);
            for K = 1:numel(MetricNames)
                fprintf('    %-28s %g\n', MetricNames{K}, ...
                        Entry.metrics.(MetricNames{K}));
            end
            fprintf('    (%.2f s)\n', Entry.durationSeconds);
        else
            fprintf('    ERROR: %s\n', Entry.message);
        end
    end

    OutDir = fileparts(Args.Out);
    if ~isfolder(OutDir)
        mkdir(OutDir);
    end
    Fid = fopen(Args.Out, 'w');
    Cleanup = onCleanup(@() fclose(Fid)); %#ok<NASGU>
    % No 'PrettyPrint': that jsonencode option postdates R2020b, which the
    % lab machines run. compare.py reads this, and writes readable baselines.
    fprintf(Fid, '%s', jsonencode(Report));

    fprintf('\nMetrics written: %s\n', Args.Out);
    fprintf('Compare with:    python3 ci/bench/compare.py\n');
end

% --- helpers -------------------------------------------------------------

function Entry = runCase(CaseName)
    Entry = struct('status', 'error', 'message', '', ...
                   'durationSeconds', 0, 'meta', struct(), ...
                   'metrics', struct(), 'tolerances', struct());
    T = tic;
    try
        Result = feval(CaseName);

        if ~isstruct(Result) || ~isfield(Result, 'metrics')
            error('runBench:BadCase', ...
                  'Case must return a struct with a "metrics" field.');
        end
        validateMetrics(Result.metrics, CaseName);

        Entry.metrics = Result.metrics;
        if isfield(Result, 'tolerances')
            Entry.tolerances = Result.tolerances;
        end
        if isfield(Result, 'meta')
            Entry.meta = Result.meta;
        end
        Entry.status = 'ok';
    catch ME
        Entry.message = sprintf('%s: %s', ME.identifier, ME.message);
    end
    Entry.durationSeconds = toc(T);
end

function validateMetrics(Metrics, CaseName)
    % Every metric must be a real finite scalar: the comparator comes from a
    % different language and a NaN or a matrix silently breaks the diff.
    if ~isstruct(Metrics) || isempty(fieldnames(Metrics))
        error('runBench:NoMetrics', '%s returned no metrics.', CaseName);
    end
    Names = fieldnames(Metrics);
    for I = 1:numel(Names)
        V = Metrics.(Names{I});
        if ~(isnumeric(V) || islogical(V)) || ~isscalar(V) || ~isreal(V)
            error('runBench:BadMetric', ...
                  '%s.%s must be a real numeric scalar.', CaseName, Names{I});
        end
        if ~isfinite(V)
            error('runBench:NonFiniteMetric', ...
                  '%s.%s is %g; metrics must be finite.', ...
                  CaseName, Names{I}, V);
        end
    end
end

function Sha = gitSha(RepoRoot)
    Sha = 'unknown';
    try
        Cmd = sprintf('git -C "%s" rev-parse HEAD', RepoRoot);
        [Status, Out] = system(Cmd);
        if Status == 0
            Sha = strtrim(Out);
        end
    catch
        % Leave as 'unknown' -- provenance is nice to have, not required.
    end
end

function S = utcStamp()
    D = datetime('now', 'TimeZone', 'UTC');
    S = char(string(D, 'yyyy-MM-dd''T''HH:mm:ss''Z'''));
end
