#!/usr/bin/env bash
# One entry point an agent must pass before it may say "done".
# Runs the real tools this repo has, then the repo's own rules. First failure wins.
#
#   ./slopguard/verify.sh [--quick]     # --quick skips the slow suites
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"   # the kit's own repo, never git toplevel:
cd "$ROOT" || exit 1                        # $HOME can itself be a git repo
QUICK=${1:-}
fail() { echo "FAIL: $1" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
run()  { echo "== $*"; "$@" || fail "$*"; }

# Layer 1: the tools. Only what this repo actually has - no config, no guessing.
if [ -f go.mod ]; then
  run go build ./...
  run go vet ./...
  have golangci-lint && run golangci-lint run
  [ "$QUICK" = "--quick" ] || run go test -race ./...
fi

if ls ./*.py >/dev/null 2>&1 || [ -f pyproject.toml ]; then
  PY=$([ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)
  have ruff && run ruff check .
  run "$PY" -m compileall -q . -x "(\.venv|node_modules|\.git)"
  for t in test_*.py tests/test_*.py; do
    [ -f "$t" ] && run "$PY" "$t"
  done
fi

if [ -f package.json ]; then
  have npm && grep -q '"test"' package.json && { [ "$QUICK" = "--quick" ] || run npm test --silent; }
  have npx && [ -f tsconfig.json ] && run npx --no-install tsc --noEmit
fi

# Layer 2: this repo's own conventions, as checks.
run python3 "$(dirname "$0")/slopguard.py"

echo "VERIFIED"
