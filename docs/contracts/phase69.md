# 契約書: Phase 69（v0.9.0）

<!--
SPEC.md・PLAN.md を確定する前に、人間が確認するための資料です（2026-11-22 承認済み）。
作成: 2026-11-22（Phase 68 完了時点 1de845d、および ad-team-trial での試行に対する分析）
範囲の決定（人間が承認）: Phase 69 では、コードの修正に加えて `auto-dev demo` とモデルの系統の明示を作る。README・サンプル・リリースは Phase 70 に回す。
-->

## 1. このフェーズの目的

**初めての利用者が、自分の環境とモデルで Auto Developer を試し、使い始められる状態にする。**

README のクイックスタートを試した結果、README を書く前に直す必要がある問題が見つかった。

- 最初に試す手段がない。
  - `run --mock` は、必ず失敗する。
  - シェルの1行コマンドで試すには、実際には使っていないモデル名を書く必要がある。
- 対応しているモデルの系統が3社だけで、それ以外のモデルでは `run` も `commit-step` も通らない。
- 雛形の検証スクリプトが、外部のプロジェクトでそのまま通らない（BACKLOG FRICTION-001 で既知）。

あわせて、Phase 68 の CI と、ad-team-trial での試行で見つかった欠陥を直す。

## 2. 要件の一覧

| ID | 内容 | 種別 | 起点 |
| :--- | :--- | :--- | :--- |
| REL-4 | 型検査を、Linux と macOS の両方の platform 指定で通す | 修正 | Phase 68 の CI（Ubuntu）で `verifier.py:1560` の型エラー。mypy は `sys.platform` の分岐を、指定された OS の側しか解析しない |
| TEAM-27 | 基点からのコミットが0件のチームは「変更なし」として統合の対象から外し、記録を求めない | 修正 | 試行のシナリオ2。タスクのない `core` チームの Bundle が `INTEGRITY_ONLY` になり、統合が必ず失敗した |
| RPT-6 | `auto-dev run` は、レポートを書いてから Bundle を作る（Bundle に、その実行のレポートが入る） | 修正 | Phase 68 の確認。2回目以降の実行で `NIGHTLY_REPORT.md may be stale` |
| RPT-7 | チームのジョブのレポートに、各チームのタスクと査読の結果を載せる | 修正 | 試行のシナリオ1。本体のレポートが「完了した新規タスクはありません」になった |
| DX-5 | 雛形の検証スクリプトは PATH の `python3` を使い（環境変数で上書き可）、`src/` があれば `PYTHONPATH` に加える。ワーカーの既定の起動は `sys.executable` | 修正 | BACKLOG FRICTION-001、試行の準備、`multi_team.py:904` |
| DX-6 | `auto-dev demo`: 一時ディレクトリの中で、正常な実行・隔離・`brief`・Bundle の検証を1コマンドで見せる | 新規 | クイックスタートの確認 |
| DX-7 | モデルの系統を明示できるようにし、組み込みの判定に主要な系統を足す | 新規 | 対応しているのが3社だけ |

Phase 70 に回すもの: DX-1（README の手順の実行テスト）、DX-3（`examples/two-teams/`。ad-team-trial を原型にする）、DX-4（CLI リファレンスの生成）、REL-2（0.9.0 表記）、README.md / README.en.md、CONTRIBUTING.md、独立監査、タグ。

## 3. 影響分析

### 3.0 入口の列挙（Phase 68 の見落としを受けて、最初に行う）

CLI の挙動は、次の6つの入口から使われる。指紋は、すべての入口について探す。

1. `auto-dev`（`pyproject.toml` の `project.scripts`）
2. `python -m auto_developer.cli`
   - テストのサブプロセス
   - ワーカーの起動（`multi_team.py` 904 行目は `"python3"`、3231 行目は `sys.executable`）
3. 互換ラッパー `scripts/nightly_orchestrator.py`（テストから `subprocess` で呼ばれる）
4. `auto_developer.cli.main([...])` の直接呼び出し
5. スキル（`skills/*/SKILL.md`）に書かれたコマンド
6. README に書かれたコマンド（Phase 70 で書き直す）

