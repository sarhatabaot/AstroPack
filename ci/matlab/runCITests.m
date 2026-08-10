function Summary = runCITests(Args)
    % runCITests  Run the enforced ("green") AstroPack test tier for CI.
    %
    % Reads ci/suite.json, builds a suite from the files listed under "green",
    % runs it, writes JUnit XML plus a JSON summary, and -- when the config
    % has "enforce": true -- errors on any failure so the CI job goes red.
    %
    % Quarantined files are never run here. They are listed in suite.json with
    % a reason, and are exercised only by the triage workflow. This keeps CI
    % meaningful from day one instead of permanently red.
    %
    % Input  : ...,key,val,...
    %          'Config'   - Path to suite.json. Default is ci/suite.json.
    %          'OutDir'   - Where to write artifacts. Default is ci/results.
    %          'Enforce'  - Override the config's "enforce" flag. Default [].
    %          'Seed'     - RNG seed. Default is 0.
    %
    % Output : Summary - struct with counts and per-test detail.
    %
    % Example: runCITests();
    %          runCITests('Enforce', true);

    arguments
        Args.Config  = ''
        Args.OutDir  = ''
        Args.Enforce = []
        Args.Seed (1,1) double = 0
    end

    RepoRoot = ciSetupPaths('Seed', Args.Seed);

    if isempty(Args.Config)
        Args.Config = fullfile(RepoRoot, 'ci', 'suite.json');
    end
    if isempty(Args.OutDir)
        Args.OutDir = fullfile(RepoRoot, 'ci', 'results');
    end
    if ~isfolder(Args.OutDir)
        mkdir(Args.OutDir);
    end

    if ~isfile(Args.Config)
        error('runCITests:MissingConfig', ...
              ['Suite config not found: %s\n' ...
               'Run the triage workflow first to generate it ' ...
               '(see ci/README.md).'], Args.Config);
    end

    Config = jsondecode(fileread(Args.Config));
    Green  = normalizeList(getFieldOr(Config, 'green', {}));
    Quar   = getFieldOr(Config, 'quarantine', []);
    Enforce = getFieldOr(Config, 'enforce', false);
    if ~isempty(Args.Enforce)
        Enforce = Args.Enforce;
    end

    fprintf('\n=== AstroPack CI: functional test run ===\n');
    fprintf('Config      : %s\n', Args.Config);
    fprintf('Green tier  : %d file(s)\n', numel(Green));
    fprintf('Quarantined : %d file(s)\n', numelQuarantine(Quar));
    fprintf('Enforcing   : %d\n\n', Enforce);

    if isempty(Green)
        fprintf(['No files in the green tier yet. Nothing to run.\n' ...
                 'Bootstrap it with the triage workflow (ci/README.md).\n']);
        Summary = emptySummary();
        writeSummary(Args.OutDir, Summary);
        return
    end

    % Build the suite file by file so one unbuildable file cannot abort the
    % whole run -- it is reported as a build error instead.
    % Seed from the concrete class fromFile() returns; TestSuite itself is
    % abstract.
    Suite = matlab.unittest.Test.empty(1, 0);
    BuildErrors = struct('file', {}, 'message', {});
    for I = 1:numel(Green)
        RelPath  = Green{I};
        FullPath = fullfile(RepoRoot, RelPath);
        if ~isfile(FullPath)
            BuildErrors(end+1) = struct('file', RelPath, ...
                'message', 'File listed in suite.json does not exist.'); %#ok<AGROW>
            continue
        end
        try
            Suite = [Suite, matlab.unittest.TestSuite.fromFile(FullPath)]; %#ok<AGROW>
        catch ME
            BuildErrors(end+1) = struct('file', RelPath, ...
                'message', ME.message); %#ok<AGROW>
        end
    end

    if ~isempty(BuildErrors)
        fprintf('%d file(s) failed to build into a suite:\n', numel(BuildErrors));
        for I = 1:numel(BuildErrors)
            fprintf('  %s\n    %s\n', BuildErrors(I).file, BuildErrors(I).message);
        end
        fprintf('\n');
    end

    if isempty(Suite)
        fprintf(['No test cases were built from the %d green file(s).\n' ...
                 'Every one of them failed to load -- see above.\n'], ...
                numel(Green));
        Summary = summarize(matlab.unittest.TestResult.empty(1, 0), BuildErrors);
        writeSummary(Args.OutDir, Summary);
        if Enforce
            error('runCITests:NothingRan', ...
                  'The green tier produced no runnable test cases.');
        end
        return
    end

    Runner = matlab.unittest.TestRunner.withTextOutput( ...
        'OutputDetail', matlab.unittest.Verbosity.Concise);
    JUnitFile = fullfile(Args.OutDir, 'junit.xml');
    Runner.addPlugin(matlab.unittest.plugins.XMLPlugin.producingJUnitFormat(JUnitFile));
    Runner.addPlugin(matlab.unittest.plugins.DiagnosticsRecordingPlugin);

    Results = Runner.run(Suite);

    Summary = summarize(Results, BuildErrors);
    writeSummary(Args.OutDir, Summary);

    fprintf('\n=== Summary ===\n');
    fprintf('Total       : %d\n', Summary.total);
    fprintf('Passed      : %d\n', Summary.passed);
    fprintf('Failed      : %d\n', Summary.failed);
    fprintf('Skipped     : %d\n', Summary.skipped);
    fprintf('BuildErrors : %d\n', Summary.buildErrors);
    fprintf('Duration    : %.1f s\n', Summary.durationSeconds);
    fprintf('JUnit XML   : %s\n', JUnitFile);

    if Summary.failed > 0
        fprintf('\nFailed tests:\n');
        for I = 1:numel(Summary.failures)
            fprintf('  %s\n', Summary.failures(I).name);
        end
    end

    if Enforce && (Summary.failed > 0 || Summary.buildErrors > 0)
        error('runCITests:Failed', ...
              '%d test failure(s) and %d suite build error(s).', ...
              Summary.failed, Summary.buildErrors);
    end
    if ~Enforce && (Summary.failed > 0 || Summary.buildErrors > 0)
        fprintf(['\nNOTE: "enforce" is false in suite.json, so this run is ' ...
                 'reporting only.\nSet it to true once the green tier is ' ...
                 'stable to make failures block merges.\n']);
    end
