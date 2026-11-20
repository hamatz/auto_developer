# docs/history — v0.8.0 までの記録（凍結）

v0.9.0 では、SPEC.md と LESSONS.md を書き直しました（「歴史の断絶」）。
v0.8.0 までの版は、このディレクトリに当時の内容のまま残しています。**以後は編集しません。**

| ファイル | 内容 |
| :--- | :--- |
| `SPEC-v0.8.md` | v0.8.0 時点の SPEC（機能A〜YYY）。作業した順に要件を追記した開発ログ形式 |
| `LESSONS-v0.8.md` | v0.8.0 時点の LESSONS（40件超の個別事象） |
| `PLAN-phase65.md` | v0.8.0 をリリースした Phase 65 の PLAN |

コードやコミットメッセージに出てくる旧ID（`機能WWW`、`XXX.3`、`EEE.4` など）は、`SPEC-v0.8.md` を参照してください。v0.8.0 時点のコードとの対応は、タグ `v.0.8.0` で確認できます。

## なぜ書き直したか

- 旧 SPEC は 864 行・150KB に達していました。現在の仕様を知るには全体を読む必要があり、Checker に渡す範囲も切り出しにくくなっていました。
- 同じ機能について「Stage 1 最終確定」「完全無欠」「完璧クローズ」などの追記が重なり、どれが最新の契約なのかを判断しにくくなっていました。
- 「Stage」の番号が README と SPEC で食い違っていました。
- v0.9.0 から OSS として外部の利用者を迎えるので、利用者と新しい貢献者が読める分量に戻す必要がありました。

## 新しい SPEC での扱い

- 要件は領域ごと（CORE / GATE / RCV / RPT / BDL / AUDIT / SBX / TEAM / REL / DX）にまとめ、ID を振り直しました。
- 新しい SPEC は「現在の挙動の契約」です。経緯は LESSONS.md・CHANGELOG.md・git 履歴に残します。
- 旧 SPEC で未チェックのまま残っていた項目は、次のように扱いました。

| 旧ID | 扱い |
| :--- | :--- |
| 機能U（試行履歴） | 実装されていることを確認した → CORE-4 に統合 |
| 機能VV / WW（親の強制終了への耐性・同一性照合） | 後続の XX / YY で実装済み → TEAM-4 に統合 |
| 機能VVV（実 Claude 監査の完全性検証） | `stop_reason` の拘束などは実装済み → AUDIT-1 に統合 |
| 機能YYY（v0.8.0 リリース） | v0.8.0 として完了 |

## 旧ID と新ID の大まかな対応

| 旧ID | 新ID |
| :--- | :--- |
| A, B, C, D, F, H, K | CORE-1〜6, GATE-1 |
| G, I, M, N, Q, XXX.1 | GATE-2〜4 |
| E, J, L, S, T | RCV-1〜3, RPT-1 |
| P, R, YYY.2 | RPT-1〜2 |
| XXX.3〜5, YYY.1 | RPT-3〜4 |
| V, Ω/Ψ/Φ/Χ（機密スキャン・マニフェスト照合） | BDL-1〜2, INV-5 |
| CCC, EEE, FFF, GGG, HHH, QQQ〜UUU, WWW, XXX.2 | AUDIT-1〜5 |
| ZZ, AAA, BBB, DDD, III〜PPP | SBX-1〜2 |
| W〜Z, AA〜FF, GG〜YY | TEAM-1〜6 |
| O | CORE-9, INV-1 |

## Evidence Bundle の互換性

v0.9.0 から、Bundle に形式バージョン（`format_version: 2`）を記録します（SPEC REL-1）。
v0.8.0 以前に作った Bundle は、v0.8.0 の auto-dev で検証してください。

```bash
pip install "git+https://github.com/hamatz/auto_developer.git@v.0.8.0"
auto-dev verify-bundle <old-bundle.zip>
```
