#!/usr/bin/env bash
set -euo pipefail

echo "=== [1/4] Checking protected file invariants (tracked & untracked) ==="
CHECKPOINT="${1:-HEAD}"

# 1. Check diff against checkpoint
if git rev-parse --verify "$CHECKPOINT" >/dev/null 2>&1; then
  PROTECTED_DIFF=$(git diff --name-only "$CHECKPOINT" -- .github/ SPEC.md scripts/verify_local.sh tests/immunity/ 2>/dev/null || true)
  if [ -n "$PROTECTED_DIFF" ]; then
    echo "ERROR: Protected files were modified without authorization (git diff):"
    echo "$PROTECTED_DIFF"
    exit 1
  fi
fi

# 2. Check untracked and staged protected files via git status
DIRTY_PROTECTED=$(git status --porcelain | awk '{print $2}' | grep -E '^(\.github/|SPEC\.md|scripts/verify_local\.sh|tests/immunity/)' || true)
if [ -n "$DIRTY_PROTECTED" ]; then
  echo "ERROR: Protected files were created or modified (git status):"
  echo "$DIRTY_PROTECTED"
  exit 1
fi

echo "=== [2/4] Running Ruff linter (if available) ==="
if command -v ruff >/dev/null 2>&1; then
  ruff check .
else
  echo "ruff not installed, skipping local lint check"
fi

echo "=== [3/4] Running Mypy type checker (if available) ==="
if command -v mypy >/dev/null 2>&1; then
  mypy src scripts
else
  echo "mypy not installed, skipping local type check"
fi

echo "=== [4/4] Running Python unit and acceptance tests ==="
if [ -x "/usr/bin/python3" ]; then
  PYTHON="/usr/bin/python3"
else
  PYTHON="python3"
fi
"$PYTHON" -m unittest discover -s tests -p "test_*.py"

echo "=== All verification checks passed! (Exit 0) ==="

