# 契約書: Phase 74（v0.10.0）— 手直し（Phase 73 の振り返り）

<!--
SPEC.md・PLAN.md を確定する前に、人間が確認するための資料です。
作成: 2026-11-25（Phase 73 完了時点 441a82e に対する分析）
範囲の決定（人間が承認、2026-11-25）: Spec Compiler の前に、Phase 73 の振り返りで見つかった手直しを1フェーズで行う。
Spec Compiler は Phase 75 以降にずらす。ステップは小さく切る（Phase 73 では 9 ステップ中 8 ステップで1回目の査読が REJECT になり、3 ステップが隔離された）。
-->

## 1. このフェーズの目的

**v0.10.0 のリリースと Spec Compiler の前に、Phase 73 で見つかった穴をふさぐ。**

Phase 73 の確認で、次のことが分かった（PLAN.md の 2026-11-24 の記録）。

- `auto-dev demo --teams` は約1秒で終わり、イベントの時刻が秒単位なので、ダッシュボードの再生が1秒になる。`scripts/record_dashboard.py` の GIF は3コマにしかならず、README や紹介の投稿に使えない（IDEA-023）。リリースの見せ場なので、最優先で直す。
- タスク 5 で、Checker の REJECT の出力が記録されず、同じ差分に対して実行し直した APPROVE が記録されてコミットされた。その査読は、2回目の `verify-step` より前に行われていた（IDEA-020）。Phase 69 に続いて2回目の、査読の記録の食い違いである。
- `verify-step` の既定の制限時間（300 秒）が、このリポジトリの検証（約 8 分）より短い。全体のオプション `--timeout` は、エージェントには見つけられなかった（IDEA-021）。
- 隔離ブランチには新しく作ったファイルが入らず、スナップショットにだけある。隔離の案内どおりにブランチの差分だけを見ると、新しいテストを見落とす（IDEA-022）。
- ダッシュボードの細部（IDEA-024）と、ドキュメントのコマンドのずれの検出（IDEA-025 の 1）。

## 2. 要件の一覧

| ステップ | ID | 内容 | 種別 |
| --- | --- | --- | --- |
| 1 | STAT-9 | 再生の最小の間隔（同じ秒のイベントを1秒ずつ空けて再生する） | 修正 |
| 2 | STAT-10 | ダッシュボードの細部（空白の印の文字、隔離の区別） | 修正 |
| 3 | GATE-13 | 検証の後に作業ツリーが変わっていたら、査読の入力を作らず記録もしない | 新規 |
| 4 | GATE-13 | 1回の検証に1回の査読。判定の決まらない出力も履歴に残す | 新規 |
| 5 | GATE-13 | `review_input` のイベントと `REVIEW_NOT_RECORDED`、スキルの手順 | 新規 |
| 6 | DX-13 | 検証の制限時間（`verify-step --timeout`、検証スクリプトの行、案内） | 新規 |
| 7 | RCV-6 | 隔離の案内（スナップショットにだけある新しいファイル） | 新規 |
| 8 | DX-14 | ドキュメントのコマンドの検査（`scripts/check_doc_commands.py`） | 新規 |

あわせて、準備のコミットで次を直す（人間が行う。Maker の作業ではない）。

- SPEC の Phase 73 の要件を 4 章へ移す。そのとき、フェーズの途中で人間が決めた解釈を文言に入れる。
  - STAT-8: `AUDIT_OUTDATED` は、`check_audit_diff_status` の結果が `OK` 以外ならすべて注意として出す。
  - STAT-7: 「1回だけ警告」は、利用者が実行した1つのコマンドにつき1回。
- RCV-1 の文言を、実際の挙動に合わせる。今の文言は「未追跡ファイルと無視ファイルを含む作業ツリーを隔離ブランチに退避する」だが、実装は、追跡しているファイルの変更だけを隔離ブランチにコミットし（`git add -u`。機密の混入を防ぐため）、未追跡ファイルと無視ファイルはスナップショットに退避している。ブランチ名の形も、今の文言（`quarantine/task-<id>-*`）と違う。
- `scripts/verify_local.sh`（保護ファイル）の先頭に `# auto-dev: verify-timeout=1200` を加える。DX-13 が入るまでは何の効果もない。

