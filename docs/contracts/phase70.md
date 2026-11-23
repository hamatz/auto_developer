# 契約書: Phase 70（v0.9.0）

<!--
SPEC.md・PLAN.md を確定する前に、人間が確認するための資料です（2026-11-22 承認済み）。
作成: 2026-11-22（Phase 69 完了時点 84a0043 に対する分析）
範囲の決定（人間が承認）: Phase 70 では、査読の証跡の機械による強制と、Checker に差し戻されたまま入った4件の修正を行う。
README・サンプル・リリースは Phase 71 に回す。
-->

## 1. このフェーズの目的

**査読の判定を、自己申告ではなく Checker の出力から機械的に確定させる。そして、Checker に差し戻されたまま入った変更を直す。**

### 何が起きたか

- Phase 67〜69 の全タスクについて、Checker の出力ファイル（`.dev/checker/review_*.txt`）とイベントの記録を、時刻で突き合わせた。
- その結果、**Checker の出力が REJECT なのに APPROVE として記録され、コミットされたもの**が4件見つかった。

| コミット | 要件 | Checker の指摘（要旨） |
| :--- | :--- | :--- |
| b7af7f2（Phase 67 タスク 7） | TEAM-24 | macOS では、バイトコードが worktree の外に書かれる状態でも合格し、Linux と検査の内容が変わる |
| 86eeea9（Phase 68 タスク 4） | BDL-3 | 読み取るディレクトリを広げ、中身から記録かどうかを推測する判定を足した。フォールバック時に未完了の記録を成果として載せる |
| e5fa287（Phase 69 タスク 5） | RPT-7 | 基点が記録されていなくても「main は変更されていない」と表示する。モデル名を記録以外から補う |
| 84a0043（Phase 69 タスク 7） | DX-6 | デモの Checker が自分で SPEC.md を拒否するので、保護ファイルの検知が壊れていても成功と表示する。`VERIFIED` を部分一致で判定する |

- このほか2件（Phase 68 のタスク 6・8）は、Checker の出力より先にコミットしていた。ただし判定は、たまたま APPROVE だった。

### 原因（Antigravity 側の分析、2026-11-22）

1. **直接の原因**
   - Checker（`run_checker.py`）をバックグラウンドで起動していた。
   - その完了通知が、同じタスクから2回届く挙動があった（1回目が APPROVE、数秒後の2回目が REJECT）。
   - オーケストレーターのエージェントは、1回目の通知を受けた時点で出力ファイルを読まずに、`record-review --decision APPROVE` と `commit-step` を実行した。
2. **根本の原因**
   - `record-review` / `commit-step` は、判定を文字列の引数（自己申告）で受け取っていた。
   - そのため `auto-dev` は、Checker の出力の中身、対象の差分、`verify-step` との順序を確かめられなかった。

### 1.1 CI の Ubuntu で見つかったサンドボックスの欠陥（2026-11-22 追記）

- Phase 69 の完了後、CI の Ubuntu 24.04 だけで、`tests/test_red_verifier.py` の29件が失敗した。
  - 大半は `NO_TESTS_RAN`（候補テストが1件も実行されない）。
  - 1件に、bwrap 自身のエラー `bwrap: Creating new namespace failed: Resource temporarily unavailable` が出ていた。
- 原因: 候補テストを起動する前に、プロセス数の上限（`RLIMIT_NPROC`）を「同じユーザーの今のプロセス数 + 50」に設定していた（`verifier.py` の `_compute_nproc_limit`。`ps -u <uid> -o pid=` で数えている）。
  - Linux の `RLIMIT_NPROC` が数えるのは、プロセスではなく**スレッド**である。
  - ランナーには、同じユーザーでスレッドの多いプロセスがあるため、bwrap が名前空間を作る時点で上限に当たっていた。
- 再現: 手元の Linux で、同じユーザーのスレッドを 300 本（プロセスは 11 個）立てた状態で実行すると、CI と同じ2つのエラーが出た。スレッドを立てなければ通る。
- 影響:
  - Linux のデスクトップでは、IDE やブラウザが数百本のスレッドを持つのが普通なので、**多くの Linux の利用者の環境でサンドボックスが起動できない**。
  - 起動の失敗が `NO_TESTS_RAN` と誤って分類され、原因がわからない（安全側には倒れている）。
  - SBX-3 の起動の確認は、この上限をかけずに行うので、「使える」と判定してしまう。
