from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEMO_MAKER_MODEL = "auto-dev-demo-maker"
DEMO_MAKER_FAMILY = "demo-maker"
DEMO_CHECKER_MODEL = "auto-dev-demo-checker"
DEMO_CHECKER_FAMILY = "demo-checker"

_DEMO_PLAN_MD = """\
# PLAN.md

## 現在の目標
- Auto Developer デモ用の計算モジュール実装と検証

## ステップ
- [ ] 1. 計算モジュール (src/demo_app/calculator.py) の実装
- [ ] 2. 単体テスト (tests/test_calculator.py) の追加と検証
"""

_DEMO_MAKER_SCRIPT = """\
from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    task_id = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("AUTO_DEV_TASK_ID", "")
    repo = Path.cwd()
    if task_id == "1":
        pkg = repo / "src" / "demo_app"
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "__init__.py").write_text('\"\"\"Demo calculator package.\"\"\"\\n', encoding="utf-8")
        (pkg / "calculator.py").write_text(
            "from __future__ import annotations\\n\\n"
            "def add(a: int, b: int) -> int:\\n"
            "    return a + b\\n\\n"
            "def multiply(a: int, b: int) -> int:\\n"
            "    return a * b\\n",
            encoding="utf-8",
        )
        print("Demo Maker: implemented src/demo_app/calculator.py")
        return 0
    if task_id == "2":
        tests_dir = repo / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        (tests_dir / "test_calculator.py").write_text(
            "import unittest\\n"
            "from demo_app.calculator import add, multiply\\n\\n"
            "class TestCalculator(unittest.TestCase):\\n"
            "    def test_add_and_multiply(self) -> None:\\n"
            "        self.assertEqual(add(2, 3), 5)\\n"
            "        self.assertEqual(multiply(3, 4), 12)\\n",
            encoding="utf-8",
        )
        print("Demo Maker: added tests/test_calculator.py")
        return 0
    if task_id == "3":
        spec_path = repo / "SPEC.md"
        existing = spec_path.read_text(encoding="utf-8")
        spec_path.write_text(
            existing + "\\n<!-- Unauthorized edit attempted by demo Maker -->\\n",
            encoding="utf-8",
        )
        print("Demo Maker: attempted unauthorized edit to SPEC.md")
        return 0
    print(f"Demo Maker: unexpected task_id={task_id}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
"""

_DEMO_CHECKER_SCRIPT = """\
from __future__ import annotations

import json
import sys


def main() -> int:
    payload = sys.stdin.read()
    diff_section = payload.split("--- SPEC ---", 1)[0]
    if not diff_section.strip():
        print(json.dumps({"decision": "REJECT", "findings": ["Empty diff"]}))
        return 0
    print(json.dumps({"decision": "APPROVE", "findings": []}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""

_DEMO_TEAMS_JSON = """\
{
  "version": 1,
  "project": "demo-two-teams",
  "teams": [
    {
      "id": "core",
      "owns": [
        "src/demo_core/**",
        "tests/test_core.py",
        "plans/core.md"
      ],
      "plan": "plans/core.md",
      "depends_on": []
    },
    {
      "id": "cli",
      "owns": [
        "src/demo_cli/**",
        "tests/test_cli.py",
        "plans/cli.md"
      ],
      "plan": "plans/cli.md",
      "depends_on": []
    }
  ]
}
"""

_DEMO_CORE_PLAN_MD = """\
# Core Team Plan

- [ ] 1. [core] Implement arithmetic helpers (src/demo_core/math_ops.py)
- [ ] 2. [core] Add unit tests for arithmetic helpers (tests/test_core.py)
"""

_DEMO_CLI_PLAN_MD = """\
# CLI Team Plan

