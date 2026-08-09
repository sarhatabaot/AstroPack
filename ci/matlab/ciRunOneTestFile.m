function ciRunOneTestFile(RelPath, OutJson)
    % ciRunOneTestFile  Run ONE test file in isolation and write a JSON verdict.
    %
    % Triage worker. The Python driver (ci/triage.py) launches one MATLAB
    % process per test file so that a crash, an infinite loop, or a blocking
    % network call in one file cannot take down the whole discovery run.
    %
    % Always writes OutJson and always exits cleanly -- the verdict lives in
    % the file, not in the exit code. A missing OutJson means the process
    % crashed or was killed on timeout, which the driver records as such.
    %
    % Input  : RelPath - Test file path relative to the repository root.
    %          OutJson - Absolute path of the JSON verdict to write.
    %
    % Example: ciRunOneTestFile('tests/astro/+celestial/+coo/test_sphere_dist.m', ...
    %                           '/tmp/verdict.json');

    Verdict = struct('file', RelPath, 'status', 'error', 'total', 0, ...
                     'passed', 0, 'failed', 0, 'skipped', 0, ...
                     'durationSeconds', 0, 'message', '');

    try
        RepoRoot = ciSetupPaths('Verbose', false);
        FullPath = fullfile(RepoRoot, RelPath);

        if ~isfile(FullPath)
            Verdict.message = 'File does not exist.';
            writeVerdict(OutJson, Verdict);
            return
        end

        Suite = matlab.unittest.TestSuite.fromFile(FullPath);
        if isempty(Suite)
            Verdict.status  = 'empty';
            Verdict.message = 'File builds no test cases.';
            writeVerdict(OutJson, Verdict);
            return
        end

        Runner  = matlab.unittest.TestRunner.withNoPlugins();
        Results = Runner.run(Suite);

        Verdict.total           = numel(Results);
        Verdict.passed          = sum([Results.Passed]);
        Verdict.failed          = sum([Results.Failed]);
        Verdict.skipped         = sum(strcmp({Results.Status}, 'Skipped'));
        Verdict.durationSeconds = sum([Results.Duration]);

        if Verdict.failed > 0
            Verdict.status  = 'failed';
            Verdict.message = firstFailureMessage(Results);
        elseif Verdict.passed == 0 && Verdict.skipped > 0
            Verdict.status  = 'skipped';
            Verdict.message = 'All test cases were skipped (assumption failed).';
        else
            Verdict.status = 'passed';
        end

    catch ME
        % Covers suite-build failures: missing dependency, syntax error,
        % undefined function, a script that errors at parse time, etc.
        Verdict.status  = 'error';
        Verdict.message = sprintf('%s: %s', ME.identifier, ME.message);
    end

    writeVerdict(OutJson, Verdict);
end

% --- helpers -------------------------------------------------------------

function Msg = firstFailureMessage(Results)
    Msg = '';
    Failed = Results([Results.Failed]);
    if isempty(Failed)
        return
    end
    try
        Recs = Failed(1).Details.DiagnosticRecord;
        if ~isempty(Recs)
            Msg = char(Recs(1).Report);
        end
    catch
        Msg = '(diagnostics unavailable)';
    end
    Msg = truncate(Msg, 2000);
end

function S = truncate(S, N)
    if numel(S) > N
        S = [S(1:N) '...(truncated)'];
    end
end

function writeVerdict(OutJson, Verdict)
    OutDir = fileparts(OutJson);
    if ~isempty(OutDir) && ~isfolder(OutDir)
        mkdir(OutDir);
    end
    Fid = fopen(OutJson, 'w');
    if Fid < 0
        return
    end
    Cleanup = onCleanup(@() fclose(Fid)); %#ok<NASGU>
    % No 'PrettyPrint': that jsonencode option postdates R2020b.
    fprintf(Fid, '%s', jsonencode(Verdict));
end