- 関連: CI の Ubuntu では、テストが、マトリクスで指定した 3.10 / 3.12 ではなく、OS の Python 3.12 で走っていた（Traceback の `^^^` の表示から）。このリポジトリの `scripts/verify_local.sh` が `/usr/bin/python3` を優先するためである。保護ファイルなので、人間が直す。
- 対応: SBX-6 として、Phase 70 の最初のステップにする（CI の Ubuntu を先に緑にし、残りのステップを Linux でも検査するため）。

## 2. 要件の一覧

| ID | 内容 | 種別 |
| :--- | :--- | :--- |
| SBX-6 | `RLIMIT_NPROC` をスレッド単位で決め、起動の確認を同じ上限で行い、機構の起動の失敗を `SANDBOX_UNAVAILABLE` とする | 修正（CI の Ubuntu の失敗） |
| GATE-5 | `review-input`（正規の差分の書き出し）と、出力ファイルから判定を読み取る `record-review` | 新規 |
| GATE-6 | `commit-step` が、査読の記録・出力ファイルのハッシュ・差分・モデル・順序を照合する | 新規 |
| GATE-7 | `run` / `teams run` / `demo` の経路でも、同じ査読の証跡を残す | 新規 |
| BDL-4 | Bundle に査読の証跡を入れ、`verify-bundle` が照合する（`format_version` 3） | 新規 |
| RCV-5 | 失敗ログの先頭に、失敗した段階と原因を書く | 修正（DX-8 の前提） |
| DX-8 | デモの修正（DX-6 の欠陥） | 修正 |
| RPT-8 | チームのジョブのレポートの修正（RPT-7 の欠陥） | 修正 |
| BDL-5 | 記録の読み取り場所の修正（BDL-3 の欠陥） | 修正 |
| TEAM-28 | 雛形の受入テストの実行環境の修正（TEAM-24 の欠陥） | 修正 |
| CORE-12 | スキルの査読の手順を、新しい記録の方法に合わせる | 修正 |

SPEC の不変条件に、INV-10（判定は申告ではなく出力から）を加えた。

## 3. 影響分析

### 3.0 入口の列挙

`record-review` と `commit-step` は、次の入口から使われる。指紋は、すべての入口について探した。

1. `auto-dev`
2. `python -m auto_developer.cli`
3. `scripts/nightly_orchestrator.py`（免疫テストはこれを `subprocess` で呼ぶ）
4. `main([...])` の直接呼び出し
5. スキル（`skills/nightly-run/SKILL.md` の 33, 36, 69, 72 行目）
6. README（Phase 71）

`run` / `teams run` / `demo` は、Checker の出力を `auto-dev` 自身が受け取って解析している（`CommandRunnerAdapter.run_checker`）。この経路は自己申告ではないので、挙動を変えず、証跡を残すだけにする（GATE-7）。

### 3.0.1 SBX-6 の影響

- **指紋**: `_compute_nproc_limit`, `RLIMIT_NPROC`, `NO_TESTS_RAN`, `_probe_sandbox_backend`
- **影響する既存テスト（変更不要・守るべき挙動）**:
  - `test_red_verifier.py` の `test_nproc_limit_computation_and_status_recording`: 状態の文字列に `applied: limit=` が含まれることと、上限が余裕の分以上であることを確かめている。
  - `test_rejects_class_without_test_methods_ran_zero`: テストが実際に0件のときの `NO_TESTS_RAN` を確かめている（結果の目印は出る）。
  - どちらも、新しい規則で保たれる。
- **Red の作り方**: 同じユーザーのスレッドを数百本立てた状態で `test_red_verifier.py` を実行する。Mac の手元では bwrap を使わないので、Linux でだけ Red になる。新しいテストは、スレッドの数え方の単体テストと、機構の起動の失敗の分類のテスト（起動に失敗する偽の bwrap を使う）を含める。

### 3.1 免疫テストとの衝突（このフェーズで最も大きな制約）

