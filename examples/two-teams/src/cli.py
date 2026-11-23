"""Calculator CLI entrypoint."""

from __future__ import annotations

import sys

from src.calc import add, sub


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 3:
        print("Usage: calc <add|sub> <a> <b>", file=sys.stderr)
        return 1
    op, raw_a, raw_b = args
    try:
        a = float(raw_a)
        b = float(raw_b)
    except ValueError:
        print(f"Invalid number: {raw_a}, {raw_b}", file=sys.stderr)
        return 1

    if op == "add":
        print(add(a, b))
        return 0
    if op == "sub":
        print(sub(a, b))
        return 0
    print(f"Unknown operation: {op}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
