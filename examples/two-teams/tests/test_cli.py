"""Unit tests for calculator CLI."""

from __future__ import annotations

import contextlib
import io
import unittest

from src.cli import main


class TestCalcCli(unittest.TestCase):
    def test_add(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = main(["add", "2", "3"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue().strip(), "5.0")

    def test_invalid_number_returns_1_without_exception(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = main(["add", "abc", "3"])
        self.assertEqual(rc, 1)
        self.assertIn("Invalid number", err.getvalue())


if __name__ == "__main__":
    unittest.main()
