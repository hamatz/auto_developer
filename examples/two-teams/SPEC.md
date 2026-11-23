# Two-Team Calculator Specification

## Core (`core` team)
- `add(a, b)`, `sub(a, b)`, `mul(a, b)` return the arithmetic result.
- `div(a, b)` returns `a / b`, and raises `ValueError("division by zero")` when `b == 0`.

## CLI (`cli` team)
- `main(argv)` evaluates `<op> <a> <b>` (`add`, `sub`, `mul`, `div`) and prints the numeric result.
- Invalid arguments, unknown operations, or division by zero must print a clear error message to `stderr` and return exit code `1` (never leak an unhandled exception).
