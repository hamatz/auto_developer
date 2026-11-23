from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

tests_dir = str(Path(__file__).resolve().parent)
if tests_dir not in sys.path:
    sys.path.insert(0, tests_dir)

from sandbox_test_helpers import (
    REQUIRE_SANDBOX_ENV_VAR,
    is_sandbox_required,
    require_platform_sandbox,
    should_mock_when_sandbox_unavailable,
    skip_or_fail_if_sandbox_unavailable,
)


class _DummyTestCase(unittest.TestCase):
    def runTest(self) -> None:  # pragma: no cover
        pass


class TestSandboxTestHelpers(unittest.TestCase):
    """SBX-4: AUTO_DEV_REQUIRE_SANDBOX=1 時の共通ヘルパー挙動を検証するテスト。"""

    def setUp(self) -> None:
        self.dummy_case = _DummyTestCase()

    def test_is_sandbox_required_flag(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(REQUIRE_SANDBOX_ENV_VAR, None)
            self.assertFalse(is_sandbox_required())

        with patch.dict(os.environ, {REQUIRE_SANDBOX_ENV_VAR: "0"}, clear=False):
            self.assertFalse(is_sandbox_required())

        with patch.dict(os.environ, {REQUIRE_SANDBOX_ENV_VAR: "1"}, clear=False):
            self.assertTrue(is_sandbox_required())

    def test_skip_or_fail_fails_when_required_and_skips_otherwise(self) -> None:
        with (
            patch.dict(os.environ, {REQUIRE_SANDBOX_ENV_VAR: "1"}, clear=False),
            self.assertRaises(AssertionError),
        ):
            skip_or_fail_if_sandbox_unavailable(self.dummy_case, "no sandbox")

        with (
            patch.dict(os.environ, {REQUIRE_SANDBOX_ENV_VAR: "0"}, clear=False),
            self.assertRaises(unittest.SkipTest),
        ):
            skip_or_fail_if_sandbox_unavailable(self.dummy_case, "no sandbox")

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(REQUIRE_SANDBOX_ENV_VAR, None)
            with self.assertRaises(unittest.SkipTest):
                skip_or_fail_if_sandbox_unavailable(self.dummy_case, "no sandbox")

    def test_should_mock_when_sandbox_unavailable(self) -> None:
        unavailable_verifier = MagicMock()
        unavailable_verifier._get_sandbox_backend.return_value = None

        available_verifier = MagicMock()
        available_verifier._get_sandbox_backend.return_value = "bwrap"

        with patch.dict(os.environ, {REQUIRE_SANDBOX_ENV_VAR: "1"}, clear=False):
            with self.assertRaises(AssertionError):
                should_mock_when_sandbox_unavailable(self.dummy_case, unavailable_verifier)
            self.assertFalse(
                should_mock_when_sandbox_unavailable(self.dummy_case, available_verifier)
            )

        with patch.dict(os.environ, {REQUIRE_SANDBOX_ENV_VAR: "0"}, clear=False):
            self.assertTrue(
                should_mock_when_sandbox_unavailable(self.dummy_case, unavailable_verifier)
            )
            self.assertFalse(
                should_mock_when_sandbox_unavailable(self.dummy_case, available_verifier)
            )

    def test_require_platform_sandbox_cross_platform_and_require_env(self) -> None:
        # 別 OS 向けのテストは AUTO_DEV_REQUIRE_SANDBOX=1 でもスキップされる
        with (
            patch.dict(os.environ, {REQUIRE_SANDBOX_ENV_VAR: "1"}, clear=False),
            patch("sandbox_test_helpers.sys.platform", "linux"),
            self.assertRaises(unittest.SkipTest),
        ):
            require_platform_sandbox(
                self.dummy_case,
                expected_platform="darwin",
                backend_available=False,
                platform_skip_reason="macOS only",
                unavailable_reason="sandbox-exec missing",
            )

        # 対象 OS 上でサンドボックスが使えない場合: =1 なら失敗、!=1 ならスキップ
        with patch("sandbox_test_helpers.sys.platform", "darwin"):
            with (
                patch.dict(os.environ, {REQUIRE_SANDBOX_ENV_VAR: "1"}, clear=False),
                self.assertRaises(AssertionError),
            ):
                require_platform_sandbox(
                    self.dummy_case,
                    expected_platform="darwin",
                    backend_available=False,
                    platform_skip_reason="macOS only",
                    unavailable_reason="sandbox-exec missing",
                )

            with (
                patch.dict(os.environ, {REQUIRE_SANDBOX_ENV_VAR: "0"}, clear=False),
                self.assertRaises(unittest.SkipTest),
            ):
                require_platform_sandbox(
                    self.dummy_case,
                    expected_platform="darwin",
                    backend_available=False,
                    platform_skip_reason="macOS only",
                    unavailable_reason="sandbox-exec missing",
                )

            # サンドボックスが利用可能ならそのまま通る
            with patch.dict(os.environ, {REQUIRE_SANDBOX_ENV_VAR: "1"}, clear=False):
                require_platform_sandbox(
                    self.dummy_case,
                    expected_platform="darwin",
                    backend_available=True,
                    platform_skip_reason="macOS only",
                    unavailable_reason="sandbox-exec missing",
                )


if __name__ == "__main__":
    unittest.main()