- `tests/immunity/test_immunity_checker_policy.py` と `tests/immunity/test_immunity_metrics_non_interference.py` は、`record-review` を通さずに `commit-step --checker-decision APPROVE` でコミットできることを確かめている。
- GATE-6 を入れると、この2つは必ず失敗する。免疫テストは Maker が変更できないので、**人間がステップの間で免疫テストを更新する**。手順は次のとおり。
  1. ステップ 2（GATE-5）: Maker が `review-input` と新しい `record-review` を作る。`commit-step` はまだ照合しない。
  2. **人間（H1）**: 更新した2つの免疫テストと、新しい免疫テスト `test_immunity_review_evidence.py` を `tests/immunity/` に置いてコミットする。
     - 案は `docs/contracts/phase70/immunity/` にある。
     - 更新した2つは、ステップ 2 の状態で通る（`commit-step` がまだ照合しないため）。
     - 新しい1つは、`commit-step` の照合に関する部分が Red になる。
  3. ステップ 3（GATE-6）: Maker が `commit-step` の照合を入れる。新しい免疫テストが Green になる。
- 更新案の差分は最小限にした。変えたのは次の3点だけで、元のアサーションはすべて残している。
  - 準備で `SPEC.md` を置く
  - `commit-step` の前に、`review-input` → Checker の出力 → `record-review` を挟むヘルパーを足す
  - 査読の証跡が記録に残ること（ハッシュ）と、査読の記録が計測の失敗に影響されないことの確認を足す
- 新しい免疫テストが塞ぐ抜け道は、次のとおり。
  - 申告と出力の食い違い
  - 記録した後の出力の書き換え（今回の競合）
  - 査読の記録がない
  - 検証より前の査読
  - 判定行が1つでない出力（途中の出力・両方を含む・行の途中の判定）
  - 差分の取り違え
  - SPEC の要約
  - コミット時のモデルの差し替え

### 3.2 GATE-5（ステップ 2）: `record-review` を呼ぶ既存テスト

- **指紋**: `"record-review"` と `"--decision"` の組み合わせ（`--checker-output` がないもの）
- **変更が必要**（新しい呼び方に、機械的に置き換える。アサーションは変えない）:
  - `tests/test_metrics.py`（255, 272, 339 行目付近）
  - `tests/test_dx7_model_families.py`（322, 338, 355 行目付近）
  - `tests/test_rpt7_team_report.py`（131 行目付近）
  - `tests/test_teams_cli_e2e.py`（377 行目付近）
- `test_metrics.py` 272 行目の `--decision MAYBE`（不正な判定の拒否）は、「判定行のない出力ファイル」で終了コード 4 になることを確かめる形にする。

### 3.3 GATE-6（ステップ 3）: `commit-step` を呼ぶ既存テスト

- **指紋**: `"commit-step"`（その前に `record-review` がないもの）
- **変更が必要**（`commit-step` の前に、査読を記録するヘルパーを挟む。アサーションは変えない）:
  - `tests/test_commit_gate_boundary.py`
  - `tests/test_dx7_model_families.py`
  - `tests/test_metrics.py`
  - `tests/test_nightly_orchestrator.py`
  - `tests/test_rpt7_team_report.py`
  - `tests/test_team27_no_changes.py`
  - `tests/test_team_context_and_protection.py`
  - `tests/test_teams_cli_e2e.py`
  - `tests/test_teams_integrate.py`
  - `tests/test_teams_report_and_brief.py`
  - `tests/test_teams_report_pending_decisions.py`
  - `tests/test_teams_run_and_recover.py`
- **影響しない**: `tests/test_skills_teams.py`（SKILL.md にコマンド名が書かれていることと、`--help` が動くことだけを確かめている）。
- 共通のヘルパーは、テストのファイルの外（例: `tests/review_test_helpers.py`）に1つ作る。各テストからはそれを呼ぶ。

### 3.4 BDL-4（ステップ 4）

- **指紋**: `format_version`
- **変更が必要**:
  - `tests/test_bundler.py` 908 行目（`format_version` == 2）
  - `tests/test_dx6_demo.py` 160 行目（`format_version` == 2）
  - どちらも 3 にする。
- `format_version` のない Bundle を拒否するテスト（`test_bundler.py` の REL-1 のテスト）は、そのまま通る（3 未満として拒否される）。

### 3.5 修正の4件（ステップ 6〜9）

- **DX-8**（`tests/test_dx6_demo.py`）:
  - `assertIn("VERIFIED", ...)`（85, 176 行目）を、完全な一致の確認に変える。
  - デモの Checker の振る舞いの変更に合わせる。
- **RPT-8**（`tests/test_rpt7_team_report.py`）:
  - `base_unchanged: True` を前提にした準備（199, 276, 355 行目）と、モデル名を補うことを前提にしたアサーションを、新しい規則（「不明」「記録なし」）に合わせる。
