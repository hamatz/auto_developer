#!/usr/bin/env python3
"""Verify auto-dev commands in README and SKILL.md files against build_parser().

Checked patterns:
- Lines inside fenced code blocks (``` and ~~~), stripping a leading "$ ".
- Backslash-continued multi-line commands (reported at the starting line).
- Inline code spans (`auto-dev ...`) outside fenced code blocks.
- Segments split on &&, ||, ;, | outside single/double quotes, skipping leading VAR=value.

Out of scope (not checked):
- Nested command substitutions ($(...) or `...` inside commands).
- Wrapper invocations such as `uv run` or `python -m`.
- Heredocs.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from auto_developer.cli import build_parser

_FENCE_RE = re.compile(r"^\s*(```+|~~~+)")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_ENV_PREFIX_RE = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|\S*)\s+)+")


@dataclass
class OptionSpec:
    """Metadata for a single CLI option string."""

    takes_value: bool
    optional_value: bool = False


@dataclass
class ParserSpec:
    """Extracted CLI parser specification."""

    name: str
    options: dict[str, OptionSpec]
    subcommands: dict[str, ParserSpec]


def _extract_parser_spec(parser: argparse.ArgumentParser, name: str) -> ParserSpec:
    options: dict[str, OptionSpec] = {}
    subcommands: dict[str, ParserSpec] = {}

    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sub_name, sub_parser in action.choices.items():
                subcommands[sub_name] = _extract_parser_spec(sub_parser, f"{name} {sub_name}")
        elif action.option_strings:
            is_flag = action.nargs == 0 or isinstance(
                action,
                (
                    argparse._StoreTrueAction,
                    argparse._StoreFalseAction,
                    argparse._HelpAction,
                    argparse._VersionAction,
                    argparse._CountAction,
                ),
            )
            takes_value = not is_flag
            optional_value = action.nargs == "?"
            for opt in action.option_strings:
                options[opt] = OptionSpec(takes_value=takes_value, optional_value=optional_value)

    return ParserSpec(name=name, options=options, subcommands=subcommands)


def _tokenize_command(cmd: str) -> list[str]:
    """Tokenize a shell-like command, keeping quotes and <...> placeholders intact."""
    tokens: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(cmd)
    in_quote: str | None = None
    in_angle = False

    def _flush() -> None:
        if buf:
            tokens.append("".join(buf))
            buf.clear()

    while i < n:
        ch = cmd[i]
        if in_quote is not None:
            if ch == "\\" and in_quote == '"' and i + 1 < n:
                buf.append(cmd[i + 1])
                i += 2
                continue
            if ch == in_quote:
                in_quote = None
                i += 1
                continue
            buf.append(ch)
            i += 1
            continue

        if in_angle:
            buf.append(ch)
            if ch == ">":
                in_angle = False
            i += 1
            continue

        if ch in ("'", '"'):
            in_quote = ch
            i += 1
            continue

        if ch == "<":
            in_angle = True
            buf.append(ch)
            i += 1
            continue

        if ch == "#" and not buf:
            break

        if ch in (";", "|"):
            _flush()
            break

        if ch == "&" and i + 1 < n and cmd[i + 1] == "&":
            _flush()
            break

        if ch.isspace():
            _flush()
            i += 1
            continue

        buf.append(ch)
        i += 1

    _flush()
    return tokens


def _split_shell_segments(line: str) -> list[str]:
    """Split a shell line on &&, ||, ;, | outside single and double quotes."""
    segments: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(line)
    in_quote: str | None = None

    while i < n:
        ch = line[i]
        if in_quote is not None:
            buf.append(ch)
            if ch == "\\" and in_quote == '"' and i + 1 < n:
                buf.append(line[i + 1])
                i += 2
                continue
            if ch == in_quote:
                in_quote = None
            i += 1
            continue

        if ch in ("'", '"'):
            in_quote = ch
            buf.append(ch)
            i += 1
            continue

        if ch == "\\" and i + 1 < n:
            buf.append(ch)
            buf.append(line[i + 1])
            i += 2
            continue

        if ch == ";":
            segments.append("".join(buf))
            buf.clear()
            i += 1
            continue

        if ch == "&" and i + 1 < n and line[i + 1] == "&":
            segments.append("".join(buf))
            buf.clear()
            i += 2
            continue

        if ch == "|":
            segments.append("".join(buf))
            buf.clear()
            i += 2 if (i + 1 < n and line[i + 1] == "|") else 1
            continue

        buf.append(ch)
        i += 1

    segments.append("".join(buf))
    return segments


def _extract_auto_dev_segments(line: str) -> list[str]:
    """Extract auto-dev command segments from a shell line."""
    stripped = line.lstrip()
    if stripped.startswith("$ "):
        stripped = stripped[2:].lstrip()
    segments: list[str] = []
    for part in _split_shell_segments(stripped):
        p = part.lstrip()
        p = _ENV_PREFIX_RE.sub("", p)
        if p == "auto-dev" or (p.startswith("auto-dev") and p[8:9].isspace()):
            segments.append(p)
    return segments


def _extract_doc_commands(path: Path) -> list[tuple[int, str]]:
    """Return list of (1-based line number, command string) from a Markdown file."""
    lines = path.read_text(encoding="utf-8").splitlines()
    results: list[tuple[int, str]] = []

    in_fence = False
    fence_char = ""
    fence_len = 0
    acc_line: list[str] = []
    acc_start = 0

    def _flush_acc() -> None:
        nonlocal acc_line, acc_start
        if not acc_line:
            return
        joined = " ".join(acc_line)
        for seg in _extract_auto_dev_segments(joined):
            results.append((acc_start, seg))
        acc_line = []
        acc_start = 0

    for idx, raw_line in enumerate(lines, start=1):
        fence_match = _FENCE_RE.match(raw_line)
        if not in_fence:
            if fence_match:
                marker = fence_match.group(1)
                in_fence = True
                fence_char = marker[0]
                fence_len = len(marker)
                continue
            # Outside code block: check inline code spans
            for inline_match in _INLINE_CODE_RE.finditer(raw_line):
                snippet = inline_match.group(1).strip()
                for seg in _extract_auto_dev_segments(snippet):
                    results.append((idx, seg))
        else:
            if fence_match:
                marker = fence_match.group(1)
                if marker[0] == fence_char and len(marker) >= fence_len:
                    _flush_acc()
                    in_fence = False
                    continue

            stripped_right = raw_line.rstrip()
            has_cont = stripped_right.endswith("\\") and not stripped_right.endswith("\\\\")
            part = stripped_right[:-1].rstrip() if has_cont else stripped_right
            if not acc_line:
                acc_start = idx
            acc_line.append(part.strip() if len(acc_line) > 0 else part)
            if not has_cont:
                _flush_acc()

    _flush_acc()
    return results


def _validate_command(tokens: list[str], root_spec: ParserSpec) -> tuple[list[str], str | None]:
    """Validate tokenized auto-dev command against root_spec.

    Returns (errors, top_level_subcommand).
    """
    if not tokens or tokens[0] != "auto-dev":
        return ([], None)

    args = tokens[1:]
    spec = root_spec
    top_sub: str | None = None
    placeholder_sub = False
    errors: list[str] = []

    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "...":
            i += 1
            continue

        if tok.startswith("-") and tok != "-":
            opt_name = tok.split("=", 1)[0]
            has_eq = "=" in tok
            if opt_name not in spec.options:
                if placeholder_sub and opt_name in root_spec.options:
                    opt_spec = root_spec.options[opt_name]
                else:
                    errors.append(f"unknown option '{opt_name}' for '{spec.name}'")
                    i += 1
                    continue
            else:
                opt_spec = spec.options[opt_name]

            if opt_spec.takes_value and not has_eq:
                if opt_spec.optional_value:
                    if (
                        i + 1 < len(args)
                        and not args[i + 1].startswith("-")
                        and args[i + 1] not in spec.subcommands
                    ):
                        i += 2
                        continue
                elif i + 1 < len(args):
                    i += 2
                    continue
            i += 1
            continue

        if spec.subcommands and not placeholder_sub:
            if tok.startswith("<") and tok.endswith(">"):
                placeholder_sub = True
                i += 1
                continue
            if tok in spec.subcommands:
                if spec is root_spec:
                    top_sub = tok
                spec = spec.subcommands[tok]
                i += 1
                continue
            errors.append(f"unknown subcommand '{tok}' for '{spec.name}'")
            break

        i += 1

    return (errors, top_sub)


def collect_doc_files(root: Path) -> list[Path]:
    """Collect README.md, README.en.md, and skills/*/SKILL.md under root."""
    files: list[Path] = []
    for name in ("README.md", "README.en.md"):
        candidate = root / name
        if candidate.is_file():
            files.append(candidate)
    skills_dir = root / "skills"
    if skills_dir.is_dir():
        files.extend(sorted(skills_dir.glob("*/SKILL.md")))
    return files