end

% --- helpers -------------------------------------------------------------

function Value = getFieldOr(S, Name, Default)
    if isstruct(S) && isfield(S, Name)
        Value = S.(Name);
    else
        Value = Default;
    end
end

function C = normalizeList(V)
    % jsondecode gives char for a 1-element string array, cellstr otherwise.
    if isempty(V)
        C = {};
    elseif ischar(V)
        C = {V};
    elseif iscell(V)
        C = V(:)';
    else
        C = cellstr(V(:))';
    end
end

function N = numelQuarantine(Q)
    if isempty(Q)
        N = 0;
    elseif isstruct(Q)
        N = numel(Q);
    elseif iscell(Q)
        N = numel(Q);
    else
        N = 0;
    end
end

function S = emptySummary()
    S = struct('total', 0, 'passed', 0, 'failed', 0, 'skipped', 0, ...
               'buildErrors', 0, 'durationSeconds', 0, ...
               'failures', struct('name', {}, 'message', {}), ...
               'matlabVersion', version(), 'timestampUTC', utcStamp());
end

function S = summarize(Results, BuildErrors)
    S = emptySummary();
    S.total       = numel(Results);
    S.passed      = sum([Results.Passed]);
    S.failed      = sum([Results.Failed]);
    % TestResult has no 'Status' property -- only Passed/Failed/Incomplete.
    % A test filtered by assumeTrue is Incomplete but not Failed; a test that
    % errored is both. So "skipped" is Incomplete minus the failures.
    S.skipped     = sum([Results.Incomplete] & ~[Results.Failed]);
    S.buildErrors = numel(BuildErrors);
    if isempty(Results)
        S.durationSeconds = 0;
    else
        S.durationSeconds = sum([Results.Duration]);
    end

    Failed = Results([Results.Failed]);
    for I = 1:numel(Failed)
        Msg = '';
        try
            Recs = Failed(I).Details.DiagnosticRecord;
            if ~isempty(Recs)
                Msg = strjoin(string({Recs.Report}), newline);
            end
        catch
            Msg = '(diagnostics unavailable)';
        end
        S.failures(end+1) = struct('name', Failed(I).Name, ...
                                   'message', char(Msg)); %#ok<AGROW>
    end
    for I = 1:numel(BuildErrors)
        S.failures(end+1) = struct( ...
            'name', ['[build] ' BuildErrors(I).file], ...
            'message', BuildErrors(I).message); %#ok<AGROW>
    end
end

function writeSummary(OutDir, Summary)
    Fid = fopen(fullfile(OutDir, 'summary.json'), 'w');
    Cleanup = onCleanup(@() fclose(Fid)); %#ok<NASGU>
    % Compact on purpose: jsonencode's 'PrettyPrint' option does not exist in
    % R2020b, which is what the lab machines run. This file is machine-read.
    fprintf(Fid, '%s', jsonencode(Summary));
end

function S = utcStamp()
    D = datetime('now', 'TimeZone', 'UTC');
    S = char(string(D, 'yyyy-MM-dd''T''HH:mm:ss''Z'''));
end
