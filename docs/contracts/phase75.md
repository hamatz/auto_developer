# 契約書: Phase 75（v0.10.0）— 隔離の修正と Spec Compiler の土台

<!--
SPEC.md・PLAN.md を確定する前に、人間が確認するための資料です。
作成: 2026-11-25（Phase 74 完了時点 fca3875 に対する分析）
範囲の決定（人間が承認、2026-11-25）: 隔離で新しいファイルが消える問題（IDEA-026、P1）を最初のステップで直し、そのあと Spec Compiler に入る。
Spec Compiler の全体の方針は docs/contracts/v0.10-draft.md（7 章の5点は決定済み）。
-->

## 1. このフェーズの目的

1. **隔離で作業を消さない**（RCV-7）。Phase 74 のステップ 7 で、偽の秘密鍵の文字列を含む新しいテストファイルが、隔離ブランチにもスナップショットにも残らずに消えた。これは INV-3（失敗した作業を消さない）の穴で、最初に直す。
2. **Spec Compiler の土台を作る**（CMP-1〜3、CMP-8）。v0.10 の目的は、「良い SPEC を書ける人」以外にも Auto Developer を使えるようにすることにある。EGM の設計書から SPEC と PLAN の下書きを作る流れのうち、決定的にできる部分（中間表現の形式、設計書の見出しの読み取り、点検）を CLI に置く。LLM が IR の下書きを作る部分（スキル）と、下書きの出力は Phase 76 で加える。

v0.10-draft の段取りとの対応: 叩き台では「Phase 73: IR の形式と点検（CMP-1〜4）、Phase 74: 下書きの出力・スキル・追跡（CMP-5〜8）と STAT-3」としていた。ダッシュボードの作り直し（Phase 73）と手直し（Phase 74）が入ったので、それぞれ Phase 75・76 にずらす。影響分析（CMP-4）は、既存のリポジトリの構文木を読む大きめの機能なので、Phase 76 に回す。

## 2. 要件の一覧

| ステップ | ID | 内容 | 種別 |
| --- | --- | --- | --- |
| 1 | RCV-7 | 機密の規則でスナップショットから外れる新しいファイルを Git のオブジェクトとして残す。書けなければ消さずに止まる。RCV-6 の案内を直す | 修正 |
| 2 | CMP-1 | `compile --check` の形式の検査（IR の `auto-dev-ir/1`）と INV-12 | 新規 |
| 3 | CMP-1 | 元の場所（行の範囲と SHA-256）の検査 | 新規 |
| 4 | CMP-2 | `compile --outline`（設計書の見出しの読み取り） | 新規 |
| 5 | CMP-3 | L-1・L-8・L-9 | 新規 |
| 6 | CMP-3 | L-5・L-6・L-7 | 新規 |
| 7 | CMP-8 | 実物の設計書（`autonomous_pipeline_orchestrator.md`）での確認 | 新規 |

あわせて、準備のコミットで次を行う（人間が行う）。

- SPEC の Phase 74 の要件を 4 章へ移す。そのとき、フェーズの途中で人間が決めた解釈を文言に入れる。RCV-6 の「新しいファイル」は `git ls-files --others --exclude-standard`、一覧の処理が失敗しても復元は最後まで行う。DX-14 は対象にする書き方と、対象にしない書き方。
- 不変条件 INV-12（Spec Compiler は契約を書かない）を、3 章に加える。`compile` のコマンドがこのフェーズで入るため。

## 3. 設計の判断

### 3.1 消えるファイルの扱い（RCV-7）

BACKLOG の IDEA-026 では、(a) 止まって人間に渡す、(b) Git のオブジェクトとして残す、の2案を挙げた。(b) を基本にし、(b) ができないときだけ (a) にする。

- (b) なら、夜の作業は止まらない（隔離の後の停止は、これまでどおり INV-4 による）。人間は、朝に案内のコマンドで取り出せる。
- Git のオブジェクトは、ブランチやタグから参照されないので、push されない（`git gc` で、既定では2週間後に消える）。隔離ブランチ・スナップショット・Bundle には入れないので、INV-5 の範囲は変わらない。なお、`verify-step` が Tree SHA を求めるとき（GATE-9）にも、無視されていない未追跡のファイルは、すでに同じように Git のオブジェクトとして書かれている。Phase 74 のテストファイルを取り戻せたのは、このためだった。
- 対象には、名前の規則に当たるファイル（`.gitignore` されていない `.env` など）も含める。今は、`git clean -fd` で利用者の `.env` が消えうる。
- オブジェクトを書けなかったときは、何も消さずに止まる（INV-3 の「退避に失敗したら操作を止める」）。

