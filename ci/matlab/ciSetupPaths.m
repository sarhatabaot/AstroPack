function RepoRoot = ciSetupPaths(Args)
    % ciSetupPaths  Put AstroPack on the MATLAB path deterministically for CI.
    %
    % Wraps the repository's own matlab/startup/startup.m so CI uses the same
    % path layout developers use, with two deliberate differences:
    %
    %   1. Randomness is seeded, not shuffled. startup.m calls rng('shuffle'),
    %      which would make any test using random data unreproducible between
    %      runs. CI seeds a fixed value instead.
    %   2. ASTROPACK_PATH / ASTROPACK_CONFIG_PATH are pointed at this checkout
    %      rather than at whatever the host machine happens to have installed.
    %
    % Input  : ...,key,val,...
    %          'Seed'      - RNG seed for reproducibility. Default is 0.
    %          'Verbose'   - Print the resolved environment. Default is true.
    %
    % Output : RepoRoot - Absolute path to the repository root.
    %
    % Example: RepoRoot = ciSetupPaths();

    arguments
        Args.Seed (1,1) double = 0
        Args.Verbose (1,1) logical = true
    end

    % ci/matlab/ciSetupPaths.m  ->  repo root is two levels up.
    ThisDir  = fileparts(mfilename('fullpath'));
    RepoRoot = fileparts(fileparts(ThisDir));

    % Point AstroPack's startup at this checkout unless the caller already
    % chose otherwise (lets a developer run against a different tree).
    if isempty(getenv('ASTROPACK_PATH'))
        setenv('ASTROPACK_PATH', RepoRoot);
    end
    if isempty(getenv('ASTROPACK_CONFIG_PATH'))
        % startup.m appends 'config' to this value.
        setenv('ASTROPACK_CONFIG_PATH', RepoRoot);
    end

    addpath(fullfile(RepoRoot, 'matlab', 'startup'));

    % setRandomNumbers=false suppresses rng('shuffle'); we seed explicitly
    % below. UpdateTime=false keeps CI off the network.
    startup('setRandomNumbers', false, 'UpdateTime', false, ...
            'AstroPack_BasePath', RepoRoot, ...
            'AstroPack_ConfigPath', RepoRoot);

    rng(Args.Seed, 'twister');

    % Test helper classes (e.g. HealpixTestHelper) live beside the tests and
    % must be resolvable by name, so every test folder goes on the path.
    TestsRoot = fullfile(RepoRoot, 'tests');
    if isfolder(TestsRoot)
        warning('off', 'MATLAB:dispatcher:nameConflict');
        addpath(genpath(TestsRoot));
        warning('on', 'MATLAB:dispatcher:nameConflict');
    end

    if Args.Verbose
        fprintf('CI RepoRoot           : %s\n', RepoRoot);
        fprintf('CI MATLAB             : %s\n', version());
        fprintf('CI ASTROPACK_PATH     : %s\n', getenv('ASTROPACK_PATH'));
        fprintf('CI ASTROPACK_DATA_PATH: %s\n', getenv('ASTROPACK_DATA_PATH'));
        fprintf('CI RNG seed           : %d\n', Args.Seed);
    end
end