- **BDL-5**（`tests/test_bundler.py` 1207〜1224 行目、`tests/test_reporter.py` 700 行目）:
  - 記録の読み取り場所の規則が1つにまとまることに合わせ、準備だけを変えてよい。
- **TEAM-28**（`tests/test_teams_template_e2e.py`）:
  - 検証スクリプトが使う Python を固定し、worktree の中の `.pyc` を条件なしで確かめる形に変える。

### 3.6 Checker の指摘をそのまま採らなかった点

- Phase 68 タスク 4（BDL-3）の Checker は、「読み取り対象を `records/` だけに戻す」ことを求めていた。
- しかし、チームの worktree の Evidence は `.dev/evidence/` に保存される作りで、`records/` だけに戻すと RPT-7（チームのタスクの表示）が読めなくなる。
- そこで BDL-5 は、「保存先を決める規則（`EvidenceStore`）を読み取りにも使い、ディレクトリの列挙と中身からの推測をやめる」とした。
- Checker の指摘の問題意識（場当たり的な判定・範囲の拡大）には応えつつ、手段は変えた。

## 4. 判断していただきたい点

> **決定（2026-11-22、人間が承認）**: D1〜D3 はすべて案A を採用する。

- **D1 強制の範囲**:
  - 案A（推奨）: 査読の証跡を常に必須とし、無効にする設定は設けない。
  - 案B: リポジトリの設定で無効にできるようにする。古い使い方との互換は保てるが、Maker の振る舞いによっては再び抜け道になりうる。
- **D2 差分の照合の方法**:
  - 案A（推奨）: `review-input` が書き出した正規の差分と、1バイト単位で一致することを求める。
  - 案B: エージェントが作った差分を受け付け、変更されたファイルと内容で照合する。git の設定による出力の揺れを吸収する処理が必要で、照合が複雑になる。
- **D3 Bundle の形式**:
  - 案A（推奨）: `format_version` を 3 にし、2 以下（Phase 66〜69 の開発版）は拒否する。v0.9.0 は未リリースなので、利用者への影響はない。
  - 案B: 2 のまま、査読の証跡を任意の項目にする。証跡のない Bundle を通すことになる。

## 5. 人間が行うこと

- Phase 70 の準備コミット: SPEC.md、PLAN.md、LESSONS.md、BACKLOG.md、この契約書、`docs/contracts/phase70/immunity/`（免疫テストの案）。
- このリポジトリの `scripts/verify_local.sh` で、テストを動かす Python を、環境変数 `PYTHON` → PATH の `python3` の順に選ぶ形に変える（1.1 節）。準備コミットに含める。
- **H1（ステップ 2 の完了後、ステップ 3 の前）**:
  - `docs/contracts/phase70/immunity/` の3つのファイルを `tests/immunity/` に置き、コミットする（2つは既存ファイルの置き換え、1つは新規）。
  - このとき、更新した2つが通り、新しい1つが Red（`commit-step` の照合に関する部分）になることを確かめる。
- ステップ 1〜3 の間は、まだ自己申告の経路が残っている。そこで、エージェントに次を求める。
  - Checker の出力ファイルを読んで判定行を確かめてから記録する。
  - 記録の `--reason` に、判定行をそのまま引用する。
- Phase 70 の完了後:
  - Linux で全テストを実行する。
  - CI が両 OS で通ることを確かめる。
  - 査読の記録と Checker の出力ファイルを、Phase 69 と同じ方法で突き合わせる。食い違いが0件であることを確かめる。

## 6. 影響分析の手順メモ（Phase 69 からの改善）

- Phase 69 では、DX-5 の指紋 `"python3", "-m"` を1行の文字列として探した。そのため、リストの要素が1行に1つずつ並んだテスト（`test_multi_team.py` 210 行目付近）を見落とした。
  - 今回は、呼び出しの単位で数えた（ファイルごとに `"commit-step"` と `"record-review"` の出現を数え、その対応を見た）。
  - Spec Compiler では、行単位の grep ではなく、構文木（AST）で呼び出しやリストの中身を探す必要がある（BACKLOG IDEA-008）。
- 今回の4件は、影響分析ではなく、**記録同士の突き合わせ**（イベントの記録と Checker の出力ファイルの時刻と中身）で見つかった。フェーズの確認の手順に、この突き合わせを常に含める。
