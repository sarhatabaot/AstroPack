#!/usr/bin/env bash
# Install the repository git hooks. Run once per clone:
#
#     ci/install-hooks.sh
#
# Uses core.hooksPath so the hooks stay version-controlled in ci/hooks/ and
# update with the repo, rather than being copied into .git/hooks and going
# stale. Undo with:  git config --unset core.hooksPath

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

chmod +x ci/hooks/* ci/run-local.sh 2>/dev/null || true
git config core.hooksPath ci/hooks

echo "Hooks installed (core.hooksPath = ci/hooks)."
echo
echo "Active hooks:"
for h in ci/hooks/*; do
    [[ -f "$h" ]] && echo "  $(basename "$h")"
done
echo
echo "Bypass any hook once with:  git <cmd> --no-verify"
