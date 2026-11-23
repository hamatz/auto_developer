# 契約書: Phase 72（v0.10.0 の最初のフェーズ）

<!--
SPEC.md・PLAN.md を確定する前に、人間が確認するための資料です。
作成: 2026-11-23（v0.9.0 リリース後、12140dd 時点に対する分析）
v0.10 全体の方針は docs/contracts/v0.10-draft.md（7 章の5点は、2026-11-23 に提案どおり決定）。
-->

## 1. このフェーズの目的

- v0.9.0 のリリース前の独立監査で見つかった、機密の検査の穴（AUD-1004、AUD-1005）を塞ぐ。
- 「何が起きて、人間は何を判断すればいいか」を一目で分かるようにする土台として、記録だけから作る状態のスナップショット（`status --json`）と、それを表示するダッシュボードを用意する。Spec Compiler（Phase 73〜74）が出す計画の構造は、後でこの上に重ねる。

## 2. 要件の一覧

| ステップ | ID | 内容 | 種別 |
| --- | --- | --- | --- |
| 1 | GATE-11 | 機密の検査で、日本語などを含むファイル名と種類の変更を扱う（GATE-10。GATE-8 は表示だけ） | 修正（監査の指摘） |
| 2 | STAT-1（単一チーム） | `auto-dev status [--json]`: 計画・タスクごとの経過・隔離・監査・`attention[]` | 新規 |
| 3 | STAT-1（チーム） | チームのジョブ、チームごとの状態、統合の結果を加える | 新規 |
| 4 | STAT-2 | `auto-dev dashboard`: スナップショットを埋め込んだ1枚の HTML | 新規 |
| 5 | STAT-2 | `dashboard --watch`: 一定の間隔で作り直す | 新規 |

SPEC の不変条件に、INV-11（表示は記録から）を加えた。

## 3. 影響分析

### 3.0 入口の列挙

新しいサブコマンド（`status`、`dashboard`）は、次の入口から使われる。

1. `auto-dev`
2. `python -m auto_developer.cli`
3. `main([...])` の直接呼び出し
4. `docs/cli.md`（DX-4 のテストが、CLI の定義から作り直した結果との一致を確かめる）
5. README（このフェーズでは変えない。v0.10.0 のリリースのフェーズでまとめて書く）

**注意**: CLI にサブコマンドを足すと、`docs/cli.md` が古くなり、`tests/test_dx4_cli_reference.py` が失敗する。サブコマンドを足すステップでは、同じステップで `python scripts/gen_cli_reference.py` を実行して `docs/cli.md` を作り直す。これは既存テストの変更ではない。

### 3.1 GATE-11（ステップ 1）

- **指紋**:
  - `guard.py` の `find_secret_files_in_tree`（`diff-tree -r --name-only --diff-filter=AM`。`-z` も `core.quotePath=false` もない）
  - `evidence.py` の `compute_canonical_diff`（`git diff --cached`）と `redact_canonical_diff`（`diff --git a/... b/...` の行からパスを取り出す `_extract_diff_rel_path`）
- **起きていること**: Git は既定で、日本語などを含むパスを `"\351\215\265.pem"` のように引用符と8進数で表す。その結果、次の2つが起きる。
  - GATE-10: 末尾が `.pem"` になり、拡張子の規則に当たらないので、`鍵.pem` がコミットされる（AUD-1004）。
  - GATE-8: 確かめたところ、`鍵.pem` の中身は正しく省略されていた（名前の判定で引用符を外している）。ただし、省略の表示のファイル名が `\351\215\265.pem` の形になる。読み方を GATE-10 とそろえ、元のファイル名で表示する。
- **種類の変更（AUD-1005）**: `--diff-filter=AM` は `T`（種類の変更）を含まない。`AMT` にする。
- **正規の差分のバイト列への影響**: `core.quotePath=false` にすると、日本語などを含むパスの差分の見出しが変わる。`review-input` と `record-review` は同じ関数で正規の差分を作るので、照合は崩れない。過去の Bundle の写しは、記録した SHA-256 で照合しているので影響しない。
- **守るべき既存テスト（変更しない）**: `tests/test_gate10_secret_commit.py`、`tests/test_gate8_canonical_diff_redaction.py`、`tests/test_gate5_review_evidence.py`、`tests/immunity/test_immunity_review_evidence.py`。どれも ASCII のファイル名だけを使っているので、挙動は変わらない。
- **Red の作り方**:
  - 一時リポジトリに `鍵.pem` を作って `verify-step` すると、`SECRET_FILE_IN_COMMIT` にならずに通る。
  - `review-input` の差分の省略の表示が、`\351\215\265.pem` の形になる（中身は省略されている）。
  - 追跡している `.env` のシンボリックリンクを、中身のある通常のファイルに置き換えると、`verify-step` を通る。