## 3. 設計の判断

### 3.1 再生の最小の間隔（STAT-9）

イベントの時刻をミリ秒で記録する案も考えた。しかし、デモは本当に1秒で終わるので、時刻を細かくしても再生は1秒のままである。デモに `sleep` を入れて遅くする案は、利用者がデモを待つ時間が延びる。

そこで、再生の側で「続く2つのイベントの間を最低1秒空ける」ことにした。同じ秒に記録されたイベントは記録の順に1秒ずつ並ぶ。2チームのイベントは交互に並ぶので、並行して進む様子は保たれる。実際の夜の記録（数時間）では、査読とコミットが同じ秒に記録される程度なので、ほとんど影響しない。

`demo --teams` のイベントは約20件なので、1× で約20秒、`record_dashboard.py` の既定（4×、10 コマ/秒）で約50コマの GIF になる見込みである。

### 3.2 査読の記録の厳格化（GATE-13）

タスク 5 で起きたことは、次の順である。

1. 1回目の `verify-step`（Evidence ができる）→ Checker の REJECT を記録した。
2. Maker が手直しした。
3. `review-input` を実行した。このとき、1回目の Evidence が残っていたので、`review-input` は拒否しなかった（GATE-5 は「検証を通った Evidence があるか」しか見ない）。
4. Checker を実行した（`v2`。REJECT）。入力に検証の出力がなく、「検証結果が空」も理由の1つだった。記録はしなかった。
5. 2回目の `verify-step` を実行した。
6. 同じ差分に対して Checker を実行し直した（`v2b`。APPROVE）。これを記録してコミットした。

GATE-13 の最初の規則（Evidence の Tree SHA と今の作業ツリーの一致）があれば、3 の時点で `review-input` が拒否され、査読は必ず検証の後になる。2つめの規則（1回の検証に1回の査読）は、REJECT を記録した後に査読だけをやり直して APPROVE を記録すること（今は GATE-6 が最新の記録だけを見るので通ってしまう）を防ぐ。

**この仕組みで防げないこと**: エージェントが Checker を自分で起動する経路（スキルの低レベルのコマンドの経路）では、同じ入力に対して Checker を2回実行し、1回目の出力を捨てても、ツールには分からない。完全に防ぐには、ツールが Checker を起動し、すべての出力を記録する必要がある（`run` の経路は、すでにそうなっている）。低レベルのコマンドの経路にもそれを用意するかは、Phase 75 以降で決める（BACKLOG の IDEA-020 に残す）。このフェーズでは、`review_input` のイベントと `REVIEW_NOT_RECORDED` の表示で、入力を出したのに記録されていない査読を見えるようにし、スキルの手順に「1回の入力に Checker は1回」と書く。

判定の決まらない出力（出力が途中で切れた場合など。IDEA-013）は、今は記録されずに消える。履歴に残したうえで、同じ検証に対して判定の決まった査読を1回だけ記録できるようにする。Phase 73 のステップ 6 では、途中で切れた出力が2回あり、記録がないまま実行し直された。

### 3.3 検証の制限時間（DX-13）

制限時間はリポジトリ（検証の重さ）で決まるので、リポジトリに置くのが自然である。新しい設定ファイルを作る代わりに、検証スクリプトの先頭の行に書く形にした。検証スクリプトは保護ファイルなので、Maker が制限時間を勝手に延ばせない。人間がこの行を書く。

### 3.4 隔離の案内（RCV-6）

隔離ブランチに未追跡ファイルを入れる案もあるが、`git add -u` にしたのは、機密に当たるファイルをブランチに入れないためだった（ブランチは push されうる。スナップショットは `.dev/` の中にあり、push されない）。この判断は変えず、案内で補う。

### 3.5 ドキュメントのコマンドの検査（DX-14）