def check_docs(root: Path) -> int:
    """Check all target documentation files under root against build_parser()."""
    root_spec = _extract_parser_spec(build_parser(), "auto-dev")
    doc_files = collect_doc_files(root)
    if not doc_files:
        print(f"FAILED: no target documentation files found under {root}.", file=sys.stderr)
        return 1

    all_errors: list[str] = []
    readme_subcommands: set[str] = set()
    checked_commands = 0

    for doc_path in doc_files:
        try:
            rel_path = doc_path.relative_to(root).as_posix()
        except ValueError:
            rel_path = str(doc_path)

        commands = _extract_doc_commands(doc_path)
        for line_no, cmd_str in commands:
            tokens = _tokenize_command(cmd_str)
            if not tokens or tokens[0] != "auto-dev":
                continue
            checked_commands += 1
            errs, top_sub = _validate_command(tokens, root_spec)
            if rel_path == "README.md" and top_sub is not None:
                readme_subcommands.add(top_sub)
            for err in errs:
                all_errors.append(f"{rel_path}:{line_no}: {err} (command: {cmd_str})")

    for err_line in all_errors:
        print(err_line, file=sys.stderr)

    unlisted = [name for name in root_spec.subcommands if name not in readme_subcommands]
    if unlisted:
        print(f"Reference — subcommands not mentioned in README.md: {', '.join(unlisted)}")
    else:
        print("Reference — all top-level subcommands are mentioned in README.md.")

    if all_errors:
        print(
            f"FAILED: {len(all_errors)} command error(s) found across {len(doc_files)} file(s).",
            file=sys.stderr,
        )
        return 1

    print(f"OK: {checked_commands} command(s) verified across {len(doc_files)} file(s).")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Verify auto-dev commands in README and SKILL.md against CLI parser."
    )
    ap.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root directory containing README.md, README.en.md, and skills/",
    )
    args = ap.parse_args(argv)
    return check_docs(args.root.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
