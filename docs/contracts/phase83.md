# 契約書: Phase 83（v0.11.0）— テーマ G を閉じる、テーマ T の最初: チームの持ち物の宣言と記録

<!--
SPEC.md・PLAN.md を確定する前に、人間が確認するための資料です。
作成: 2026-11-28（Phase 82 完了時点 9815c08 に対する分析）
範囲: テーマ G を閉じ、v0.11 の優先順位（G → T → L → E → S）に従って、T に入る。
-->

## 1. このフェーズの目的

1. **テーマ G を閉じる。** 準備のコミットで、このリポジトリの検証スクリプトを `red-first=enforce` にする（人間の変更）。Phase 82 の後の監査の指摘（AUD-1011、`enforce` で誤って通る向き）を GATE-22 で直す。
2. **テーマ T の最初。** チームごとに違う持ち物（スキル・モデル・査読の文書・検証スクリプト）を `teams.json` に宣言し、それを守り、使い、宣言どおりに動いたことを確かめて記録する（v0.11-draft の 2.1・6.2）。

### 1.1 AUD-1011 の確認

手元で、対象の `tests/test_a.py` に中身のない変更（注釈の1行）だけをし、チェックポイントの時点で失敗する `tests/sub/test_a.py` を置いて確かめた。製品に `src/newmod.py` を足すと、対象のファイルの結果は `CONFIRMED`（`IMPORT_ERROR` → `PASSED`）になった。実際に失敗して通ったのは `tests/sub/test_a.py` で、対象のファイルは一度も失敗していない。`enforce` で誤って通る向きの不具合である。一方、誤って止める向きではないので、`enforce` への切り替えは、この修正を待たずに準備のコミットで行う。

## 2. 要件の一覧

| ステップ | ID | 内容 | 種別 |
| --- | --- | --- | --- |
| 1 | GATE-22 | Red-First の確認で、対象のファイルだけを実行する（AUD-1011） | 修正 |
| 2 | TEAM-29 | `teams.json` のチームの持ち物（`skills`・`maker`・`checker`・`review_docs`・`verify_script`）の宣言と読み込みの検査 | 新規 |
| 3 | TEAM-30 | 宣言した査読の文書と検証スクリプトを保護ファイルにする | 新規 |
| 4 | TEAM-31 | チームの worktree の `verify-step` で、チームの検証スクリプトを使う | 新規 |
| 5 | TEAM-32 | チームの worktree の `review-input` が、`team_profile.json` と査読の文書の写しを書く | 新規 |
| 6 | TEAM-33 | 宣言したモデルと実際のモデルの一致を `commit-step` で確かめ、持ち物を Evidence に残す | 新規 |

## 3. 設計の判断

### 3.1 持ち物を宣言するのは人間（TEAM-29）

v0.11-draft の 2.1 と 3 章（「チームの分け方やスキルの割り当てを、人間の承認なしに機械が決めない」）に従う。持ち物は、保護ファイルの `teams.json` に書く。Maker はチームの持ち物を変えられない。Spec Compiler がチームの分け方と持ち物を提案する部分は、Phase 84 以降で行う（提案するだけで、決めるのは人間）。

新しい項目は厳しく検査する（型、空の値、パスの形、Maker と Checker の系統）。宣言の書き間違いを黙って無視すると、チームが宣言と違う持ち物で動いても気づけないためである。一方、チームの項目の知らないキーを無視する今の挙動は、互換性のために変えない。

### 3.2 宣言したファイルを守る（TEAM-30）

査読の文書（チームの基準）と検証スクリプトは、関門そのものである。Maker が書き換えられると、関門をすり抜けられる。`scripts/verify_local.sh` が保護ファイルであるのと同じ理由で、宣言したファイルも保護ファイルにする。どのファイルが保護されるかは、チェックポイントの時点の `teams.json`（保護ファイル）から読むので、Maker が一覧を変えることもできない。

### 3.3 使う経路は低レベルのコマンドから（TEAM-31・32）

このリポジトリと ad-team-trial は、エージェントが低レベルのコマンド（`pre-flight` → `verify-step` → `review-input` → Checker → `record-review` → `commit-step`）を呼ぶ経路で動いている。まずこの経路で持ち物を使う。`teams run`（外部のコマンドの Maker と Checker）の経路で、チームごとにモデルとコマンドを変えるのは、Phase 84 以降で行う。

統合の後の再検証は、チームの検証スクリプトではなく、リポジトリの `scripts/verify_local.sh` で行う。統合の結果は、どのチームのものでもないためである。

### 3.4 宣言どおりに動いたことを確かめる（TEAM-33）

