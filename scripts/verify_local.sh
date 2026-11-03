#!/usr/bin/env bash
set -euo pipefail

echo "=== [1/2] Checking protected file invariants in unstaged diff ==="
CHECKPOINT="${1:-HEAD}"
if git rev-parse --verify "$CHECKPOINT" >/dev/null 2>&1; then
  PROTECTED_DIFF=$(git diff --name-only "$CHECKPOINT" -- .github/ SPEC.md scripts/verify_local.sh tests/immunity/ 2>/dev/null || true)
  if [ -n "$PROTECTED_DIFF" ]; then
    echo "ERROR: Protected files were modified without authorization:"
    echo "$PROTECTED_DIFF"
    exit 1
  fi
fi

echo "=== [2/2] Running Python unit and acceptance tests ==="
if [ -x "/usr/bin/python3" ]; then
  PYTHON="/usr/bin/python3"
else
  PYTHON="python3"
fi
"$PYTHON" -m unittest discover -s tests -p "test_*.py"

echo "=== All verification checks passed! (Exit 0) ==="
