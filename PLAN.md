# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 31: Stage 3 同時保存の排他的確定 & TOCTOU競合解消 (Sol氏レビュー対応 P1)
  - P1: `os.link` による排他的・アトミック確定（機能QQ.1）
  - P1: TOCTOU 競合注入テスト & 決定論的受入実証（機能QQ.2）
  - 受入実証: 全体検証ゲート合格確認（179+ テスト）、および Evidence Bundle 生成・検証（機能QQ.3）

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- tests/test_multi_team.py: `test_preserve_team_bundles_uses_atomic_replace_and_cleans_temp_file`:
  Phase 30 レビューにおいて Sol氏より、`os.replace()` は対象が存在する場合に無条件上書きするため TOCTOU 競合を引き起こすとの指摘（P1）を受領。
  Phase 31 (QQ.1) にて `os.replace` から `os.link` による排他的アトミック確定への移行に伴い、`os.replace` の監視アサーションを `os.link` による排他的確定の監視へ更新。

## ステップ
- [x] 1. `os.link` による排他的・アトミック確定（QQ.1: `FileExistsError` 時の SHA-256 照合・冪等再利用または衝突拒否）の実装と単体テスト（Red-First）
- [x] 2. TOCTOU 競合注入テスト（QQ.2: 存在確認直後の他プロセスファイル作成・同一ハッシュ冪等成功 & 異ハッシュ上書き阻止・原本保護）の実装と単体テスト（Red-First）
- [x] 3. 同時保存排他性・TOCTOU競合拒否の受入実証、全体検証ゲート合格確認、および Evidence Bundle 生成・検証（QQ.3）

## 止まっていること・判断待ち
- なし