- [ ] 1. [cli] Implement output formatter (src/demo_cli/formatter.py)
- [ ] 2. [cli] Add unit tests for output formatter (tests/test_cli.py)
"""

_DEMO_TEAMS_MAKER_SCRIPT = """\
from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    task_id = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("AUTO_DEV_TASK_ID", "")
    task_title = os.environ.get("AUTO_DEV_TASK_TITLE", "")
    attempt = os.environ.get("AUTO_DEV_ATTEMPT", "1")
    repo = Path.cwd()

    if "[core]" in task_title:
        if task_id == "1":
            pkg = repo / "src" / "demo_core"
            pkg.mkdir(parents=True, exist_ok=True)
            (pkg / "__init__.py").write_text('\"\"\"Demo core package.\"\"\"\\n', encoding="utf-8")
            (pkg / "math_ops.py").write_text(
                "from __future__ import annotations\\n\\n"
                "def add(a: int, b: int) -> int:\\n"
                "    return a + b\\n\\n"
                "def sub(a: int, b: int) -> int:\\n"
                "    return a - b\\n",
                encoding="utf-8",
            )
            print("Demo Maker [core]: implemented src/demo_core/math_ops.py")
            return 0
        if task_id == "2":
            tests_dir = repo / "tests"
            tests_dir.mkdir(parents=True, exist_ok=True)
            if attempt == "1":
                (tests_dir / "test_core.py").write_text(
                    "import unittest\\n"
                    "from demo_core.math_ops import add, sub\\n\\n"
                    "class TestCore(unittest.TestCase):\\n"
                    "    def test_add_and_sub(self) -> None:\\n"
                    "        self.assertEqual(add(2, 3), 999)  # deliberate 1st-attempt failure\\n"
                    "        self.assertEqual(sub(5, 2), 3)\\n",
                    encoding="utf-8",
                )
                print("Demo Maker [core]: wrote failing test_core.py on attempt 1")
                return 0
            (tests_dir / "test_core.py").write_text(
                "import unittest\\n"
                "from demo_core.math_ops import add, sub\\n\\n"
                "class TestCore(unittest.TestCase):\\n"
                "    def test_add_and_sub(self) -> None:\\n"
                "        self.assertEqual(add(2, 3), 5)\\n"
                "        self.assertEqual(sub(5, 2), 3)\\n",
                encoding="utf-8",
            )
            print(f"Demo Maker [core]: fixed test_core.py on attempt {attempt}")
            return 0

    if "[cli]" in task_title:
        if task_id == "1":
            pkg = repo / "src" / "demo_cli"
            pkg.mkdir(parents=True, exist_ok=True)
            (pkg / "__init__.py").write_text('\"\"\"Demo CLI package.\"\"\"\\n', encoding="utf-8")
            (pkg / "formatter.py").write_text(
                "from __future__ import annotations\\n\\n"
                "def format_result(label: str, value: int) -> str:\\n"
                "    return f'{label}: {value}'\\n",
                encoding="utf-8",
            )
            print("Demo Maker [cli]: implemented src/demo_cli/formatter.py")
            return 0
        if task_id == "2":
            tests_dir = repo / "tests"
            tests_dir.mkdir(parents=True, exist_ok=True)
            (tests_dir / "test_cli.py").write_text(
                "import unittest\\n"
                "from demo_cli.formatter import format_result\\n\\n"
                "class TestCli(unittest.TestCase):\\n"
                "    def test_format_result(self) -> None:\\n"
                "        self.assertEqual(format_result('sum', 5), 'sum: 5')\\n",
                encoding="utf-8",
            )
            print("Demo Maker [cli]: added tests/test_cli.py")
            return 0

    print(f"Demo Maker: unexpected task_id={task_id} title={task_title!r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
"""


def _demo_subprocess_env() -> dict[str, str]:
    """サブプロセスで auto_developer CLI と検証スクリプトを実行するための環境変数を組み立てる。"""
    env = os.environ.copy()
    pkg_parent = str(Path(__file__).resolve().parents[1])
    existing_pp = env.get("PYTHONPATH", "")
    pp_parts = [p for p in existing_pp.split(os.pathsep) if p] if existing_pp else []
    if pkg_parent not in pp_parts:
        pp_parts.insert(0, pkg_parent)
    env["PYTHONPATH"] = os.pathsep.join(pp_parts)
    env.setdefault("PYTHON", sys.executable)
    return env


def _run_demo_cli(repo_dir: Path, *args: str) -> tuple[int, str, str]:
    """デモ用一時リポジトリに対して `python -m auto_developer.cli` を実行する。"""
    proc = subprocess.run(
        [sys.executable, "-m", "auto_developer.cli", "--work-dir", str(repo_dir), *args],
        cwd=repo_dir,
        env=_demo_subprocess_env(),
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _run_git(repo_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """デモ用一時リポジトリで Git コマンドを実行する。"""
    return subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=True,
    )


def _ensure_cli_ok(label: str, rc: int, stdout: str, stderr: str) -> None:
    if rc != 0:
        raise RuntimeError(f"{label} failed with exit {rc}:\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}")


def _run_demo_teams_flow(repo_dir: Path) -> None:
    """2チーム並行実行・検証失敗リトライ・統合RCタグ・ダッシュボード生成のデモを実行する (DX-10)。"""
    with tempfile.TemporaryDirectory(prefix="auto-dev-demo-teams-agents-") as agents_tmp:
        agents_dir = Path(agents_tmp)
        maker_script = agents_dir / "demo_teams_maker.py"
        checker_script = agents_dir / "demo_checker.py"
        maker_script.write_text(_DEMO_TEAMS_MAKER_SCRIPT, encoding="utf-8")
        checker_script.write_text(_DEMO_CHECKER_SCRIPT, encoding="utf-8")

        py_quoted = shlex.quote(sys.executable)
        maker_cmd = f"{py_quoted} {shlex.quote(str(maker_script))} {{task_id}}"
        checker_cmd = f"{py_quoted} {shlex.quote(str(checker_script))} {{task_id}}"

        # 1. 一時 Git リポジトリ作成 + auto-dev init + 2チーム構成
        print("\n[1/4] Initializing temporary Git repository with 2 teams (core, cli)...")
        _run_git(repo_dir, "init", "-b", "main")
        _run_git(repo_dir, "config", "user.name", "AutoDev Demo")
        _run_git(repo_dir, "config", "user.email", "demo@example.com")

        rc, out, err = _run_demo_cli(repo_dir, "init")
        _ensure_cli_ok("auto-dev init", rc, out, err)

        (repo_dir / "teams.json").write_text(_DEMO_TEAMS_JSON, encoding="utf-8")
        plans_dir = repo_dir / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        (plans_dir / "core.md").write_text(_DEMO_CORE_PLAN_MD, encoding="utf-8")
        (plans_dir / "cli.md").write_text(_DEMO_CLI_PLAN_MD, encoding="utf-8")

        _run_git(repo_dir, "add", "-A")
        _run_git(repo_dir, "commit", "-m", "chore: initialize 2-team demo project")
        print("  -> Initialized teams.json (core, cli) with 2 tasks per team.")

        # 2. 2チーム並行実行 + 統合 (RC タグ生成)
        print(
            "\n[2/4] Running 2 teams concurrently via `auto-dev teams run --dashboard` "
            f"(Maker={DEMO_MAKER_MODEL}/{DEMO_MAKER_FAMILY}, "
            f"Checker={DEMO_CHECKER_MODEL}/{DEMO_CHECKER_FAMILY})..."
        )
        rc, out, err = _run_demo_cli(
            repo_dir,
            "teams",
            "run",
            "--maker-cmd",
            maker_cmd,
            "--checker-cmd",
            checker_cmd,
            "--maker-model",
            DEMO_MAKER_MODEL,
            "--maker-family",
            DEMO_MAKER_FAMILY,
            "--checker-model",
            DEMO_CHECKER_MODEL,
            "--checker-family",
            DEMO_CHECKER_FAMILY,
            "--dashboard",
            "--json",
        )
        _ensure_cli_ok("auto-dev teams run", rc, out, err)

        try:
            run_data = json.loads(out)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON from teams run --json: {exc}\n{out}") from exc

        rc_tags = [
            t.strip()
            for t in _run_git(repo_dir, "tag", "--list", "rc/*").stdout.splitlines()
            if t.strip()
        ]
        if not rc_tags:
            raise RuntimeError("No RC tag was created by `auto-dev teams run`.")
        rc_tag = str(run_data.get("rc_tag") or rc_tags[-1])
        print(f"  -> Teams completed and integrated into RC tag: {rc_tag}")

        # 3. ダッシュボード生成と検証
        # teams run --dashboard はイベントごとに更新するが、統合後の最終状態 (RC タグを含む) を
        # 確実に書き出して存在を検証するため、明示的に auto-dev dashboard を実行する。
        print("\n[3/4] Generating replayable HTML dashboard...")
        rc_dash, out_dash, err_dash = _run_demo_cli(repo_dir, "dashboard")
        _ensure_cli_ok("auto-dev dashboard", rc_dash, out_dash, err_dash)

        dashboard_path = repo_dir / ".dev" / "dashboard.html"
        if not dashboard_path.is_file() or dashboard_path.stat().st_size == 0:
            raise RuntimeError(f"Dashboard HTML was not written at {dashboard_path}")

        # 4. 結果表示
        print("\n[4/4] 2-Team Demo summary:")
        print(f"RC tag: {rc_tag}")
        print(f"Dashboard: {dashboard_path}")
        print("\n=== 2-Team Demo completed successfully! ===")


def run_demo(*, keep: bool = False, teams: bool = False) -> int:
    """一時ディレクトリで Auto Developer の一連の流れを実演する (DX-6 / DX-10)。"""
    prefix = "auto-dev-demo-teams-" if teams else "auto-dev-demo-"
    repo_dir = Path(tempfile.mkdtemp(prefix=prefix))
    header = "=== Auto Developer 2-Team Demo (DX-10) ===" if teams else "=== Auto Developer Demo (DX-6) ==="
    print(header)
    print(f"Demo workspace: {repo_dir}")

    try:
        if teams:
            _run_demo_teams_flow(repo_dir)
            return 0
        with tempfile.TemporaryDirectory(prefix="auto-dev-demo-agents-") as agents_tmp:
            agents_dir = Path(agents_tmp)
            maker_script = agents_dir / "demo_maker.py"
            checker_script = agents_dir / "demo_checker.py"
            maker_script.write_text(_DEMO_MAKER_SCRIPT, encoding="utf-8")
            checker_script.write_text(_DEMO_CHECKER_SCRIPT, encoding="utf-8")

            py_quoted = shlex.quote(sys.executable)
            maker_cmd = f"{py_quoted} {shlex.quote(str(maker_script))} {{task_id}}"
            checker_cmd = f"{py_quoted} {shlex.quote(str(checker_script))} {{task_id}}"

            # 1. 一時 Git リポジトリ作成 + auto-dev init
            print("\n[1/5] Initializing temporary Git repository and running `auto-dev init`...")
            _run_git(repo_dir, "init", "-b", "main")
            _run_git(repo_dir, "config", "user.name", "AutoDev Demo")
            _run_git(repo_dir, "config", "user.email", "demo@example.com")

            rc, out, err = _run_demo_cli(repo_dir, "init")
            _ensure_cli_ok("auto-dev init", rc, out, err)
            (repo_dir / "PLAN.md").write_text(_DEMO_PLAN_MD, encoding="utf-8")
            _run_git(repo_dir, "add", "-A")
            _run_git(repo_dir, "commit", "-m", "chore: initialize auto-developer project")
            print(
                "  -> Initialized SPEC.md, scripts/verify_local.sh, tests/immunity/, "
                "and wrote 2-task PLAN.md."
            )

            # 2. 正常な 2 タスクの実行
            print(
                "\n[2/5] Running 2 normal tasks via `auto-dev run` "
                f"(Maker={DEMO_MAKER_MODEL}/{DEMO_MAKER_FAMILY}, "
                f"Checker={DEMO_CHECKER_MODEL}/{DEMO_CHECKER_FAMILY})..."
            )
            rc, out, err = _run_demo_cli(
                repo_dir,
                "run",
                "--maker-cmd",
                maker_cmd,
                "--checker-cmd",
                checker_cmd,
                "--maker-model",
                DEMO_MAKER_MODEL,
                "--maker-family",
                DEMO_MAKER_FAMILY,
                "--checker-model",
                DEMO_CHECKER_MODEL,
                "--checker-family",
                DEMO_CHECKER_FAMILY,
            )
            _ensure_cli_ok("auto-dev run (tasks 1-2)", rc, out, err)

            plan_after_2 = (repo_dir / "PLAN.md").read_text(encoding="utf-8")
            if "- [x] 1." not in plan_after_2 or "- [x] 2." not in plan_after_2:
                raise RuntimeError("Expected tasks 1 and 2 to be marked [x] in PLAN.md.")
            recent_commits = _run_git(repo_dir, "log", "--oneline", "-n", "2").stdout.strip()
            print("  -> Tasks 1 and 2 implemented, verified, reviewed, and committed ([x]):")
            for c_line in recent_commits.splitlines():
                print(f"     {c_line}")

            # 3. 保護ファイル (SPEC.md) 改変による隔離
            print("\n[3/5] Running task 3 that modifies protected SPEC.md (expect quarantine & exit 1)...")
            (repo_dir / "PLAN.md").write_text(
                plan_after_2.rstrip() + "\n- [ ] 3. 保護ファイル (SPEC.md) の改変を試みるタスク\n",
                encoding="utf-8",
            )
            _run_git(repo_dir, "add", "PLAN.md")
            _run_git(repo_dir, "commit", "-m", "chore: add task 3 (protected file tamper demo)")

            rc_fail, out_fail, err_fail = _run_demo_cli(
                repo_dir,
                "run",
                "--maker-cmd",
                maker_cmd,
                "--checker-cmd",
                checker_cmd,
                "--maker-model",
                DEMO_MAKER_MODEL,
                "--maker-family",
                DEMO_MAKER_FAMILY,
                "--checker-model",
                DEMO_CHECKER_MODEL,
                "--checker-family",
                DEMO_CHECKER_FAMILY,
            )
            if rc_fail != 1:
                raise RuntimeError(
                    f"Expected protected-file tamper task to fail with exit 1, got {rc_fail}.\n"
                    f"STDOUT:\n{out_fail}\nSTDERR:\n{err_fail}"
                )

            tree_status = _run_git(repo_dir, "status", "--porcelain").stdout.strip()
            if tree_status:
                raise RuntimeError(f"Expected main working tree to be clean after quarantine, got:\n{tree_status}")

            q_branches = [
                b.lstrip("* ").strip()
                for b in _run_git(repo_dir, "branch", "--list", "quarantine/3-*").stdout.splitlines()
                if b.strip()
            ]
            if not q_branches:
                raise RuntimeError("Expected a quarantine/3-* branch to be created.")
            q_branch = q_branches[0]
            q_plan = _run_git(repo_dir, "show", f"{q_branch}:PLAN.md").stdout
            if "**[FAILED]**" not in q_plan:
                raise RuntimeError(f"Expected **[FAILED]** marker in quarantined {q_branch}:PLAN.md.")
            failure_log = repo_dir / "logs" / "nightly" / "task_3_failure.log"
            if not failure_log.is_file():
                raise RuntimeError("Expected logs/nightly/task_3_failure.log to be created.")
            failure_lines = failure_log.read_text(encoding="utf-8").splitlines()
            if not failure_lines or failure_lines[0].strip() != "Failure Phase: PROTECTED_FILES":
                first_line = failure_lines[0] if failure_lines else "(empty)"
                raise RuntimeError(
                    f"Expected Failure Phase: PROTECTED_FILES in task_3_failure.log, got: {first_line}"
                )
            print(
                f"  -> Circuit breaker caught SPEC.md edit: exited with 1, quarantined work with "
                f"**[FAILED]** to {q_branch}, and restored clean main."
            )

            # 4. Morning Brief の表示
            print("\n[4/5] Running `auto-dev brief`...")
            rc, out_brief, err_brief = _run_demo_cli(repo_dir, "brief")
            _ensure_cli_ok("auto-dev brief", rc, out_brief, err_brief)
            print(out_brief.rstrip())

            # 5. Bundle 作成 & verify-bundle 検証
            print("\n[5/5] Running `auto-dev bundle` and `auto-dev verify-bundle`...")
            rc, out_bundle, err_bundle = _run_demo_cli(repo_dir, "bundle")
            _ensure_cli_ok("auto-dev bundle", rc, out_bundle, err_bundle)

            bundle_path: Path | None = None
            for line in out_bundle.splitlines():
                if line.startswith("BUNDLE CREATED:"):
                    bundle_path = Path(line.split("BUNDLE CREATED:", 1)[1].strip())
                    break
            if bundle_path is None or not bundle_path.is_file():
                bundles = sorted((repo_dir / ".dev" / "bundles").glob("bundle_*.zip"))
                if not bundles:
                    raise RuntimeError("No Evidence Bundle ZIP was created by `auto-dev bundle`.")
                bundle_path = bundles[-1]

            rc, out_verify, err_verify = _run_demo_cli(
                repo_dir, "verify-bundle", str(bundle_path), "--json"
            )
            _ensure_cli_ok("auto-dev verify-bundle --json", rc, out_verify, err_verify)
            try:
                verify_data = json.loads(out_verify)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Invalid JSON from verify-bundle --json: {exc}\n{out_verify}") from exc
            verify_status = verify_data.get("status")
            if verify_status != "VERIFIED":
                raise RuntimeError(f"Expected verify-bundle status == 'VERIFIED', got: {verify_status!r}")
            print(f"Bundle verification status: {verify_status}")

            print("\n=== Demo completed successfully! ===")
            return 0
    except Exception as exc:  # noqa: BLE001
        print(f"DEMO FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        if keep:
            print(f"Demo workspace kept at: {repo_dir}")
        else:
            shutil.rmtree(repo_dir, ignore_errors=True)