テーマ G の考え方（柔らかい規則を硬い関門に）を、T にも当てはめる。宣言したモデルと違うモデルで動いたら、`commit-step` が止める（終了コード 4。INV-6 の Maker と Checker の別系統の確認と同じ扱い）。

スキルは、ツールから「実際に使ったか」を確かめられない。宣言したもの（`declared`）として Evidence に残し、何を教えた上での結果かを後から確かめられるようにする（v0.11-draft の 2.5）。

### 3.5 PLAN の作業手順の印

Phase 82 の GATE-21 で、テストの書き方の規則を静的なテストにしたので、準備で印を付け替えた。`git init` のブランチの名前・Python 3.10・秘密の目印の3つを、1つの【機械】の行にまとめた。一時リポジトリを `tempfile` の中に作る規則は、【手順】のまま残した。【手順】は 15 行から 13 行になった。Red-First の確認（`enforce`）を【機械】の行として加えた。

## 4. 影響分析

### 4.1 GATE-22（ステップ 1）

- **指紋**: `verifier.py` の、一時クローンでテストを実行する処理（`loader.discover(start_dir=<対象の親>, pattern=<ファイルの名前>)`。9815c08 の 1632 行の付近）。
- **既存テスト**: 変更しない。

### 4.2 TEAM-29（ステップ 2）

- **指紋**: `manifest.py` の `TeamConfig` と `TeamManifest.from_dict` のチームの項目の読み込み、`teams status --json`・`teams prepare --json` の出力。
- **既存テスト**: `teams status --json`・`teams prepare --json` の項目の集合を完全な一致で確かめているテストは、`profile` を期待に加えてよい。持ち物の宣言のない `teams.json` の挙動は変わらない。

### 4.3 TEAM-30（ステップ 3）

- **指紋**: `guard.py` の `get_modified_protected_files`・`check_protected_files_untouched`（固定のパターン `PROTECTED_PATTERNS`・`BASE_PROTECTED_PATTERNS`）。`verify-step` と `commit-step` の保護ファイルの検査の呼び出し。
- **既存テスト**: 変更しない。`teams.json` のないリポジトリと、持ち物の宣言のない `teams.json` では、保護ファイルの一覧は変わらない。

### 4.4 TEAM-31（ステップ 4）

- **指紋**: `cli.py` の `verify-step` の検証スクリプトの決め方（`--verify-script` と既定の `scripts/verify_local.sh`）、チームの worktree の判定（`_resolve_cli_team_context`、`manifest.py` の `resolve_team_context`）、`resolve_verify_timeout`・`resolve_red_first_mode`、`evidence.py` の Evidence の記録。
- **既存テスト**: Evidence の記録の項目の集合を完全な一致で確かめているテストは、検証スクリプトのパスと SHA-256 の項目を期待に加えてよい。

### 4.5 TEAM-32（ステップ 5）

- **指紋**: `cli.py` の `review-input`（GATE-5。`diff.patch`・`spec_excerpt.md`・`red_first.json` を書いている所）。スキル `/nightly-run` のチームの経路。
- **既存テスト**: `review-input` の出力先のファイルの集合を完全な一致で確かめているテストは、チームの worktree の場合だけ、`team_profile.json` と `team_review_docs/` を期待に加えてよい。

### 4.6 TEAM-33（ステップ 6）

- **指紋**: `cli.py` の `commit-step` の査読の方針の検査（`CHECKER_POLICY_VIOLATION` の所）、`record-review` の査読の記録、`evidence.py`。
- **既存テスト**: Evidence と査読の記録の項目の集合を完全な一致で確かめているテストは、チームの worktree の場合だけ、`team_profile` を期待に加えてよい。チームの worktree の外の挙動は変わらない。

## 5. 人間が行うこと

- 準備のコミット: SPEC.md、PLAN.md、BACKLOG.md、`scripts/verify_local.sh`（`red-first=enforce`。人間の変更）、docs/contracts/v0.11-draft.md（6 章）、この契約書。
- 準備のコミットの前に: Phase 82 の Evidence の記録と査読の記録を `.git/nightly_evidence/archive/phase82/` に退かせる（済み。`audit_stage.json` は残す）。
- ステップ 6 の後:
  - Linux での全テスト、CI、査読の記録の突き合わせ、Bundle の検証。
  - `enforce` で止まったステップがあれば、その理由が正しかったかを確かめる。
  - ad-team-trial で、持ち物の違う2チーム（違うモデル、片方だけ査読の文書と検証スクリプト）を宣言し、低レベルのコマンドの経路で1ステップずつ動かす。宣言したファイルの変更が拒否されること、宣言と違うモデルでのコミットが拒否されることを確かめる。
