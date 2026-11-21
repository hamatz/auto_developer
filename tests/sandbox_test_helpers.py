from __future__ import annotations

import os
import sys
import unittest
from typing import Any

REQUIRE_SANDBOX_ENV_VAR = "AUTO_DEV_REQUIRE_SANDBOX"


def is_sandbox_required() -> bool:
    """AUTO_DEV_REQUIRE_SANDBOX=1 が設定されているかを返す。"""
    return os.environ.get(REQUIRE_SANDBOX_ENV_VAR) == "1"


def skip_or_fail_if_sandbox_unavailable(test_case: unittest.TestCase, reason: str) -> None:
    """サンドボックスが使えないとき、AUTO_DEV_REQUIRE_SANDBOX=1 なら失敗、それ以外はスキップする。"""
    if is_sandbox_required():
        test_case.fail(f"{REQUIRE_SANDBOX_ENV_VAR}=1 requires a working sandbox: {reason}")
    test_case.skipTest(reason)


def require_platform_sandbox(
    test_case: unittest.TestCase,
    *,
    expected_platform: str,
    backend_available: bool,
    platform_skip_reason: str,
    unavailable_reason: str,
) -> None:
    """対象 OS 以外ならスキップし、対象 OS でサンドボックスが使えなければスキップまたは失敗にする。"""
    if sys.platform != expected_platform:
        test_case.skipTest(platform_skip_reason)
    if not backend_available:
        skip_or_fail_if_sandbox_unavailable(test_case, unavailable_reason)


def should_mock_when_sandbox_unavailable(
    test_case: unittest.TestCase,
    verifier: Any,
    reason: str = "OS sandbox backend is unavailable",
) -> bool:
    """サンドボックスが使えない場合にモックへ切り替えてよいか判定する。

    AUTO_DEV_REQUIRE_SANDBOX=1 のときにサンドボックスが使えない場合は fail する。
    """
    if verifier._get_sandbox_backend() is not None:
        return False
    if is_sandbox_required():
        test_case.fail(f"{REQUIRE_SANDBOX_ENV_VAR}=1 forbids falling back to mock: {reason}")
    return True