### 3.2 STAT-1（ステップ 2・3）

- **新しく書くのは読み取りだけ**。何も書き換えない（`.dev/` にも書かない）。
- **記録の読み取りは、既存の関数を使う**（同じものを別の規則で読むと、BDL-3 のように食い違う）:
  - Evidence の記録の場所: `evidence.py` の `resolve_evidence_records_dir`（BDL-5 の規則）
  - 計画ファイルのタスク: `dispatcher.py`（CORE-10 の読み取り範囲）
  - イベント: Evidence の保存先の `events.jsonl`（イベントの種類は `verify_step`、`checker_review`、`commit_step`、`rollback_step`、`audit_stage`）。worktree では `.dev/evidence/` の下になる。
  - チームのジョブ: `multi_team.py` の、ジョブの状態の読み込み（`.dev/integration_jobs/`）と、`teams status` が使っている関数
  - 独立監査: `audit_stage.json`。`verify-bundle` が `AUDIT_OUTDATED` を判定する規則（`bundler.py`）を、関数として共有する
- **既存の表示との関係**: `brief` と `NIGHTLY_REPORT.md` は変えない。STAT-1 は新しい出力で、既存のテストには影響しない。ただし、`attention[]` の中身（隔離、判断待ちなど）は、`brief` の「判断してほしいこと」と同じ規則にそろえる。違う規則にすると、画面と朝の報告で言うことが変わる。
- **既存テストへの影響**: `status` という名前のトップレベルのサブコマンドは、まだない（`teams status` とは別）。既存テストで、サブコマンドの一覧をそのまま確かめているものはない。

### 3.3 STAT-2（ステップ 4・5）

- 新しいモジュール（例: `src/auto_developer/dashboard.py`）に置く。既存のコードは、CLI への登録だけを変える。
- **安全**: タスクの題名、Checker の理由、ブランチ名などの文字列は、計画ファイルやエージェントの出力から来る。HTML に埋め込む前に必ずエスケープする（`html.escape`）。`<script>` に埋め込む JSON は、`</` を `<\/` にするなど、埋め込みの規則に従う。外部の URL を読み込まない。
- **表示の検査のテスト**: 生成した HTML に、次のことを確かめるテストを置く。
  - `attention[]` の項目が、タスクの一覧より前に出る。
  - `<script>` を含む題名が、エスケープされている。
  - `http://` や `https://` から読み込む要素（`src=`、`href=` のスタイルシートなど）がない。
  - `schema` が違うスナップショットで、その旨を表示する。
- `--watch` のテストは、短い間隔で2回書き直されることと、中断できることを確かめる（時間に依存しすぎない形にする。BACKLOG IDEA-011）。

## 4. 判断済みの点（2026-11-23）

docs/contracts/v0.10-draft.md の 7 章のとおり。

- `compile --check` は `/nightly-run` の前の関門にせず、勧めるだけにする（Phase 73〜74 で扱う）。
- ダッシュボードの更新は、サーバーなし（`--watch` で HTML を書き直す）から始める。
- 承認した IR はリポジトリに残し、保護ファイルにする（Phase 73〜74）。
- IR の下書きは、スキルだけで作る（Phase 74）。
- 下書きの言語は、入力の設計書の言語に合わせる（Phase 74）。

## 5. 人間が行うこと

- 準備のコミット: SPEC.md、PLAN.md、BACKLOG.md、この契約書、docs/contracts/v0.10-draft.md。
- 準備のコミットの前に: Phase 71 の Evidence の記録と査読の記録を `.git/nightly_evidence/archive/phase71/` に退かせる（済み）。
- ステップ 1 の後: CI を確かめる。
- ステップ 5 の後: Linux での全テスト、CI、査読の記録と出力の突き合わせ、Bundle の検証。加えて、このリポジトリと `examples/two-teams` の両方でダッシュボードを開き、表示を目で確かめる。

## 6. 影響分析の手順メモ

- サブコマンドを足すと `docs/cli.md` が古くなる（DX-4）。CLI を変えるステップでは、作り直しを手順に入れる。
- 状態を読む新しいコードは、既存の読み取りの関数を使う。同じ記録を別の規則で読むと、表示どうしが食い違う（BDL-3 → BDL-5 の教訓）。
