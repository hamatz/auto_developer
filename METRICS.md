# METRICS.md: 成果の計測基盤とベースライン記録

本書は、Auto Developer パイプラインにおける自律開発の成果・品質・自律度を定量的に測定し、判断に紐付けるための指標定義と履歴ベースラインを記録する。

## 指標の定義と判断の対応

| 指標名 | 情報源 | 定義 | 判断・評価の対応 |
| :--- | :--- | :--- | :--- |
| **tasks_committed** | event | 当該フェーズでコミット完了に至ったタスク数 | 開発スループットの測定 |
| **first_pass_rate** | event | コミット済みタスクのうち、1回目の verify-step で合格した割合 | Maker の能力およびタスク分割の粒度が適切か |
| **mean_verify_attempts** | event | コミット済みタスクの平均 verify-step 試行回数 | Maker の実装精度と試行錯誤の負荷 |
| **verify_failures** | event | verify-step が失敗 (FAILED) した回数 | コード品質とローカル不合格の早期検出頻度 |
| **circuit_breakers** | event | 保護ファイル改変等により遮断 (CIRCUIT_BREAKER) された回数 | ガードレール・境界逸脱の発生頻度 |
| **quarantines** | event | 修正不能等で隔離 (rollback-step) された回数 | 致命的障害とタスク隔離メカニズムの作動状況 |
| **checker_rejects** | event | Checker (独立査読役) により差し戻し (REJECT) された回数 | 査読が健全に機能し癒着なく欠陥を遮断しているか |
| **checker_policy_violations** | event | 同一モデル・同一系統・系統不明によるコミット試行を遮断した回数 | 独立査読方針 (AGENTS.md Rule 6) の遵守状況 |
| **audit_dispatched** | event | 独立外部監査により真正と判定され次期 PLAN に登録された指摘数 | 監査によって検出された有効なバグ・欠陥件数 |
| **audit_backlogged** | event | 独立外部監査により P2/INFO として BACKLOG.md に送られた指摘数 | 将来改善としてプールされた課題数 |
| **audit_escalated** | event | 修正予算上限を超過し人間判断へエスカレーションされた指摘数 | 難航または根本改修が必要なモジュールの検出 |
| **audit_duplicate** | event | 既存指摘と重複のため抑止された指摘数 | 監査の重複抑止メカニズムの作動状況 |
| **audit_rejected** | event | 偽Red・構文異常等により多層防壁で遮断された不正指摘数 | 監査役の幻覚・不正指摘に対する防御力 |
| **genuine_p0p1_per_100k_tokens** | event | 真正 P0/P1 指摘 (DISPATCHED + ESCALATED) 件数 ÷ 消費トークン合計 × 100,000 | 外部監査のトークンコスト対効果 (トークン不明時は null) |
| **planned_steps** | git | フェーズ開始コミット時点で PLAN.md に存在した数値ステップ数 | 当初計画の規模 |
| **unplanned_steps** | git | フェーズ終了時点で追加されていた数値ステップ数 | パイプライン外や途中でしか見つからなかった計画外タスクの量 |
| **audit_steps_added** | git | フェーズ中に外部監査によって PLAN.md に追加された AUD- ステップ数 | 自律監査サイクルが生成したタスク量 |
| **pipeline_commits** | git | メッセージに [refs: を含む自動パイプラインコミット数 | 自律ループによるコミットの進捗量 |
| **human_commits** | git | メッセージに [refs: を含まない非マージコミット数 | 人間の介入（指示・修正・仕様調整等）の回数 |
| **lead_time_hours** | git | フェーズ開始コミットから終了コミットまでの経過時間（時間） | フェーズ全体の所要時間とリードタイム |


## Phase 50

| 指標 | 値 | 直前差分 | 情報源 |
| :--- | :--- | :--- | :--- |
| tasks_committed | - | - | event |
| first_pass_rate | - | - | event |
| mean_verify_attempts | - | - | event |
| verify_failures | - | - | event |
| circuit_breakers | - | - | event |
| quarantines | - | - | event |
| checker_rejects | - | - | event |
| checker_policy_violations | - | - | event |
| audit_dispatched | - | - | event |
| audit_backlogged | - | - | event |
| audit_escalated | - | - | event |
| audit_duplicate | - | - | event |
| audit_rejected | - | - | event |
| genuine_p0p1_per_100k_tokens | - | - | event |
| planned_steps | 4 | +1 | git |
| unplanned_steps | 0 | 0 | git |
| audit_steps_added | 0 | 0 | git |
| pipeline_commits | 4 | +1 | git |
| human_commits | 2 | +1 | git |
| lead_time_hours | 23.45 | -3.8 | git |

## Phase 51

| 指標 | 値 | 直前差分 | 情報源 |
| :--- | :--- | :--- | :--- |
| tasks_committed | - | - | event |
| first_pass_rate | - | - | event |
| mean_verify_attempts | - | - | event |
| verify_failures | - | - | event |
| circuit_breakers | - | - | event |
| quarantines | - | - | event |
| checker_rejects | - | - | event |
| checker_policy_violations | - | - | event |
| audit_dispatched | - | - | event |
| audit_backlogged | - | - | event |
| audit_escalated | - | - | event |
| audit_duplicate | - | - | event |
| audit_rejected | - | - | event |
| genuine_p0p1_per_100k_tokens | - | - | event |
| planned_steps | 4 | 0 | git |
| unplanned_steps | 0 | 0 | git |
| audit_steps_added | 0 | 0 | git |
| pipeline_commits | 4 | 0 | git |
| human_commits | 1 | -1 | git |
| lead_time_hours | 14.08 | -9.37 | git |

## Phase 52

| 指標 | 値 | 直前差分 | 情報源 |
| :--- | :--- | :--- | :--- |
| tasks_committed | - | - | event |
| first_pass_rate | - | - | event |
| mean_verify_attempts | - | - | event |
| verify_failures | - | - | event |
| circuit_breakers | - | - | event |
| quarantines | - | - | event |
| checker_rejects | - | - | event |
| checker_policy_violations | - | - | event |
| audit_dispatched | - | - | event |
| audit_backlogged | - | - | event |
| audit_escalated | - | - | event |
| audit_duplicate | - | - | event |
| audit_rejected | - | - | event |
| genuine_p0p1_per_100k_tokens | - | - | event |
| planned_steps | 4 | 0 | git |
| unplanned_steps | 0 | 0 | git |
| audit_steps_added | 0 | 0 | git |
| pipeline_commits | 4 | 0 | git |
| human_commits | 1 | 0 | git |
| lead_time_hours | 0.6 | -13.48 | git |

## Phase 53

| 指標 | 値 | 直前差分 | 情報源 |
| :--- | :--- | :--- | :--- |
| tasks_committed | - | - | event |
| first_pass_rate | - | - | event |
| mean_verify_attempts | - | - | event |
| verify_failures | - | - | event |
| circuit_breakers | - | - | event |
| quarantines | - | - | event |
| checker_rejects | - | - | event |
| checker_policy_violations | - | - | event |
| audit_dispatched | - | - | event |
| audit_backlogged | - | - | event |
| audit_escalated | - | - | event |
| audit_duplicate | - | - | event |
| audit_rejected | - | - | event |
| genuine_p0p1_per_100k_tokens | - | - | event |
| planned_steps | 4 | 0 | git |
| unplanned_steps | 0 | 0 | git |
| audit_steps_added | 0 | 0 | git |
| pipeline_commits | 4 | 0 | git |
| human_commits | 3 | +2 | git |
| lead_time_hours | 2.23 | +1.63 | git |

## Phase 54

| 指標 | 値 | 直前差分 | 情報源 |
| :--- | :--- | :--- | :--- |
| tasks_committed | - | - | event |
| first_pass_rate | - | - | event |
| mean_verify_attempts | - | - | event |
| verify_failures | - | - | event |
| circuit_breakers | - | - | event |
| quarantines | - | - | event |
| checker_rejects | - | - | event |
| checker_policy_violations | - | - | event |
| audit_dispatched | - | - | event |
| audit_backlogged | - | - | event |
| audit_escalated | - | - | event |
| audit_duplicate | - | - | event |
| audit_rejected | - | - | event |
| genuine_p0p1_per_100k_tokens | - | - | event |
| planned_steps | 4 | 0 | git |
| unplanned_steps | 0 | 0 | git |
| audit_steps_added | 0 | 0 | git |
| pipeline_commits | 4 | 0 | git |
| human_commits | 1 | -2 | git |
| lead_time_hours | 3.17 | +0.94 | git |

## Phase 55

| 指標 | 値 | 直前差分 | 情報源 |
| :--- | :--- | :--- | :--- |
| tasks_committed | - | - | event |
| first_pass_rate | - | - | event |
| mean_verify_attempts | - | - | event |
| verify_failures | - | - | event |
| circuit_breakers | - | - | event |
| quarantines | - | - | event |
| checker_rejects | - | - | event |
| checker_policy_violations | - | - | event |
| audit_dispatched | - | - | event |
| audit_backlogged | - | - | event |
| audit_escalated | - | - | event |
| audit_duplicate | - | - | event |
| audit_rejected | - | - | event |
| genuine_p0p1_per_100k_tokens | - | - | event |
| planned_steps | 4 | 0 | git |
| unplanned_steps | 0 | 0 | git |
| audit_steps_added | 0 | 0 | git |
| pipeline_commits | 4 | 0 | git |
| human_commits | 1 | 0 | git |
| lead_time_hours | 0.89 | -2.28 | git |

## Phase 56

| 指標 | 値 | 直前差分 | 情報源 |
| :--- | :--- | :--- | :--- |
| tasks_committed | - | - | event |
| first_pass_rate | - | - | event |
| mean_verify_attempts | - | - | event |
| verify_failures | - | - | event |
| circuit_breakers | - | - | event |
| quarantines | - | - | event |
| checker_rejects | - | - | event |
| checker_policy_violations | - | - | event |
| audit_dispatched | - | - | event |
| audit_backlogged | - | - | event |
| audit_escalated | - | - | event |
| audit_duplicate | - | - | event |
| audit_rejected | - | - | event |
| genuine_p0p1_per_100k_tokens | - | - | event |
| planned_steps | 4 | 0 | git |
| unplanned_steps | 0 | 0 | git |
| audit_steps_added | 0 | 0 | git |
| pipeline_commits | 4 | 0 | git |
| human_commits | 1 | 0 | git |
| lead_time_hours | 0.55 | -0.34 | git |

## Phase 57

| 指標 | 値 | 直前差分 | 情報源 |
| :--- | :--- | :--- | :--- |
| tasks_committed | - | - | event |
| first_pass_rate | - | - | event |
| mean_verify_attempts | - | - | event |
| verify_failures | - | - | event |
| circuit_breakers | - | - | event |
| quarantines | - | - | event |
| checker_rejects | - | - | event |
| checker_policy_violations | - | - | event |
| audit_dispatched | - | - | event |
| audit_backlogged | - | - | event |
| audit_escalated | - | - | event |
| audit_duplicate | - | - | event |
| audit_rejected | - | - | event |
| genuine_p0p1_per_100k_tokens | - | - | event |
| planned_steps | 4 | 0 | git |
| unplanned_steps | 0 | 0 | git |
| audit_steps_added | 0 | 0 | git |
| pipeline_commits | 4 | 0 | git |
| human_commits | 1 | 0 | git |
| lead_time_hours | 4.31 | +3.76 | git |

## Phase 58

| 指標 | 値 | 直前差分 | 情報源 |
| :--- | :--- | :--- | :--- |
| tasks_committed | - | - | event |
| first_pass_rate | - | - | event |
| mean_verify_attempts | - | - | event |
| verify_failures | - | - | event |
| circuit_breakers | - | - | event |
| quarantines | - | - | event |
| checker_rejects | - | - | event |
| checker_policy_violations | - | - | event |
| audit_dispatched | - | - | event |
| audit_backlogged | - | - | event |
| audit_escalated | - | - | event |
| audit_duplicate | - | - | event |
| audit_rejected | - | - | event |
| genuine_p0p1_per_100k_tokens | - | - | event |
| planned_steps | 4 | 0 | git |
| unplanned_steps | 0 | 0 | git |
| audit_steps_added | 0 | 0 | git |
| pipeline_commits | 4 | 0 | git |
| human_commits | 1 | 0 | git |
| lead_time_hours | 0.6 | -3.71 | git |

## Phase 59

| 指標 | 値 | 直前差分 | 情報源 |
| :--- | :--- | :--- | :--- |
| tasks_committed | - | - | event |
| first_pass_rate | - | - | event |
| mean_verify_attempts | - | - | event |
| verify_failures | - | - | event |
| circuit_breakers | - | - | event |
| quarantines | - | - | event |
| checker_rejects | - | - | event |
| checker_policy_violations | - | - | event |
| audit_dispatched | - | - | event |
| audit_backlogged | - | - | event |
| audit_escalated | - | - | event |
| audit_duplicate | - | - | event |
| audit_rejected | - | - | event |
| genuine_p0p1_per_100k_tokens | - | - | event |
| planned_steps | 4 | 0 | git |
| unplanned_steps | 0 | 0 | git |
| audit_steps_added | 0 | 0 | git |
| pipeline_commits | 4 | 0 | git |
| human_commits | 1 | 0 | git |
| lead_time_hours | 0.76 | +0.16 | git |

## Phase 60

| 指標 | 値 | 直前差分 | 情報源 |
| :--- | :--- | :--- | :--- |
| tasks_committed | - | - | event |
| first_pass_rate | - | - | event |
| mean_verify_attempts | - | - | event |
| verify_failures | - | - | event |
| circuit_breakers | - | - | event |
| quarantines | - | - | event |
| checker_rejects | - | - | event |
| checker_policy_violations | - | - | event |
| audit_dispatched | - | - | event |
| audit_backlogged | - | - | event |
| audit_escalated | - | - | event |
| audit_duplicate | - | - | event |
| audit_rejected | - | - | event |
| genuine_p0p1_per_100k_tokens | - | - | event |
| planned_steps | 4 | 0 | git |
| unplanned_steps | 0 | 0 | git |
| audit_steps_added | 0 | 0 | git |
| pipeline_commits | 4 | 0 | git |
| human_commits | 1 | 0 | git |
| lead_time_hours | 0.5 | -0.26 | git |

## Phase 61

| 指標 | 値 | 直前差分 | 情報源 |
| :--- | :--- | :--- | :--- |
| tasks_committed | - | - | event |
| first_pass_rate | - | - | event |
| mean_verify_attempts | - | - | event |
| verify_failures | - | - | event |
| circuit_breakers | - | - | event |
| quarantines | - | - | event |
| checker_rejects | - | - | event |
| checker_policy_violations | - | - | event |
| audit_dispatched | - | - | event |
| audit_backlogged | - | - | event |
| audit_escalated | - | - | event |
| audit_duplicate | - | - | event |
| audit_rejected | - | - | event |
| genuine_p0p1_per_100k_tokens | - | - | event |
| planned_steps | 4 | 0 | git |
| unplanned_steps | 0 | 0 | git |
| audit_steps_added | 0 | 0 | git |
| pipeline_commits | 4 | 0 | git |
| human_commits | 2 | +1 | git |
| lead_time_hours | 2.17 | +1.67 | git |

## Phase 62

| 指標 | 値 | 直前差分 | 情報源 |
| :--- | :--- | :--- | :--- |
| tasks_committed | - | - | event |
| first_pass_rate | - | - | event |
| mean_verify_attempts | - | - | event |
| verify_failures | - | - | event |
| circuit_breakers | - | - | event |
| quarantines | - | - | event |
| checker_rejects | - | - | event |
| checker_policy_violations | - | - | event |
| audit_dispatched | - | - | event |
| audit_backlogged | - | - | event |
| audit_escalated | - | - | event |
| audit_duplicate | - | - | event |
| audit_rejected | - | - | event |
| genuine_p0p1_per_100k_tokens | - | - | event |
| planned_steps | 4 | 0 | git |
| unplanned_steps | 0 | 0 | git |
| audit_steps_added | 29 | +29 | git |
| pipeline_commits | 3 | -1 | git |
| human_commits | 50 | +48 | git |
| lead_time_hours | 31.41 | +29.24 | git |

## Phase 63

| 指標 | 値 | 直前差分 | 情報源 |
| :--- | :--- | :--- | :--- |
| tasks_committed | - | - | event |
| first_pass_rate | - | - | event |
| mean_verify_attempts | - | - | event |
| verify_failures | - | - | event |
| circuit_breakers | - | - | event |
| quarantines | - | - | event |
| checker_rejects | - | - | event |
| checker_policy_violations | - | - | event |
| audit_dispatched | - | - | event |
| audit_backlogged | - | - | event |
| audit_escalated | - | - | event |
| audit_duplicate | - | - | event |
| audit_rejected | - | - | event |
| genuine_p0p1_per_100k_tokens | - | - | event |
| planned_steps | 5 | +1 | git |
| unplanned_steps | 2 | +2 | git |
| audit_steps_added | 0 | -29 | git |
| pipeline_commits | 7 | +4 | git |
| human_commits | 8 | -42 | git |
| lead_time_hours | 10.74 | -20.67 | git |

## Phase 64

| 指標 | 値 | 直前差分 | 情報源 |
| :--- | :--- | :--- | :--- |
| tasks_committed | 4 | - | event |
| first_pass_rate | 1.0 | - | event |
| mean_verify_attempts | 2.5 | - | event |
| verify_failures | 0 | - | event |
| circuit_breakers | 0 | - | event |
| quarantines | 0 | - | event |
| checker_rejects | 0 | - | event |
| checker_policy_violations | 0 | - | event |
| audit_dispatched | 0 | - | event |
| audit_backlogged | 0 | - | event |
| audit_escalated | 0 | - | event |
| audit_duplicate | 0 | - | event |
| audit_rejected | 0 | - | event |
| genuine_p0p1_per_100k_tokens | - | - | event |
| planned_steps | 7 | +2 | git |
| unplanned_steps | 0 | -2 | git |
| audit_steps_added | 0 | 0 | git |
| pipeline_commits | 6 | -1 | git |
| human_commits | 1 | -7 | git |
| lead_time_hours | 2.57 | -8.17 | git |