### REL-4 型検査の両 platform 対応

- **指紋**: `sys.platform == "darwin"` / `startswith("linux")` の分岐の中で、初めて型の注記をしている変数
- **影響する既存テスト**: なし
- **補足**: 人間が `scripts/verify_local.sh` を、`mypy --platform linux` と `mypy --platform darwin` の両方を実行する形に変更済み。Mac でも、Linux 側の型エラーで検証が落ちる（Red）。

### TEAM-27 変更のないチーム

- **指紋**: `INTEGRITY_ONLY`, `require_team_evidence`, `bundle audit failed`, `AUDIT_FAILED`
- **規則（案）**: 統合の時点で `git rev-list --count <基点>..team/<id>` が0のチームを「変更なし（`NO_CHANGES`）」とし、次のように扱う。
  - Bundle の検証とマージを省き、統合の結果に `NO_CHANGES` と表示する。
  - 全チームが `NO_CHANGES` なら、RC タグは作らず、その旨を表示して終了コード 0 とする。
  - コミットが1件以上あるのに記録がないチームは、従来どおり拒否する。
  - 計画に未完了のタスクがあるチームは、従来どおり統合を拒否する。
- **影響する既存テスト（変更不要・守るべき挙動）**:
  - `test_teams_integrate.py` の `test_integrate_rejects_missing_evidence_audit_failure_and_keeps_worktrees`（351 行目）: コミットがあり、記録がない場合の拒否を確かめている。新しい規則でも、この拒否は変わらない。
  - `test_bundler.py`（290, 598 行目）、`test_multi_team_foundation.py`（217 行目）: Bundle 単体の `INTEGRITY_ONLY` の判定を確かめている。判定そのものは変えないので影響しない。
- **実地での確認**: ad-team-trial に失敗したジョブ `job-20261122-002709-97afe0` を残してある。修正後に `teams integrate --job-id job-20261122-002709-97afe0` を実行し直すと、実際の状況で確認できる（`cli` の成果物には欠陥があるので、main には取り込まない）。

### RPT-6 レポートと Bundle の順序

- **指紋**: `orchestrator.py` 683 行目（成功時）と 979 行目（失敗時）の `create_bundle` → `write_report` の順序、`bundle_path`
- **規則（案）**: Bundle の出力先を先に決め、そのパスを書いたレポートを書いてから Bundle を作る。
- **影響する既存テスト**: レポートの中の Bundle のパスや、Bundle の中のレポートを確かめているテストは見つからなかった。
- **影響するドキュメント**: なし

### RPT-7 チームのジョブのレポート

- **指紋**: 「完了した新規タスクはありません」（`reporter.py` 271 行目）、「マルチチーム実行結果」（355 行目）
- **影響する既存テスト**:
  - `test_teams_report_and_brief.py`（161〜209 行目）: 文字列が含まれることを確かめるだけなので、追加の変更では影響しない。
  - `test_nightly_orchestrator.py` 1977 行目: 単一チームのレポートの「最新コミット (HEAD)」の行を確かめている。単一チームのレポートは変えない。
- **影響するドキュメント**: なし（README で扱う）

### DX-5 Python の選択と `src/` の構成

- **指紋**: `/usr/bin/python3`（`cli.py` 77〜78 行目の `DEFAULT_VERIFY_SH`、`test_teams_template_e2e.py` 149 行目）、`"python3", "-m"`（`multi_team.py` 904 行目）
- **影響する既存テスト**:
  - `test_teams_template_e2e.py` 149 行目（**変更が必要**）: 「検証スクリプトは `/usr/bin/python3` を使う」ことを前提に、その Python のバイトコードの置き場所を調べている。新しい規則で検証スクリプトが使う Python（PATH の `python3`、または環境変数で指定したもの）に合わせる。
  - `test_nightly_orchestrator.py` 797〜813, 1328〜1337 行目、`test_team_context_and_protection.py` 123〜124 行目（変更不要）: 雛形が区切りの文字列「Running Project Tests」を含むこと、テストの節に `|| true` がないこと、`teams.json` を保護していることを確かめている。新しい雛形でも、この3点を保つ。