準備のときに、試しに同じ検査を手元で書いて、今の README とスキルに対して実行した。存在しないサブコマンドやオプションは見つからなかった。このステップの意味は、今後のずれを検出できるようにすることにある。README に載っていないサブコマンド（`status`、`dashboard` など）は、v0.10.0 のリリースのフェーズで人間が README に足す。この検査の参考の一覧は、その確認に使う。

README の修正案を自動で作る仕組み（IDEA-025 の 2）と、Spec Compiler が要件ごとに影響するドキュメントを書く仕組み（IDEA-025 の 3）は、このフェーズの範囲外とする。

## 4. 影響分析

### 4.0 入口の列挙

1. `auto-dev`（`dashboard`, `demo`, `status`, `review-input`, `record-review`, `commit-step`, `verify-step`, `run`, `teams run`, `teams integrate`, `rollback-step`）
2. `python -m auto_developer.cli`
3. `scripts/nightly_orchestrator.py`
4. `main([...])` の直接呼び出し
5. スキル（`skills/nightly-run/SKILL.md`）
6. `docs/cli.md`（DX-4。引数を変えたステップ 6 では、同じステップで作り直す）
7. README（このフェーズでは変えない）

### 4.1 STAT-9（ステップ 1）

- **指紋**: `dashboard.py` の `_build_replay_payload`（`replay_t` の計算。`_GAP_THRESHOLD_SEC`、`_COMPRESSED_GAP_SEC`）、`status.py` の `timeline[]` の並べ方（同じ時刻のイベントの順番）。
- **既存テストへの影響**: `tests/test_stat2_dashboard.py` に、`t` と `duration` の値を確かめるテストがある（600 秒と 601 秒の境界、30 分の空白、複数の空白の組み合わせなど）。調べた範囲では、どれもイベントの間隔が1秒以上なので、値は変わらない見込みである。1秒未満の間隔のイベントを使っていて値が変わるものだけ、期待の値を変えてよい。境界の確認（600 秒は圧縮しない、601 秒は圧縮する）は変えない。
- **新しいテスト**: 同じ秒の3件のイベントが1秒ずつ空くこと、記録の順が保たれること、10 分を超える空白の圧縮と組み合わせたときの値、`demo --teams` の payload の `duration` がイベントの数から 1 を引いた値以上であること。

### 4.2 STAT-10（ステップ 2）

- **指紋**: `dashboard.py` の空白の印の描画（JavaScript）、`attention[]` の隔離ブランチの項目（`status.py`）、上部の要約。
- `rollback_step` のイベントは、低レベルのコマンドの経路では `quarantine_branch` を記録している（`cli.py` の `rollback-step`）。`run` の経路（STAT-4）でも同じ項目があるかを確かめる。ない場合は止まって報告する。
- **既存テスト**: 変更しない。

### 4.3 GATE-13（ステップ 3〜5）

- **指紋**: `cli.py` の `review-input` と `record-review`、`evidence.py` の `record_review`（GATE-12 の履歴）、`commit-step` の GATE-6 の照合、`status.py` の `attention[]`。
- **ステップ 3（作業ツリーの一致）の既存テストへの影響**: `verify-step` の後に作業ツリーを変えてから `review-input` / `record-review` を呼んでいる既存テストは、拒否されるようになる。準備の中で、その変更を `verify-step` の前に移してよい（アサーションは変えない）。調べた範囲では、`tests/test_gate5_review_evidence.py`、`tests/test_gate12_review_history.py`、`tests/test_gate8_canonical_diff_redaction.py`、`tests/test_dx7_model_families.py`、`tests/test_metrics.py` は、ファイルを書いてから `verify-step` を呼んでいる。`review-input` の出力先は、リポジトリの外か、無視される `.dev/` の下にある（Tree SHA は無視ファイルを含めないので、出力先を書いても作業ツリーは変わらない）。
- **ステップ 4（1回の検証に1回の査読）の既存テストへの影響**: 同じ検証に対して `record-review` を2回以上、判定の決まった出力で呼んでいるテストは、2回目が拒否されるようになる。調べた範囲では次のとおり。
  - `tests/test_gate12_review_history.py`: 2回の記録の間に `verify-step` を挟んでいる（別の検証に対する記録）。変わらない見込み。
  - `tests/test_dx7_model_families.py` の `test_record_review_records_family_source_and_rejects_mismatch`: APPROVE と REJECT と食い違いを、同じ検証に対して続けて記録している。記録ごとに `verify-step` を挟むか、テストを分けてよい。確かめる内容は変えない。
  - `tests/test_metrics.py` の `test_record_review_subcommand_case_insensitive_and_validation`: REJECT の記録の後に、判定の決まらない出力を渡している。判定の決まらない出力は拒否のまま（終了コード 4）なので、変わらない見込み。
  - `tests/test_gate5_review_evidence.py` の `test_record_review_rejects_invalid_checker_output_and_decision_mismatch` など: 判定の決まらない出力や食い違いで記録されなかった後に、1回だけ記録している。変わらない見込み。
  - 判定の決まらない出力が履歴に残るようになるので、履歴の件数を確かめているテストの期待が変わりうる。その場合だけ件数の期待を変えてよい。
