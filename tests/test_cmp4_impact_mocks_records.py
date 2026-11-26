"""CMP-4 (Phase 77 Step 3): compile --impact <IR> (I-2 mock_tests & I-3 record_tests)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    return env


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    snap: dict[str, bytes] = {}
    for p in sorted(root.rglob("*")):
        if ".git" in p.parts:
            continue
        if p.is_file() and not p.is_symlink():
            snap[p.relative_to(root).as_posix()] = p.read_bytes()
    return snap


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=root,
        check=True,
        capture_output=True,
    )


def _git_add_all(root: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)


def _make_valid_ir(root: Path, requirements: list[dict[str, Any]]) -> Path:
    doc_rel = "docs/designs/feature.md"
    doc_path = root / doc_rel
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_bytes = "".join(f"Line {i}: design spec\n" for i in range(1, 25)).encode("utf-8")
    doc_path.write_bytes(doc_bytes)
    lines = doc_bytes.splitlines(keepends=True)

    def _range_sha(start: int, end: int) -> str:
        return hashlib.sha256(b"".join(lines[start - 1 : end])).hexdigest()

    for idx, req in enumerate(requirements):
        if "source" not in req:
            s = min(1 + idx * 2, 20)
            req["source"] = {"path": doc_rel, "lines": [s, s + 1], "sha256": _range_sha(s, s + 1)}
        req.setdefault("kind", "modify")
        req.setdefault("acceptance", ["AC-1"])

    ir = {
        "schema": "auto-dev-ir/1",
        "source": [{"path": doc_rel, "sha256": hashlib.sha256(doc_bytes).hexdigest()}],
        "purpose": {
            "statement": "Test I-2 and I-3 impact analysis.",
            "source": {"path": doc_rel, "lines": [1, 2], "sha256": _range_sha(1, 2)},
        },
        "requirements": requirements,
        "acceptance": [
            {
                "id": "AC-1",
                "given": "A repo with mock tests and record-synthesizing tests",
                "when": "compile --impact runs",
                "then": "I-2 and I-3 candidates are listed",
                "observable": ["exit 0"],
                "source": {"path": doc_rel, "lines": [3, 4], "sha256": _range_sha(3, 4)},
            }
        ],
        "steps": [
            {
                "n": 1,
                "title": "Do work",
                "requirements": [requirements[0]["id"]],
                "files": ["src/app.py"],
            }
        ],
        "verification": {
            "commands": ["python3 -m unittest"],
            "source": {"path": doc_rel, "lines": [5, 6], "sha256": _range_sha(5, 6)},
        },
    }
    ir_path = root / "feature.ir.json"
    ir_path.write_text(json.dumps(ir, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return ir_path


class TestCmp4ImpactMocksAndRecords(unittest.TestCase):
    def _run_cli(self, args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        env = _clean_env()
        src_path = str(_REPO_ROOT / "src")
        env["PYTHONPATH"] = f"{src_path}{os.pathsep}{env['PYTHONPATH']}" if "PYTHONPATH" in env else src_path
        return subprocess.run(
            [sys.executable, "-m", "auto_developer.cli", *args],
            cwd=cwd or _REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_i2_mock_tests_and_call_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_git_repo(root)

            (root / "src").mkdir()
            (root / "src" / "worker.py").write_text(
                "def run_job(x):\n    return x\n\nclass JobRunner:\n    def execute(self):\n        pass\n",
                encoding="utf-8",
            )
            (root / "tests").mkdir()
            (root / "tests" / "test_worker_mocks.py").write_text(
                "import unittest\n"
                "from unittest.mock import MagicMock, Mock, patch\n"
                "from src.worker import JobRunner, run_job\n"
                "\n"
                "class TestWorkerMocks(unittest.TestCase):\n"
                "    @patch('src.worker.run_job')\n"
                "    def test_patch_decorator(self, mock_run):\n"
                "        mock_run.return_value = 42\n"
                "        self.assertEqual(mock_run.call_count, 0)\n"
                "\n"
                "    def test_patch_object_and_assert_called(self):\n"
                "        with patch.object(JobRunner, 'execute') as m:\n"
                "            JobRunner().execute()\n"
                "            m.assert_called_once()\n"
                "\n"
                "    def test_magic_mock_unrelated_symbol(self):\n"
                "        m = MagicMock()\n"
                "        m.other_fn()\n"
                "        m.assert_called()\n"
                "\n"
                "    def test_no_mock_uses_run_job(self):\n"
                "        self.assertEqual(run_job(1), 1)\n"
                "\n"
                "def test_top_level_mock():\n"
                "    m = Mock()\n"
                "    run_job(m)\n"
                "    assert m.call_count == 0\n",
                encoding="utf-8",
            )
            _git_add_all(root)

            ir_path = _make_valid_ir(
                root,
                [
                    {
                        "id": "REQ-1",
                        "statement": "Change run_job and JobRunner.",
                        "fingerprints": ["run_job", "JobRunner"],
                    }
                ],
            )
            before = _snapshot_tree(root)

            proc = self._run_cli(
                ["--work-dir", str(root), "compile", "--impact", str(ir_path), "--json"]
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(before, _snapshot_tree(root))

            payload = json.loads(proc.stdout)
            req = payload["requirements"][0]
            mock_tests = req["mock_tests"]
            funcs = [m["function"] for m in mock_tests]
            self.assertEqual(
                funcs,
                [
                    "TestWorkerMocks.test_patch_decorator",
                    "TestWorkerMocks.test_patch_object_and_assert_called",
                    "test_top_level_mock",
                ],
            )
            # Unrelated mock test and non-mock test must NOT be in mock_tests
            self.assertNotIn("TestWorkerMocks.test_magic_mock_unrelated_symbol", funcs)
            self.assertNotIn("TestWorkerMocks.test_no_mock_uses_run_job", funcs)

            first = mock_tests[0]
            self.assertEqual(first["path"], "tests/test_worker_mocks.py")
            self.assertEqual(first["fingerprints"], ["run_job"])
            self.assertIn("patch", first["signals"])
            self.assertIn("call_count", first["signals"])

            second = mock_tests[1]
            self.assertIn("patch.object", second["signals"])
            self.assertIn("assert_called_once", second["signals"])

            # Plain text output includes mock_tests
            proc_txt = self._run_cli(
                ["--work-dir", str(root), "compile", "--impact", str(ir_path)]
            )
            self.assertEqual(proc_txt.returncode, 0, proc_txt.stdout + proc_txt.stderr)
            self.assertIn("mock_tests:", proc_txt.stdout)
            self.assertIn("TestWorkerMocks.test_patch_decorator", proc_txt.stdout)

    def test_i3_record_synthesizing_tests_and_non_writers_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_git_repo(root)

            (root / "tests").mkdir()
            (root / "tests" / "test_synth_write_text.py").write_text(
                "def test_events(tmp_path):\n"
                "    (tmp_path / 'events.jsonl').write_text('{}', encoding='utf-8')\n",
                encoding="utf-8",
            )
            (root / "tests" / "test_synth_json_dump.py").write_text(
                "import json\n"
                "def test_job_state(tmp_path):\n"
                "    p = tmp_path / 'job_state.json'\n"
                "    with open(p, 'w', encoding='utf-8') as f:\n"
                "        json.dump({'ok': True}, f)\n",
                encoding="utf-8",
            )
            (root / "tests" / "test_synth_path_open_bytes.py").write_text(
                "def test_bundle(tmp_path):\n"
                "    (tmp_path / 'bundle_manifest.json').write_bytes(b'{}')\n"
                "    with (tmp_path / 'audit_stage.json').open('w') as f:\n"
                "        f.write('{}')\n"
                "    p = tmp_path / 'nightly_evidence' / 'run.json'\n"
                "    p.write_text('x', encoding='utf-8')\n",
                encoding="utf-8",
            )
            # Read-only test referencing events.jsonl must NOT be flagged
            (root / "tests" / "test_read_only_events.py").write_text(
                "import json\n"
                "def test_read(tmp_path):\n"
                "    text = (tmp_path / 'events.jsonl').read_text(encoding='utf-8')\n"
                "    with open(tmp_path / 'job_state.json', 'r', encoding='utf-8') as f:\n"
                "        json.load(f)\n"
                "    return text\n",
                encoding="utf-8",
            )
            # Non-test file writing events.jsonl must NOT be in record_tests
            (root / "src").mkdir()
            (root / "src" / "logger.py").write_text(
                "def write_event(p):\n    p.joinpath('events.jsonl').write_text('{}')\n",
                encoding="utf-8",
            )
            _git_add_all(root)

            # Even with no fingerprints, record_tests is reported once at top level
            ir_path = _make_valid_ir(
                root,
                [{"id": "REC-1", "statement": "No fingerprints here."}],
            )
            before = _snapshot_tree(root)

            proc = self._run_cli(
                ["--work-dir", str(root), "compile", "--impact", str(ir_path), "--json"]
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(before, _snapshot_tree(root))

            payload = json.loads(proc.stdout)
            rec_tests = payload["record_tests"]
            rec_paths = [r["path"] for r in rec_tests]
            self.assertEqual(
                rec_paths,
                [
                    "tests/test_synth_json_dump.py",
                    "tests/test_synth_path_open_bytes.py",
                    "tests/test_synth_write_text.py",
                ],
            )
            self.assertNotIn("tests/test_read_only_events.py", rec_paths)
            self.assertNotIn("src/logger.py", rec_paths)

            by_path = {r["path"]: r for r in rec_tests}
            self.assertEqual(by_path["tests/test_synth_write_text.py"]["markers"], ["events.jsonl"])
            self.assertIn("write_text", by_path["tests/test_synth_write_text.py"]["writers"])
            self.assertIn("json.dump", by_path["tests/test_synth_json_dump.py"]["writers"])
            self.assertIn("open", by_path["tests/test_synth_json_dump.py"]["writers"])
            self.assertEqual(
                by_path["tests/test_synth_path_open_bytes.py"]["markers"],
                ["audit_stage.json", "bundle_manifest.json", "nightly_evidence"],
            )

            # Plain text output includes record_tests
            proc_txt = self._run_cli(
                ["--work-dir", str(root), "compile", "--impact", str(ir_path)]
            )
            self.assertEqual(proc_txt.returncode, 0, proc_txt.stdout + proc_txt.stderr)
            self.assertIn("record_tests:", proc_txt.stdout)
            self.assertIn("tests/test_synth_write_text.py", proc_txt.stdout)


if __name__ == "__main__":
    unittest.main()
