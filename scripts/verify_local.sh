#!/usr/bin/env bash
# auto-dev: verify-timeout=1200
# （検証の制限時間。macOS で全体のテストに約 8 分かかるため。DX-13 で auto-dev が読む）
set -euo pipefail

echo "=== [1/4] Checking protected file invariants (tracked & untracked) ==="
CHECKPOINT="${1:-HEAD}"

# 1. Check diff against checkpoint
if git rev-parse --verify "$CHECKPOINT" >/dev/null 2>&1; then
  PROTECTED_DIFF=$(git diff --name-only "$CHECKPOINT" -- .github/ SPEC.md scripts/verify_local.sh tests/immunity/ teams.json 'docs/designs/*.ir.json' 2>/dev/null || true)
  if [ -n "$PROTECTED_DIFF" ]; then
    echo "ERROR: Protected files were modified without authorization (git diff):"
    echo "$PROTECTED_DIFF"
    exit 1
  fi
fi

# 2. Check untracked and staged protected files via git status
DIRTY_PROTECTED=$(git status --porcelain | awk '{print $2}' | grep -E '^(\.github/|SPEC\.md|scripts/verify_local\.sh|tests/immunity/|teams\.json$|docs/designs/[^/]+\.ir\.json$)' || true)
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
  mypy --platform linux src scripts
  mypy --platform darwin src scripts
else
  echo "mypy not installed, skipping local type check"
fi

echo "=== [4/4] Running Python unit and acceptance tests ==="
# テストを動かす Python: 環境変数 PYTHON → PATH の python3 の順に選ぶ（DX-5 の雛形と同じ規則）。
# /usr/bin/python3 を優先しない（CI の Ubuntu ではマトリクスの Python ではなく OS の Python で走っていた）。
if [ -z "${PYTHON:-}" ]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
  else
    echo "ERROR: python3 not found to execute tests (set PYTHON to the interpreter to use)"
    exit 1
  fi
fi
echo "Using Python: $(command -v "$PYTHON") ($("$PYTHON" --version 2>&1))"
"$PYTHON" -m unittest discover -s tests -p "test_*.py"

echo "=== All verification checks passed! (Exit 0) ==="