- **影響するドキュメント**: なし。このリポジトリ自身の `scripts/verify_local.sh` も `/usr/bin/python3` を優先しているが、保護ファイルなので、変えるかどうかは人間が判断する。

### DX-6 `auto-dev demo`

- **指紋**: なし（新しいサブコマンド）
- **影響する既存テスト**: なし
- **影響するドキュメント**: README（Phase 70 のクイックスタートの中心になる）、CLI リファレンス（Phase 70、DX-4）

### DX-7 モデルの系統

- **指紋**: `detect_model_family`, `verify_model_independence`, `Unrecognized model family`
- **呼び出し元（5か所）**: `commit-step`（`cli.py` 1169〜1190 行目）、`record-review`（1391 行目）、`run`（`orchestrator.py` 99〜126 行目）、`teams run`（`multi_team.py` 3188 行目）、独立監査（`audit.py` 98, 640 行目）
- **影響する既存テスト（変更不要・守るべき挙動）**:
  - `tests/immunity/test_immunity_checker_policy.py`: `"pro"` のような不明な系統を、終了コード 4 で拒否する。系統を明示しなければ、この挙動は変わらない。
  - `test_audit_model_policy.py`（115〜125 行目）: `"totally-unknown"` を拒否する。組み込みの判定に足す系統の名前と、ぶつからないこと。
  - `test_metrics.py`（166〜267 行目）: 記録された `maker_family` / `checker_family` の値を確かめている。項目の追加（明示か推定か）だけなら影響しない。
- **影響するドキュメント**: `skills/nightly-run/SKILL.md`（`commit-step` の説明に系統の明示を追記）、README（Phase 70）

## 4. 判断していただきたい点

> **決定（2026-11-22、人間が承認）**: D1〜D3 はすべて案A を採用する。

- **D1 `auto-dev demo` の後始末**:
  - 案A（推奨）: 既定では一時ディレクトリを使い、終了時に削除する。`--keep` を付けると残し、場所を表示する。
  - 案B: 既定でカレントディレクトリの下に `auto-dev-demo/` を作って残す。
- **D2 系統を明示する方法**:
  - 案A（推奨）: コマンドの引数（`--maker-family` / `--checker-family` / `--auditor-family`）だけにする。指定した系統が組み込みの判定と食い違う場合（例: `claude-...` という名前に `--checker-family other` を指定）は拒否する。系統の偽装を防ぐためである。
  - 案B: 引数に加えて、リポジトリの設定ファイル（モデル名の接頭辞 → 系統の対応表）も読む。
- **D3 組み込みの判定に足す系統**:
  - 案A（推奨）: `mistral` / `llama` / `qwen` / `deepseek` を足す。それ以外は明示で対応する。
  - 案B: 足さず、明示だけで対応する。

## 5. 人間が行うこと

- Phase 69 の準備コミット: SPEC.md、PLAN.md、LESSONS.md、BACKLOG.md、この契約書、`docs/contracts/phase68.md` の 6 章への追記。`scripts/verify_local.sh` の mypy の変更も含める（作業ツリーにある）。
- Phase 69 の完了後:
  - ad-team-trial で `teams integrate --job-id job-20261122-002709-97afe0` を実行し直し、TEAM-27 を確かめる。
  - CI が両 OS で通ることを確かめる。

## 6. 影響分析の手順メモ（Phase 68 からの改善）

- 「入口の列挙」を最初の手順にした（3.0）。Phase 68 では、DX-2 の影響を `_run_cli(...)` と `main([...])` の形でしか探さなかった。そのため、互換ラッパーを `subprocess` で呼ぶテストを1件見落とした。
- 実地での試行（ad-team-trial）で、テストでは見つからない欠陥が3件見つかった。
  - タスクのないチーム（TEAM-27）
  - レポートの欠落（RPT-7）
  - Checker の見逃し（BACKLOG IDEA-007）
- 影響分析の入力には、テストの grep だけでなく、利用者の操作での試行を含めるのがよい。
