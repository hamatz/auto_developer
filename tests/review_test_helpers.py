from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from auto_developer.evidence import EvidenceStore

SRC_DIR = str(Path(__file__).resolve().parent.parent / "src")


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{SRC_DIR}:{existing_pp}" if existing_pp else SRC_DIR
    return env


def record_test_review(
    repo_dir: Path,
    task_id: str,
    *,
    maker_model: str = "gemini-3.8-flash",
    checker_model: str = "claude-opus-5-5-medium",
    maker_family: str | None = None,
    checker_family: str | None = None,
    decision: str = "APPROVE",
    reason: str = "Approved in test",
    plan_path: Path | None = None,
    spec_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """GATE-6 テスト用ヘルパー: review-input -> checker output -> record-review を実行する。"""
    io_dir = EvidenceStore(repo_dir=repo_dir).evidence_dir / "test_review_io" / f"task_{task_id}"
    io_dir.mkdir(parents=True, exist_ok=True)

    base_args = [sys.executable, "-m", "auto_developer.cli", "--work-dir", str(repo_dir)]
    if plan_path is not None:
        base_args.extend(["--plan", str(plan_path)])

    effective_spec = spec_path or (repo_dir / "SPEC.md")
    if not effective_spec.is_file():
        fallback_spec = io_dir / "fallback_spec.md"
        fallback_spec.write_text("# SPEC\n", encoding="utf-8")
        effective_spec = fallback_spec
    base_args.extend(["--spec", str(effective_spec)])

    ri = subprocess.run(
        [*base_args, "review-input", "--task-id", str(task_id), "--out-dir", str(io_dir)],
        cwd=repo_dir,
        env=_clean_env(),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if ri.returncode != 0:
        return ri

    spec_excerpt = io_dir / "spec_excerpt.md"
    spec_excerpt.write_text(effective_spec.read_text(encoding="utf-8"), encoding="utf-8")

    out_file = io_dir / "checker_output.txt"
    out_file.write_text(f"{reason}\nDECISION: {decision}\n", encoding="utf-8")

    rr_args = [
        *base_args,
        "record-review",
        "--task-id",
        str(task_id),
        "--checker-output",
        str(out_file),
        "--diff-file",
        str(io_dir / "diff.patch"),
        "--spec-file",
        str(spec_excerpt),
        "--maker-model",
        maker_model,
        "--checker-model",
        checker_model,
        "--reason",
        reason,
    ]
    if maker_family is not None:
        rr_args.extend(["--maker-family", maker_family])
    if checker_family is not None:
        rr_args.extend(["--checker-family", checker_family])

    return subprocess.run(
        rr_args,
        cwd=repo_dir,
        env=_clean_env(),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def ensure_synthetic_review_evidence(
    repo_dir: Path,
    task_id: str,
    *,
    diff_text: str = "diff --git a/app.py b/app.py\n+pass\n",
    spec_text: str = "# SPEC\n",
    checker_output: str = "Synthetic review\nDECISION: APPROVE\n",
) -> dict[str, Any]:
    """BDL-4 テスト用ヘルパー: 製品コードを使わずに査読の3ファイルと checker.review を用意する。"""
    git_path = repo_dir / ".git"
    if git_path.is_dir():
        ev_dir = git_path / "nightly_evidence"
        candidate_recs = [
            ev_dir / "records" / f"task_{task_id}.json",
            ev_dir / "archive" / f"task_{task_id}.json",
            repo_dir / ".dev" / "evidence" / f"task_{task_id}.json",
        ]
    else:
        ev_dir = repo_dir / ".dev" / "evidence"
        candidate_recs = [ev_dir / f"task_{task_id}.json"]

    rev_dir = ev_dir / "reviews" / f"task_{task_id}"
    rev_dir.mkdir(parents=True, exist_ok=True)
    out_file = rev_dir / "checker_output.txt"
    diff_file = rev_dir / "diff.patch"
    spec_file = rev_dir / "spec_excerpt.md"
    out_file.write_text(checker_output, encoding="utf-8")
    diff_file.write_text(diff_text, encoding="utf-8")
    spec_file.write_text(spec_text, encoding="utf-8")

    review_meta: dict[str, Any] = {
        "output_sha256": hashlib.sha256(out_file.read_bytes()).hexdigest(),
        "diff_sha256": hashlib.sha256(diff_file.read_bytes()).hexdigest(),
        "spec_sha256": hashlib.sha256(spec_file.read_bytes()).hexdigest(),
        "recorded_at": time.time(),
        "decision_source": "checker_output",
    }
    for rec_path in candidate_recs:
        if rec_path.is_file():
            data = json.loads(rec_path.read_text(encoding="utf-8"))
            if isinstance(data.get("checker"), dict):
                data["checker"]["review"] = review_meta
                rec_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return review_meta

