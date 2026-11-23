"""Unit tests for core calculator operations."""

from __future__ import annotations

import unittest

from src.calc import add, sub


class TestCoreCalc(unittest.TestCase):
    def test_add_and_sub(self) -> None:
        self.assertEqual(add(2, 3), 5)
        self.assertEqual(sub(5, 3), 2)


if __name__ == "__main__":
    unittest.main()