- **免疫テスト**: `tests/immunity/test_immunity_review_evidence.py`、`tests/immunity/test_immunity_checker_policy.py`、`tests/immunity/test_immunity_metrics_non_interference.py` は、どれも1回の検証に1回の記録をしている。変更せずに通ること。失敗した場合は止まって報告する。
- **ステップ 5**: `review_input` のイベントは、`metrics` の集計に新しい種類として加わる。既存の集計の値（査読の回数など）を変えないこと。`events.jsonl` の件数を確かめる既存テストが失敗した場合は、止まって報告する。

### 4.4 DX-13（ステップ 6）

- **指紋**: `cli.py` の全体の `--timeout`（既定 300）、`run` の `--timeout`（既定 `None`）、`teams run` の `--timeout`（`argparse.SUPPRESS`）、`verify-step` の `timeout_sec=args.timeout`、`multi_team.py` の `verify_timeout`、`verifier.py` の打ち切りのメッセージ（`Verification timed out after ...`）。
- 全体の `--timeout` の既定が 300 なので、「指定がない」ことを区別できるように直す必要がある。全体の `--timeout` を明示した既存の使い方（`auto-dev --timeout N verify-step ...`）は、これまでどおり効くこと。
- **既存テスト**: 変更しない。打ち切りのメッセージの1行目は変えない。

### 4.5 RCV-6（ステップ 7）

- **指紋**: `guard.py` の `quarantine_and_restore` と `create_quarantine_snapshot`（manifest に全ファイルの一覧がある）、`cli.py` の `rollback-step` の出力、`orchestrator.py` と `multi_team.py` の隔離、失敗ログ（RCV-5）。
- 「スナップショットにだけある新しいファイル」は、manifest の一覧のうち、チェックポイントにも隔離ブランチのコミットにもないファイル（`.dev/`、キャッシュ類を除く）とする。
- **既存テスト**: 変更しない。`rollback-step` の出力の1行目と、失敗ログの先頭の2行（RCV-5）は変えない。

### 4.6 DX-14（ステップ 8）

- 新しいスクリプトとテストだけ。製品のパッケージは変えない。既存テストは変更しない。

## 5. 人間が行うこと

- 準備のコミット: SPEC.md、PLAN.md、BACKLOG.md、この契約書、`scripts/verify_local.sh` の制限時間の行。
- 準備のコミットの前に: Phase 73 の Evidence の記録と査読の記録を `.git/nightly_evidence/archive/phase73/` に退かせる（済み）。
- ステップ 2 の後: このリポジトリと `demo --teams` のダッシュボードを開き、目で確かめる（空白の印の文字、隔離の区別）。
- ステップ 8 の後: Linux での全テスト、CI、査読の記録の突き合わせ（判定の決まらない出力の履歴を含む）、Bundle の検証、`demo --teams` の GIF の書き出し（目標: 30 コマ以上）。
