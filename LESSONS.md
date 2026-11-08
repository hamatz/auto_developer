# Repository Lessons Learned (AI Operational Memory)

<!--
エージェントが踏んだ地雷・設計ミス・ライブラリの癖を蓄積する外部記憶。
プロンプトに常時全文を入れるのではなく、着手時に関連する項目だけを検索して読む。
教訓はここに書いて終わりにせず、できる限り下の段（検査・関門・作り）に置き、
元の失敗を再現してガードが作動することを確かめる（Proof）。
モデルを更新したら、危険度の低いガードを外して元の失敗を再現し、
もう起きなければ削除して理由をここに残す。権限・機密・本番接続のガードは自動では外さない。
以下の2件は記入例です。プロジェクトに合わせて書き換えてください。
-->

## 記録フォーマット
### [YYYY-MM-DD] 事象の簡潔な要約
- Context: 対象ファイル、利用ライブラリ、作業内容
- Trap: 何を誤認し、どのような不具合を引き起こしたか
- Rule: 今後とるべき実装パターン（推奨/禁止）
- Rung: 修正の置き場所（会話 / プロンプト / AGENTS.md / 道具 / 検査 / 関門 / 作り）。直感より一段下を目指す
- Immunity Test: 再発を機械的に防止するテストのパス
- Proof: 元の失敗を再現し、ガードが作動したことを確かめた記録

## 恒久教訓インデックス

### [2026-11-08] 外部プロセス自律回収時の同一性・所属関係照合と統合遮断 (PID & PGID Fail-Closed)
- Context: `src/auto_developer/multi_team.py` (`recover_multi_team_job`, `WorkerRegistry`)
- Trap: PID の OS 開始識別子照合だけを行い、Registry の PGID の正当性を検証せずに `os.killpg(w.pgid)` を送ったため、改ざんや誤設定で別プロセスの PGID が指定された場合に無関係なプロセスグループを誤停止する脆弱性があった。また、照合に失敗して停止を保留した未確認ワーカー（`unverified_processes`）が残っているのに、Evidence が揃っているだけで統合ゲートや Worktree 削除に進めてしまう漏れがあった。
- Rule:
  1. プロセスグループ停止前に `os.getpgid(w.pid) == w.pgid` で指定 PID が指定 PGID に実在・所属していることを必須照合し、不一致または照合不能な場合はシグナル送信を絶対に保留する。
  2. 未確認ワーカーが 1 つでも残っている場合は、統合処理（`IntegrationGate`）への進行を物理遮断して `RECOVERY_REQUIRED` に遷移させ、当該ワーカーの Worktree も削除せず保全する。
- Rung: 作り（ガード実装） & 検査（受入テスト）
- Immunity Test: `tests/test_multi_team.py` (`test_recover_multi_team_job_skips_when_pid_does_not_belong_to_pgid`, `test_recover_multi_team_job_aborts_integration_when_unverified_processes_exist`, `test_recover_multi_team_job_preserves_worktree_for_unverified_processes`)
- Proof: 意図的に PID と PGID の所属関係を狂わせた実プロセスを用いたテストで、シグナル送信・統合呼び出し・Worktree 削除がすべて遮断され、期待通り防御されることを実証済み。

### [2026-11-08] 復旧成果物の真正性・改ざん検証 (ZIP 構造検査 vs Evidence 独立監査)
- Context: `src/auto_developer/multi_team.py`, `bundler.py`
- Trap: `zipfile.is_zipfile()` や `testzip()` のチェックサム確認だけでは、ZIP 書式が正常であることしか分からず、`bundle_manifest.json` の欠如、Tree SHA 不一致、Checker 承認レコードの有無といった真正性・改ざんの検証が素通りしてしまう。
- Rule: Evidence 成果物の受け入れ・復旧には必ず `EvidenceBundler.verify_bundle()` を適用し、`bundle_manifest.json`、全ファイル SHA-256、Git Tree、SPEC ハッシュ、Checker 承認レコードのすべてが `VERIFIED` であることを独立監査する。
- Rung: 作り & 検査
- Immunity Test: `tests/test_multi_team.py` (`test_recover_multi_team_job_rejects_zip_without_bundle_manifest`)
- Proof: マニフェストを持たないダミー ZIP が `INVALID_EVIDENCE` として厳格に拒絶されることを実証済み。

### [記入例] 非同期処理とトランザクションのスコープ
- Context: IndexedDB 操作全般
- Trap: transaction のスコープ内で外部の fetch を await したため、トランザクションが自動コミットされ例外が発生した
- Rule: トランザクション内に非同期I/Oを混在させない。必要なデータを取得し終えてから書き込む
- Rung: 検査
- Immunity Test: tests/storage_transaction.test.ts
- Proof: 修正前のコードでこのテストが失敗することを確認済み

### [記入例] 縦組み紙面で図版がページをまたぐ
- Context: 紙面モード（ページ送り）の組版。図版・コード図版を本文に回り込ませる処理
- Trap: 図版の前に余白を足して次のページへ押したところ、本文がその余白まで避け、別の図版が境目をまたいだ。
  スクリーンショットで確認しながら直したため、直した崩れが別の修正で崩れ、一部の判型でだけ直っていることもあり、20回以上往復した
- Rule: 図版は余白で押さず、本文中のページの切れ目まで運ぶ。運べないときだけ詰め物を挿む。
  合否は目視ではなく位置の検査で決め、全判型・全組方向（8通り）に掛ける
- Rung: 検査（CI の必須チェック）
- Immunity Test: examples/compositor/tests/verify_layout.mjs、verify_matrix.mjs
- Proof: 8通りすべてで、壊した版が Exit 1 になることを確認済み（examples/compositor/tests/selftest.mjs）
