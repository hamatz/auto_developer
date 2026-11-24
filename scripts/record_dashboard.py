#!/usr/bin/env python3
"""Record the auto-dev HTML dashboard playback as an animated GIF (dev-only).

Usage:
    python scripts/record_dashboard.py --input .dev/dashboard.html --output dashboard.gif

Requires optional development dependencies (not included in auto-developer):
    pip install playwright pillow
    playwright install chromium
"""

from __future__ import annotations

import argparse
import importlib
import io
import sys
import time
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser for dashboard GIF recording."""
    parser = argparse.ArgumentParser(
        description="Replay an auto-dev dashboard HTML file and export an animated GIF."
    )
    parser.add_argument(
        "--input",
        "-i",
        type=Path,
        default=Path(".dev/dashboard.html"),
        help="Path to the dashboard HTML file (default: .dev/dashboard.html).",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("dashboard.gif"),
        help="Path to write the output GIF file (default: dashboard.gif).",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=1280,
        help="Browser viewport width in pixels (default: 1280).",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=800,
        help="Browser viewport height in pixels (default: 800).",
    )
    parser.add_argument(
        "--speed",
        type=int,
        default=4,
        help="Playback speed multiplier button to select if present (default: 4).",
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=10,
        help="Capture frame rate in frames per second (default: 10).",
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=30.0,
        help="Maximum recording duration in seconds (default: 30.0).",
    )
    return parser


def _load_dependencies() -> tuple[Any, Any, list[str]]:
    """Import optional playwright and Pillow modules without static dependencies."""
    missing: list[str] = []
    playwright_sync_api: Any = None
    pil_image: Any = None

    try:
        playwright_sync_api = importlib.import_module("playwright.sync_api")
    except ModuleNotFoundError:
        missing.append("playwright")

    try:
        pil_image = importlib.import_module("PIL.Image")
    except ModuleNotFoundError:
        missing.append("pillow")

    return playwright_sync_api, pil_image, missing


def _print_install_instructions(missing: list[str]) -> None:
    """Print installation instructions for missing optional libraries."""
    names = ", ".join(missing)
    print(
        f"Missing required libraries for dashboard recording: {names}\n"
        "Install them with:\n"
        "  pip install playwright pillow && playwright install chromium",
        file=sys.stderr,
    )


class RecordDashboardError(RuntimeError):
    """Raised when dashboard recording cannot proceed (missing control or browser)."""


def _capture_png_frame(page: Any, pil_image: Any) -> Any:
    """Capture a viewport screenshot and decode it via PIL.Image."""
    png_bytes: bytes = page.screenshot(type="png")
    return pil_image.open(io.BytesIO(png_bytes)).convert("RGB")


def record_dashboard(
    input_path: Path,
    output_path: Path,
    *,
    width: int = 1280,
    height: int = 800,
    speed: int = 4,
    fps: int = 10,
    max_seconds: float = 30.0,
    playwright_sync_api: Any,
    pil_image: Any,
) -> int:
    """Replay the dashboard HTML in headless Chromium and save frames as a GIF."""
    interval_ms = max(1, round(1000.0 / fps))
    frames: list[Any] = []

    with playwright_sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as exc:
            raise RecordDashboardError(
                f"Error: failed to launch Chromium browser ({exc}).\n"
                "If Chromium is not installed, run:\n"
                "  playwright install chromium"
            ) from exc

        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.goto(input_path.resolve().as_uri(), wait_until="load")

            play_btn = page.locator("#btn-play")
            if play_btn.count() == 0:
                raise RecordDashboardError(
                    "Error: playback control '#btn-play' not found in dashboard HTML."
                )

            speed_btn = page.locator(f'[data-speed="{speed}"]')
            if speed_btn.count() > 0:
                speed_btn.click()

            frames.append(_capture_png_frame(page, pil_image))

            play_btn.click()
            deadline = time.monotonic() + max_seconds
            while time.monotonic() < deadline:
                page.wait_for_timeout(interval_ms)
                frames.append(_capture_png_frame(page, pil_image))
                label: str = play_btn.inner_text()
                if "Play" in label:
                    break
        finally:
            browser.close()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        output_path,
        save_all=True,
        append_images=frames[1:],
        duration=interval_ms,
        loop=0,
        optimize=True,
    )
    return len(frames)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for recording the dashboard HTML into a GIF."""
    parser = build_parser()
    args = parser.parse_args(argv)

    playwright_sync_api, pil_image, missing = _load_dependencies()
    if missing:
        _print_install_instructions(missing)
        return 2

    if args.fps <= 0 or args.width <= 0 or args.height <= 0 or args.max_seconds <= 0:
        print("Error: --width, --height, --fps, and --max-seconds must be positive.", file=sys.stderr)
        return 2

    input_path: Path = args.input
    if not input_path.is_file():
        print(f"Error: input HTML file not found: {input_path}", file=sys.stderr)
        return 2

    output_path: Path = args.output
    try:
        frame_count = record_dashboard(
            input_path,
            output_path,
            width=args.width,
            height=args.height,
            speed=args.speed,
            fps=args.fps,
            max_seconds=args.max_seconds,
            playwright_sync_api=playwright_sync_api,
            pil_image=pil_image,
        )
    except RecordDashboardError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"Saved {frame_count} frame(s) to {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
