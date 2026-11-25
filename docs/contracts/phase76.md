# 契約書: Phase 76（v0.10.0）— Spec Compiler の下書きの出力とスキル

<!--
SPEC.md・PLAN.md を確定する前に、人間が確認するための資料です。
作成: 2026-11-25（Phase 75 完了時点 dae87e5 に対する分析）
範囲の決定（人間が承認、2026-11-25）: v0.10-draft の残りを2つに分ける。Phase 76 は下書きの出力（CMP-5）とスキル（CMP-6）、
Phase 77 は影響分析（CMP-4）とダッシュボードに計画の構造を重ねる表示（STAT-3）。そのあと v0.10.0 のリリース。
-->

## 1. このフェーズの目的

**EGM の設計書から、人間が SPEC と PLAN に写せる下書きまでを、一続きで作れるようにする。**

Phase 75 で、IR（中間表現）の形式、設計書の見出しの読み取り（`compile --outline`）、決定的な点検（`compile --check`）ができた。このフェーズでは、その先の2つを加える。

- **下書きの出力**（`compile --emit`）: 点検を通った IR から、SPEC の節・PLAN のステップ・`teams.json`・契約書・受入テストの案を `.dev/compile/` に書き出す。
- **スキル `/nightly-compile`**: エージェントが、設計書を読んで IR を下書きし、点検を通るまで直し、別系統のモデルの査読を受け、書き出して人間に渡す。IR の下書きは LLM の仕事なので、スキルの側に置く（v0.10-draft の CMP-2）。

このフェーズの後、利用者は「EGM の設計書を書く → `/nightly-compile` → 契約書を読んで SPEC と PLAN に写す → `/nightly-run`」の順で Auto Developer を使える。今は、この「契約書を読んで SPEC と PLAN を書く」部分を、人間と設計セッション（Elephant）が手でやっている。

## 2. 要件の一覧

| ステップ | ID | 内容 | 種別 |
| --- | --- | --- | --- |
| 1 | CMP-5 | `compile --emit` の骨組み（前提の点検、出力先の制限、`--force`、決定的な出力、共通の見出し、`--lang`、`ir.json`・`check.json`） | 新規 |
| 2 | CMP-9 | SPEC の節の下書き（`spec_section.md`。元の場所のコメントを含む） | 新規 |
| 3 | CMP-10 | PLAN の下書き（`plan.md`）、`teams.json` と `plans/<id>.md` | 新規 |
| 4 | CMP-11, CMP-12 | 契約書の下書き（`contract.md`）、受入テストの案（`acceptance_tests.md`） | 新規 |
| 5 | CMP-6 | スキル `/nightly-compile` と `plugin.json` | 新規 |

あわせて、準備のコミットで次を行う（人間が行う）。

- SPEC の Phase 75 の要件を 4 章へ移す。RCV-7 は 4.3 へ。CMP-1・2・3・8 は新しい節「4.12 CMP — Spec Compiler」へ。そのとき、フェーズの途中の判断を文言に入れる。RCV-7 は、中断の対象を保全の段階の失敗にすること。
- CLI のトップレベルの省略形を受け付けない（`allow_abbrev=False`。Phase 75 のステップ 2 で人間が決めた）を、DX-15（実装済み）として 4.10 に加える。

## 3. 設計の判断

### 3.1 出力先と INV-12

下書きは `.dev/compile/` の下にだけ書く。`--out-dir` で別の場所を指定しても、`.dev/compile/` の外なら拒否する。`.dev/` は `init` の `.gitignore` に入っているので、下書きが誤ってコミットされることもない。

既存の出力先を上書きしない（`--force` が必要）のは、人間が下書きに書き込みを始めた後に、もう一度 `--emit` して消してしまうのを防ぐためである。`--force` でも、このコマンドが作る名前のファイルだけを書き直す。

### 3.2 決定的な出力

同じ IR から同じバイト列を出す。こうすると、テストで出力を完全に比べられる。人間も、IR を直した後に `--emit` し直して、差分を `diff` で確かめられる。

### 3.3 下書きの言語（v0.10-draft 7 章の5）

v0.10-draft では「入力の設計書の言語に合わせるか、日本語に固定するか」を挙げたが、案が書かれていなかった。次のように決める。

- 見出しや決まった文言は、`--lang` で選ぶ（既定は `ja`、ほかに `en`）。
- IR の中の文（要件・受入条件・判断）は、翻訳せずにそのまま写す。翻訳は意味を変えうるので、決定的な CLI ではしない。

### 3.4 ステップとチーム（CMP-10）

