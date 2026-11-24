"""Tests for DX-11: scripts/record_dashboard.py (development-only GIF exporter)."""

from __future__ import annotations

import importlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "record_dashboard.py"
SRC_DIR = REPO_ROOT / "src" / "auto_developer"


def _clean_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    return env


def _load_script_module():
    scripts_dir = str(REPO_ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    return importlib.import_module("record_dashboard")


class TestDX11RecordDashboard(unittest.TestCase):
    """Verify DX-11 contract for scripts/record_dashboard.py."""

    def test_script_exists(self) -> None:
        self.assertTrue(SCRIPT_PATH.is_file(), f"Missing script: {SCRIPT_PATH}")

    def test_pyproject_has_no_new_runtime_dependencies(self) -> None:
        data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        deps = data.get("project", {}).get("dependencies", [])
        self.assertEqual(deps, [])

    def test_package_does_not_reference_record_dashboard_or_optional_libs(self) -> None:
        for py_file in SRC_DIR.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            self.assertNotIn("record_dashboard", content, f"Found in {py_file}")
            self.assertNotIn("playwright", content, f"Found in {py_file}")

    def test_subprocess_help_exits_zero(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--help"],
            capture_output=True,
            text=True,
            env=_clean_subprocess_env(),
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("--input", proc.stdout)
        self.assertIn("--output", proc.stdout)

    def test_subprocess_missing_dependency_exits_2_with_install_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            blocker_dir = tmp / "blocker"
            blocker_dir.mkdir()
            (blocker_dir / "playwright.py").write_text(
                "raise ModuleNotFoundError('No module named playwright')\n",
                encoding="utf-8",
            )
            (blocker_dir / "PIL.py").write_text(
                "raise ModuleNotFoundError('No module named PIL')\n",
                encoding="utf-8",
            )
            env = _clean_subprocess_env()
            existing_pp = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = (
                f"{blocker_dir}{os.pathsep}{existing_pp}" if existing_pp else str(blocker_dir)
            )
            proc = subprocess.run(
                [sys.executable, str(SCRIPT_PATH)],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
        self.assertEqual(proc.returncode, 2, f"stdout={proc.stdout!r}, stderr={proc.stderr!r}")
        combined = proc.stdout + proc.stderr
        self.assertIn("pip install", combined)
        self.assertIn("playwright", combined)
        self.assertIn("pillow", combined.lower())

    def test_missing_pillow_only_exits_2_with_install_instructions(self) -> None:
        mod = _load_script_module()

        def fake_import(name: str):
            if name == "playwright.sync_api":
                return SimpleNamespace(sync_playwright=object())
            if name == "PIL.Image":
                raise ModuleNotFoundError("No module named 'PIL'")
            return importlib.import_module(name)

        stderr_buf = io.StringIO()
        with (
            patch.object(mod.importlib, "import_module", side_effect=fake_import),
            redirect_stderr(stderr_buf),
        ):
            rc = mod.main([])

        self.assertEqual(rc, 2)
        err = stderr_buf.getvalue()
        self.assertIn("pip install", err)
        self.assertIn("pillow", err.lower())

    def test_missing_input_html_exits_2(self) -> None:
        mod = _load_script_module()
        fake_pw = SimpleNamespace(sync_playwright=object())
        fake_pil = SimpleNamespace(open=object())

        def fake_import(name: str):
            if name == "playwright.sync_api":
                return fake_pw
            if name == "PIL.Image":
                return fake_pil
            return importlib.import_module(name)

        with tempfile.TemporaryDirectory() as tmpdir:
            missing_html = Path(tmpdir) / "does_not_exist.html"
            out_gif = Path(tmpdir) / "out.gif"
            stderr_buf = io.StringIO()
            with (
                patch.object(mod.importlib, "import_module", side_effect=fake_import),
                redirect_stderr(stderr_buf),
            ):
                rc = mod.main(["--input", str(missing_html), "--output", str(out_gif)])

        self.assertEqual(rc, 2)
        self.assertIn("not found", stderr_buf.getvalue().lower())

    def test_records_frames_and_writes_gif_with_mocked_playwright_and_pil(self) -> None:
        mod = _load_script_module()

        class _FakeLocator:
            def __init__(self, page: _FakePage, selector: str) -> None:
                self._page = page
                self._selector = selector

            def count(self) -> int:
                return 1

            def click(self) -> None:
                if self._selector == "#btn-play":
                    self._page.playing = True

            def inner_text(self) -> str:
                if self._selector == "#btn-play":
                    return "⏸ Pause" if self._page.playing else "▶ Play"
                return ""

        class _FakePage:
            def __init__(self) -> None:
                self.visited_url: str | None = None
                self.playing = False
                self.waits = 0
                self.screenshots = 0

            def goto(self, url: str, wait_until: str = "load") -> None:
                self.visited_url = url

            def locator(self, selector: str) -> _FakeLocator:
                return _FakeLocator(self, selector)

            def wait_for_timeout(self, ms: int) -> None:
                self.waits += 1
                if self.waits >= 2:
                    self.playing = False

            def screenshot(self, type: str = "png") -> bytes:
                self.screenshots += 1
                return b"FAKE_PNG_BYTES"

        fake_page = _FakePage()

        class _FakeBrowser:
            def new_page(self, viewport: dict[str, int] | None = None) -> _FakePage:
                return fake_page

            def close(self) -> None:
                pass

        class _FakePlaywrightCtx:
            def __enter__(self) -> SimpleNamespace:
                return SimpleNamespace(
                    chromium=SimpleNamespace(launch=lambda headless=True: _FakeBrowser())
                )

            def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
                pass

        saved_calls: list[dict[str, object]] = []

        class _FakeImage:
            def convert(self, mode: str) -> _FakeImage:
                return self

            def save(self, fp: object, **kwargs: object) -> None:
                Path(str(fp)).write_bytes(b"GIF89aFAKE")
                saved_calls.append({"fp": str(fp), **kwargs})

        fake_pw = SimpleNamespace(sync_playwright=lambda: _FakePlaywrightCtx())
        fake_pil = SimpleNamespace(open=lambda buf: _FakeImage())

        def fake_import(name: str):
            if name == "playwright.sync_api":
                return fake_pw
            if name == "PIL.Image":
                return fake_pil
            return importlib.import_module(name)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            html_path = tmp / "dashboard.html"
            html_path.write_text("<html><body><button id='btn-play'>▶ Play</button></body></html>", encoding="utf-8")
            gif_path = tmp / "nested" / "out.gif"

            with patch.object(mod.importlib, "import_module", side_effect=fake_import):
                rc = mod.main([
                    "--input",
                    str(html_path),
                    "--output",
                    str(gif_path),
                    "--fps",
                    "10",
                    "--speed",
                    "4",
                ])

            self.assertEqual(rc, 0)
            self.assertTrue(gif_path.is_file())
            self.assertEqual(fake_page.visited_url, html_path.resolve().as_uri())
            self.assertGreaterEqual(fake_page.screenshots, 2)
            self.assertEqual(len(saved_calls), 1)
            self.assertTrue(saved_calls[0]["save_all"])
            self.assertEqual(saved_calls[0]["loop"], 0)
            self.assertEqual(saved_calls[0]["duration"], 100)

    def test_rendered_dashboard_html_matches_expected_playback_controls(self) -> None:
        src_path = str(REPO_ROOT / "src")
        if src_path not in sys.path:
            sys.path.insert(0, src_path)
        from auto_developer.dashboard import render_dashboard_html
        from auto_developer.status import STATUS_SCHEMA

        snapshot = {
            "schema": STATUS_SCHEMA,
            "mode": "single",
            "generated_at": "2026-11-24T00:00:00Z",
            "plan": {"done": 0, "total": 1, "tasks": []},
            "attention": [],
            "timeline": [],
            "timeline_start": None,
            "timeline_end": None,
        }
        html = render_dashboard_html(snapshot)
        self.assertIn('id="btn-play"', html)
        self.assertIn("▶ Play", html)
        self.assertIn("⏸ Pause", html)
        self.assertIn('data-speed="1"', html)
        self.assertIn('data-speed="4"', html)
        self.assertIn('data-speed="60"', html)

    def test_missing_play_button_exits_2(self) -> None:
        mod = _load_script_module()

        class _MissingLocator:
            def count(self) -> int:
                return 0

        class _PageNoPlay:
            def goto(self, url: str, wait_until: str = "load") -> None:
                pass

            def locator(self, selector: str) -> _MissingLocator:
                return _MissingLocator()

        class _BrowserNoPlay:
            def new_page(self, viewport: dict[str, int] | None = None) -> _PageNoPlay:
                return _PageNoPlay()

            def close(self) -> None:
                pass

        class _PwCtx:
            def __enter__(self) -> SimpleNamespace:
                return SimpleNamespace(
                    chromium=SimpleNamespace(launch=lambda headless=True: _BrowserNoPlay())
                )

            def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
                pass

        fake_pw = SimpleNamespace(sync_playwright=lambda: _PwCtx())
        fake_pil = SimpleNamespace(open=lambda buf: None)

        def fake_import(name: str):
            if name == "playwright.sync_api":
                return fake_pw
            if name == "PIL.Image":
                return fake_pil
            return importlib.import_module(name)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            html_path = tmp / "empty.html"
            html_path.write_text("<html><body></body></html>", encoding="utf-8")
            gif_path = tmp / "out.gif"
            stderr_buf = io.StringIO()
            with (
                patch.object(mod.importlib, "import_module", side_effect=fake_import),
                redirect_stderr(stderr_buf),
            ):
                rc = mod.main(["--input", str(html_path), "--output", str(gif_path)])

        self.assertEqual(rc, 2)
        self.assertIn("#btn-play", stderr_buf.getvalue())

    def test_chromium_launch_failure_exits_2_with_install_hint(self) -> None:
        mod = _load_script_module()

        def fail_launch(headless: bool = True) -> None:
            raise RuntimeError("Executable doesn't exist")

        class _PwCtx:
            def __enter__(self) -> SimpleNamespace:
                return SimpleNamespace(chromium=SimpleNamespace(launch=fail_launch))

            def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
                pass

        fake_pw = SimpleNamespace(sync_playwright=lambda: _PwCtx())
        fake_pil = SimpleNamespace(open=lambda buf: None)

        def fake_import(name: str):
            if name == "playwright.sync_api":
                return fake_pw
            if name == "PIL.Image":
                return fake_pil
            return importlib.import_module(name)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            html_path = tmp / "dashboard.html"
            html_path.write_text("<html></html>", encoding="utf-8")
            gif_path = tmp / "out.gif"
            stderr_buf = io.StringIO()
            with (
                patch.object(mod.importlib, "import_module", side_effect=fake_import),
                redirect_stderr(stderr_buf),
            ):
                rc = mod.main(["--input", str(html_path), "--output", str(gif_path)])

        self.assertEqual(rc, 2)
        self.assertIn("playwright install chromium", stderr_buf.getvalue())

    def test_invalid_numeric_arguments_exit_2(self) -> None:
        mod = _load_script_module()
        fake_pw = SimpleNamespace(sync_playwright=object())
        fake_pil = SimpleNamespace(open=object())

        def fake_import(name: str):
            if name == "playwright.sync_api":
                return fake_pw
            if name == "PIL.Image":
                return fake_pil
            return importlib.import_module(name)

        for bad_args in (
            ["--fps", "0"],
            ["--width", "0"],
            ["--height", "-1"],
            ["--max-seconds", "0"],
        ):
            with self.subTest(bad_args=bad_args):
                stderr_buf = io.StringIO()
                with (
                    patch.object(mod.importlib, "import_module", side_effect=fake_import),
                    redirect_stderr(stderr_buf),
                ):
                    rc = mod.main(bad_args)
                self.assertEqual(rc, 2)
                self.assertIn("must be positive", stderr_buf.getvalue())

    def test_max_seconds_stops_recording_and_writes_gif(self) -> None:
        mod = _load_script_module()

        class _NeverEndingLocator:
            def count(self) -> int:
                return 1

            def click(self) -> None:
                pass

            def inner_text(self) -> str:
                return "⏸ Pause"

        class _NeverEndingPage:
            def __init__(self) -> None:
                self.screenshots = 0

            def goto(self, url: str, wait_until: str = "load") -> None:
                pass

            def locator(self, selector: str) -> _NeverEndingLocator:
                return _NeverEndingLocator()

            def wait_for_timeout(self, ms: int) -> None:
                pass

            def screenshot(self, type: str = "png") -> bytes:
                self.screenshots += 1
                return b"FAKE_PNG_BYTES"

        class _Browser:
            def new_page(self, viewport: dict[str, int] | None = None) -> _NeverEndingPage:
                return _NeverEndingPage()

            def close(self) -> None:
                pass

        class _PwCtx:
            def __enter__(self) -> SimpleNamespace:
                return SimpleNamespace(
                    chromium=SimpleNamespace(launch=lambda headless=True: _Browser())
                )

            def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
                pass

        class _FakeImage:
            def convert(self, mode: str) -> _FakeImage:
                return self

            def save(self, fp: object, **kwargs: object) -> None:
                Path(str(fp)).write_bytes(b"GIF89aFAKE")

        fake_pw = SimpleNamespace(sync_playwright=lambda: _PwCtx())
        fake_pil = SimpleNamespace(open=lambda buf: _FakeImage())

        def fake_import(name: str):
            if name == "playwright.sync_api":
                return fake_pw
            if name == "PIL.Image":
                return fake_pil
            return importlib.import_module(name)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            html_path = tmp / "dashboard.html"
            html_path.write_text("<html></html>", encoding="utf-8")
            gif_path = tmp / "out.gif"

            with patch.object(mod.importlib, "import_module", side_effect=fake_import):
                rc = mod.main([
                    "--input",
                    str(html_path),
                    "--output",
                    str(gif_path),
                    "--max-seconds",
                    "0.05",
                    "--fps",
                    "20",
                ])

            self.assertEqual(rc, 0)
            self.assertTrue(gif_path.is_file())


if __name__ == "__main__":
    unittest.main()