### 3.2 中間表現（IR）の形（CMP-1）

v0.10-draft の 5 章の案を、検査できる形に絞った。

- **元の場所**: 行の範囲だけでなく、その範囲の SHA-256 を持つ。設計書が後で変わったときに、範囲ごとに検出できる（Phase 76 の追跡（CMP-7）の土台）。ファイル全体の SHA-256 が変わっても、範囲が同じなら警告にとどめる。設計書の別の節を直しただけで、すべてが ERROR になるのを避けるためである。
- **`decisions[].forbids`**: 判断と要件の食い違い（L-5）を、決定的に見つけるための項目。LLM が IR の下書きを作るときに、「この判断は何を禁じるか」を語句で書く。CLI は、その語句が要件や受入条件にそのまま出てくるかだけを見る。意味の食い違いの判断は、スキルの査読（Phase 76）に回す。
- **`kind`（`new` / `modify`）**: 既存の要件を変えるのか、新しく足すのかを明示させる（L-6。Phase 70 で、既存の要件と同じことを言う要件が4件あった）。
- **`open_questions[]`**: 未決の点を、IR の中に隠さずに書かせる。空でなければ下書きを出さない（L-9。Phase 70 で、フェーズの途中に SPEC を変えて Bundle が INVALID になった）。

### 3.3 見出しの読み取り（CMP-2）

EGM の設計書の見本は1つだけなので、見出しの対応は v0.10-draft の 5.1 の表をそのまま使う。設計書の見本には、コードブロックの中に markdown の見出し（`NIGHTLY_REPORT.md` のテンプレートの `# Nightly Execution Report`、`## 1. Summary` など）があり、これを見出しとして扱わないことが要点になる。

見出しの対応を設定で変えられるようにする案（v0.10-draft 8 章）は、2つ目の設計書の形が出てきたときに行う。

### 3.4 点検の範囲（CMP-3）

v0.10-draft の 6 章の点検のうち、決定的に判定できるものだけを CLI に置く。

| 点検 | このフェーズ | 理由 |
| --- | --- | --- |
| L-1 受入条件 | CLI | 項目の有無で決まる |
| L-2 「A または B」 | Phase 76 のスキル | 文の意味の判断 |
| L-3 同じ対象の「行う」と「行わない」 | Phase 76 のスキル | 文の意味の判断 |
| L-4 用語 | Phase 76 | どの語が用語かを、決定的に決められない |
| L-5 判断との食い違い | CLI（`forbids` の語句の一致だけ） | 語句の一致は決まる |
| L-6 実装済みの要件との重なり | CLI（ID の一致だけ） | ID の一致は決まる |
| L-7 担当範囲 | CLI | `teams.json` と同じ当てはめ方 |
| L-8 つながらない要件・ステップ | CLI | 参照で決まる |
| L-9 未決の点 | CLI | 目印の語と `open_questions` で決まる |
| I-1〜I-4 影響分析 | Phase 76（CMP-4） | 既存のリポジトリの構文木を読む大きめの機能 |

### 3.5 実物での確認（CMP-8）

v0.10-draft の 5.3 で挙げた食い違いのうち、Decision 4 と Goals・CUJ 2 の食い違い（`git reset --hard` / `git clean -fd`）を L-5 で検出する。もう1つの「Verification Plan は `pytest`、実際の検証スクリプトは `unittest`」は、設計書と実装の食い違いで、リポジトリを読む影響分析（CMP-4）の範囲なので、Phase 76 に回す。

テストの資料の IR は、Maker がこの設計書を読んで作る。元の場所の SHA-256 が、CMP-1 の検査で正しいことを確かめる。設計書（`docs/designs/`）は保護ファイルではないので、設計書を直すとこのテストが失敗する。それは、元の場所の検査が働いている証拠である。設計書を直すときは、資料の IR も直す。

