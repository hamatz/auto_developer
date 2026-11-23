#!/usr/bin/env bash
set -euo pipefail

PYTHON="${PYTHON:-python3}"
PYTHONPATH=. "$PYTHON" -m unittest discover -s tests -p "test_*.py"