IR の `steps[]` には、どのチームのステップかの項目がない。ステップの対象のファイルと、チームの `owns` から決まる（L-7 と同じ当てはめ方）。すべてのファイルが1つのチームに当てはまるステップだけを、そのチームの計画に入れる。複数のチームにまたがるステップは、分け方を人間が決めるべきなので、契約書の「人間が判断すること」に挙げる。

### 3.5 追跡（CMP-7 の一部）

SPEC の節の下書きの各要件の直後に、元の場所（設計書の行の範囲と SHA-256）を HTML のコメントで書く。SPEC に写した後も、どの要件が設計書のどこから来たかを追える。設計書が後で変わったことの検出は、IR を残しておけば `compile --check` の元の場所の検査（CMP-1）でできる。承認した IR をリポジトリに残して保護ファイルにすること（v0.10-draft 7 章の3）は、それを読むダッシュボードの表示（STAT-3）と一緒に Phase 77 で行う。

### 3.6 スキルの査読（CMP-6）

IR の下書きは LLM が書くので、設計書の意味を変えたり、Goal や CUJ を落としたりしうる。CLI の点検は、形式と、語句や ID の一致しか見ない（Phase 75 の契約書 3.4）。そこで、スキルの中で、別系統のモデルに「設計書と IR を突き合わせる」査読をさせる。文の意味の点検（L-2・L-3・L-4）も、この査読に任せる。

この査読の記録は、`/nightly-run` の査読（GATE-5・GATE-13）のような仕組みにはしない。下書きは人間が読んで承認するので、最後の関門は人間にある。査読の出力は `.dev/compile/<name>.review.txt` に残し、人間が読めるようにする。

## 4. 影響分析

### 4.0 入口の列挙

1. `auto-dev compile`（`--emit` を加える。`--check`・`--outline` は変えない）
2. `python -m auto_developer.cli`、`scripts/nightly_orchestrator.py`、`main([...])` の直接呼び出し
3. スキル（新しい `skills/nightly-compile/SKILL.md`）と `plugin.json`
4. `docs/cli.md`（DX-4。ステップ 1 で作り直す）
5. `scripts/check_doc_commands.py`（DX-14）: 新しいスキルの `auto-dev` のコマンドも検査の対象になる。README に `compile` が載っていないことは、参考の一覧に出る（終了コードは変わらない）
6. README（このフェーズでは変えない。リリースのフェーズで、`/nightly-compile` の使い方を載せる）

### 4.1 CMP-5・CMP-9〜12（ステップ 1〜4）

- `src/auto_developer/compiler.py` と、`cli.py` の `compile` に `--emit` などの引数を加える。`--check` と `--outline` の挙動は変えない。
- `--emit` と、`--check` / `--outline` を同時に指定した場合の扱い（終了コード 2 にする、など）は、`compile` の今の引数の扱いに合わせる。
- `teams.json` の下書きの読み込みの確かめには、`manifest.py` の `load_teams_manifest` を使う。
- テストの資料: Phase 75 の `tests/fixtures/compile/autonomous_pipeline_orchestrator_resolved.json` を使ってよい。チームのある IR は、テストの中で作る。
- **既存テスト**: Phase 74 までにあったテストは変更しない。Phase 75 で作ったテスト（`tests/test_cmp*`）は、`--emit` を加えたことで引数の扱いの期待が変わる場合だけ、その期待を変えてよい（アサーションの趣旨は変えない）。

### 4.2 CMP-6（ステップ 5）

- `plugin.json` の `suggestedPrompts` に加える。`tests/test_version_consistency.py` は `plugin.json` の版だけを確かめているので、影響しない見込み。
- スキルのテストは、語句があるかどうかだけでなく、流れの順番（見出しの確認 → IR の下書き → 点検 → 査読 → 書き出し）と、止まる条件（足りない節、L-9、査読の REJECT の2回）が書かれていることを確かめる。

## 5. 人間が行うこと

- 準備のコミット: SPEC.md、PLAN.md、この契約書。
- 準備のコミットの前に: Phase 75 の Evidence の記録と査読の記録を `.git/nightly_evidence/archive/phase75/` に退かせる（済み）。
- ステップ 4 の後: テストの資料の IR から書き出した下書きを読み、SPEC と PLAN に写せる形かを目で確かめる。
- ステップ 5 の後: Linux での全テスト、CI、査読の記録の突き合わせ、Bundle の検証。設計書の見本に対して `/nightly-compile` の流れを人間が一度通し、使える形かを確かめる（Phase 77 の準備の材料にする）。
