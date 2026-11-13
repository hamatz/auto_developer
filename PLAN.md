# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 51: Stage 7 — 候補テストの機密読み取り隔離・未追跡改ざん検知完全化・実行順序レース是正 (SPEC.md 機能KKK)
  - サンドボックス内機密ファイル読み取り遮断（Bubblewrap のルートマウント見直し、macOS sandbox-exec の `(deny file-read*)` 適用、ホワイトリスト方式による Python ランタイム・一時クローンのみ許可、ホストのホームディレクトリ `~` や元リポジトリの read deny）（機能KKK.1 / Sol氏 Phase 50 P0指摘）
  - 未追跡・無視ファイルの内容 SHA-256 ハッシュ記録による改ざん検知の完全化（機能KKK.2 / Sol氏 Phase 50 P1指摘）
  - サンドボックス起動と元リポジトリ保護の起動順序是正（TOCTOU レース解消: ロックダウン内側での Popen 起動）（機能KKK.3 / Sol氏 Phase 50 P1指摘）
  - サンドボックス下での Level 7 E2E 更新（機密読み取り・未追跡改ざんの物理遮断）・報告の正確性・全体検証ゲート合格・正規 Evidence による Bundle 生成（機能KKK.4）
- スコープ外（Phase 52 以降）: 独立監査（Red-Team レビュー）の実施、実Multi-LLM無人接続。

## 作業手順（Phase 51 から更新・LESSONS.md 2026-11-13 の教訓に基づく）
- Maker と Checker は別系統のモデルにする（AGENTS.md Rule 6）。Maker が Claude 系のとき、Checker サブエージェントは `Model: "pro"`（Gemini 系）を明示指定する。`inherit` は使わない。commit-step の `--checker-reason` に Checker のモデル指定を書く。
- Checker への入力は要約・再構成しない。以下をファイルに書き出し、Checker にはそのパスを渡して直接読ませる:
  1. `awk '/^- \[ \] 機能KKK/,/^## 3\./' SPEC.md > .dev/checker/spec_kkk.md`
  2. `git add -N . && git diff <checkpoint> > .dev/checker/diff_task<n>.patch`（未追跡ファイルも差分に含める）
- verify-step の前に `git status --porcelain` を確認し、意図しないファイル（実験の生成物など）がないことを確かめる。実験・挙動確認は必ず `mktemp -d` で作った一時ディレクトリで行い、リポジトリ直下で実行しない。
- Evidence Record は手書きしない。各ステップで以下の正規経路のみを使う:
  1. `python3 scripts/nightly_orchestrator.py verify-step --checkpoint <checkpoint> --task-id <n>`
  2. Checker 査読（APPROVE / REJECT）
  3. `python3 scripts/nightly_orchestrator.py commit-step --task-id <n> --checkpoint <checkpoint> --checker-decision <decision> --checker-reason "<reason>"`
  `.git/nightly_evidence/` 配下のファイルを直接編集・作成しない。正規経路が失敗した場合は止まってこのファイルに記録する。
- Bundle 生成後に SPEC.md や追跡ファイルを変更しない（Evidence の spec_sha と不一致になる）。SPEC の完了マークは次フェーズの準備コミットで付ける。

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- KKK.1: サンドボックスの読み取りポリシーが強化（元リポジトリに対する `deny file-read*`）されたため、元リポジトリのパスに対する `target.exists()` が `PermissionError` を送出する。`tests/test_immunity_and_red_acceptance.py` の受入テスト8において、改変試行ブロック全体を try-except で囲むように更新する（テストの意図・期待値は変更しない）。
- KKK.2: 未追跡・無視ファイルの内容改ざんを検証するテストを追加する。
- KKK.3: ロックダウン内側での Popen 起動順序を検証するテストを追加する。
- KKK.4: `tests/test_level7_real_loop_e2e.py` に機密ファイル読み取り試行の拒絶経路を追加する。
- 免疫テスト（`tests/immunity/**`）は変更しない。

## ステップ
- [x] 1. 候補テストのサンドボックス内機密ファイル読み取り遮断（ホストホームディレクトリ・元リポジトリの read deny）の実装と単体テスト（Red-First）（KKK.1）
- [ ] 2. 未追跡・無視ファイルの内容 SHA-256 ハッシュ記録による改ざん検知の完全化と単体テスト（Red-First）（KKK.2）
- [ ] 3. サンドボックス起動と元リポジトリ保護の起動順序是正（ロックダウン内側での Popen 起動）の実装と単体テスト（Red-First）（KKK.3）
- [ ] 4. サンドボックス下の Level 7 E2E 更新（機密読み取り・未追跡改ざんの物理遮断）・報告の正確性・全体検証ゲート合格・正規 Evidence による Bundle 生成（KKK.4）

## 止まっていること・判断待ち
- なし。Phase 50 がマージされ、P0/P1 の3点（機密読み取り境界、未追跡ファイル改ざん検知、起動順序レース）の修正方針が確定済み。
