classdef CooTestHelper
    % CooTestHelper  Shared utilities for celestial.coo unit tests.
    %
    % Resolves the committed regression fixtures in tests/relativeData/ from
    % the location of this file, so the tests run identically wherever the
    % repository is cloned -- in CI, on a laptop, on Windows.
    %
    % Why this exists: the tests used to build the path as
    %   fullfile('~/','matlab','AstroPack','tests','relativeData', Name)
    % which only worked on one person's machine. It failed two further ways:
    %   * '~' expansion is function-dependent -- load() expands it, readtable()
    %     does not, so the same wrong path produced two different errors.
    %   * On Windows '~' is not a home shortcut at all; fullfile('~',...)
    %     yields a literal '~\matlab\...' resolved against the current folder.
    %
    % mfilename('fullpath') has none of those problems: it resolves to where
    % this file physically is, independent of the current folder, of HOME, of
    % the MATLAB path, and of which checkout ASTROPACK_PATH points at.
    %
    % Only this class knows how deep tests/astro/+celestial/+coo sits beneath
    % tests/. Callers just ask for a fixture by name.
    %
    % Example: F = CooTestHelper.dataFile('expected_refraction_results.mat');

    methods (Static)

        function Path = relativeDataDir()
            % relativeDataDir  Absolute path of tests/relativeData.
            %
            % This file is at <repo>/tests/astro/+celestial/+coo/, so tests/
            % is three levels up from the folder containing it.

            CooDir = fileparts(mfilename('fullpath'));
            TestsDir = fileparts(fileparts(fileparts(CooDir)));
            Path = fullfile(TestsDir, 'relativeData');
        end

        function FileName = dataFile(Name)
            % dataFile  Absolute path of a fixture, erroring clearly if absent.
            %
            % These fixtures are committed, so a miss means a broken or
            % partial checkout rather than an ordinary test failure.

            FileName = fullfile(CooTestHelper.relativeDataDir(), Name);
            if ~isfile(FileName)
                error('CooTestHelper:MissingFixture', ...
                      ['Regression fixture "%s" not found at %s.\n' ...
                       'It is committed under tests/relativeData/ -- check ' ...
                       'the checkout is complete.'], Name, FileName);
            end
        end

        function Data = loadData(Name)
            % loadData  Load a .mat fixture and return the whole struct.

            Data = load(CooTestHelper.dataFile(Name));
        end

    end
end