## 4. 影響分析

### 4.0 入口の列挙

1. `auto-dev`（`rollback-step`、`run`、`teams run` の隔離。新しい `compile`）
2. `python -m auto_developer.cli`
3. `scripts/nightly_orchestrator.py`
4. `main([...])` の直接呼び出し
5. スキル（このフェーズでは変えない。`/nightly-compile` は Phase 76）
6. `docs/cli.md`（DX-4。`compile` を足すステップ 2 と、引数を足すステップ 4・6 で作り直す）
7. `scripts/check_doc_commands.py`（DX-14）: README とスキルに `compile` を書くのは Phase 76 以降なので、影響しない
8. README（このフェーズでは変えない）

### 4.1 RCV-7（ステップ 1）

- **指紋**: `guard.py` の `quarantine_and_restore`、`create_quarantine_snapshot`（`is_secret_file` と `has_secret_content` で除外している）、`list_snapshot_only_files`（RCV-6。今は `git ls-files --others --exclude-standard` の一覧から、名前の規則に当たるものだけを除いている。中身の規則で除外されたファイルを、スナップショットにあるものとして並べてしまう）、`format_snapshot_only_files_lines`、`cli.py` の `rollback-step`、`orchestrator.py` と `multi_team.py` の隔離、失敗ログ。
- **変更してよい既存テスト**: `tests/test_rcv6_quarantine_snapshot_listing.py` の 120〜122 行目は、`.env`・`server.pem`・`credentials.json` の名前が `rollback-step` の出力に出ないことを確かめている。RCV-7 では、Git のオブジェクトとして残したファイルの名前と取り出し方を、別の見出しで示す。この3行は、「スナップショットにだけある一覧と `tar xzf` のコマンドに出ないこと」と「ファイルの中身（`super_secret_12345` など）が出力に出ないこと」を確かめる形に変えてよい。
- **守るべき既存テスト（変更しない）**: 機密のファイルが隔離ブランチやスナップショットに入らないことを確かめているテスト（`tests/test_recovery.py`、`tests/test_nightly_orchestrator.py`、`tests/test_teams_prepare_status_cleanup.py` など）。
- 失敗のテスト: `git hash-object` を失敗させる方法は、テストで選んでよい（PATH の前に失敗する `git` を置くのではなく、`GitGuard` の該当の呼び出しを置き換えるなど）。そのとき、作業ツリーが変わっていないこと、隔離ブランチができていないことを確かめる。

### 4.2 CMP-1〜3（ステップ 2〜6）

- 新しいモジュール（例: `src/auto_developer/compiler.py`）と、`cli.py` の新しいサブコマンド `compile`。既存の機能は変えない。
- L-7 は、`boundary.py` の `_match_path` と同じ当てはめ方を使う（同じ関数を使う）。
- L-6 は、SPEC.md の 4 章の要件の行（`- [x] **<ID> `）から ID を読む。
- INV-12 のテスト: `compile` の各オプションの実行の前後で、`git status --porcelain` と、リポジトリの直下のファイルの一覧が変わらないこと。
- **既存テスト**: 変更しない。

### 4.3 CMP-8（ステップ 7）

- 新しいテストと、テストの資料（`tests/fixtures/compile/` の下の IR の JSON）だけ。既存テストは変更しない。
- 資料の IR は、設計書の全体を写す必要はない。Goals の Fail-Fast の要件、CUJ 2 の受入条件、Decision 4 を含み、元の場所が正しいこと。

## 5. 人間が行うこと

- 準備のコミット: SPEC.md、PLAN.md、この契約書。
- 準備のコミットの前に: Phase 74 の Evidence の記録と査読の記録を `.git/nightly_evidence/archive/phase74/` に退かせる（済み）。
- ステップ 1 の後: 一時リポジトリで、偽の秘密鍵の文字列を含む新しいファイルと、無視されていない `.env` を作って `rollback-step` を実行し、案内のコマンドで取り出せることを確かめる。
- ステップ 7 の後: Linux での全テスト、CI、査読の記録の突き合わせ、Bundle の検証。`compile --outline` を設計書の見本に対して実行し、出力を目で確かめる。
