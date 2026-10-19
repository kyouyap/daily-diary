# 3本の収集スクリプトの実データ動作確認

2026-10-19、本人の依頼により実施。自動テストとは別に、実際のScreen Time出力・Langfuse API・Computer Historyイベントを使い、新しいCLIで取得から保存・再読込まで実行した。

## 対象と結果

対象期間は日本時間の2026-10-17 0時〜10-18 0時（終了を含まない）。日記用の既存runtimeから取得設定と対象の対応だけを引き継ぎ、Git管理外の専用runtimeへ保存した。認証ファイルは既存のCodex専用接続を参照し、キーをコピーしていない。

| 資料 | 保存件数 | 実行結果 | 元資料との照合 |
|---|---:|---|---|
| Screen Time | 38件（日・端末・アプリの組） | partial・保存済み | 使用した固定世代のハッシュを確認。元セッションを別の集計方法で計算し、全38件の利用秒数が一致 |
| Langfuse | 26件（会話turn） | success・保存済み | 実APIで取得後、別のHTTP要求でもHTTP 200。全件のID・本人ID・日時・入力・AI出力が保存値と一致 |
| PC活動 | 1,431件 | partial・保存済み | 全件のセグメント・行番号を元イベントへ戻り、日時・アプリ・操作種別が一致 |

合計1,495件を日別JSONへ保存し、既存CLIのread-day経由で読み直した。3本とも同じ期間で再実行し、新規追加はそれぞれ0件、保存後の総件数も1,495件のままだった。標準エラー出力は空。

## 実行方法

リポジトリのルートで、実際に次の3コマンドを実行した。`<動作確認runtime>` は、今回新規作成した私有保存先の絶対パス。

```text
python3 -B -m scripts.activity_collection.collect_screentime --root <動作確認runtime> --start 2026-10-17T00:00:00+09:00 --end 2026-10-18T00:00:00+09:00
python3 -B -m scripts.activity_collection.collect_langfuse --root <動作確認runtime> --start 2026-10-17T00:00:00+09:00 --end 2026-10-18T00:00:00+09:00
python3 -B -m scripts.activity_collection.collect_computer_history --root <動作確認runtime> --start 2026-10-17T00:00:00+09:00 --end 2026-10-18T00:00:00+09:00
```

取得結果のbatchと保存済み日別JSONを照合した後、Screen Timeは元セッションの区間端点を使う別実装で重複区間を除いて集計。PC活動は元ファイルの各出典行へ戻って照合。Langfuseは本番接続へ再度読み取り要求し、全ページを読んで保存値と照合した。保存処理やHTTP読取をモックへ置き換えていない。

保存したbatch/recordsに、実行時に使用したpublic/secret keyとBasic認証文字列が含まれないことも確認した。本文・イベント全体・認証情報は報告やGitへ貼っていない。

## 部分取得と未確認の範囲

- Screen Timeは既存exportsの取込を確認した。OSの保護資料から新しく収集する処理や、iPhone同期の完全性までは今回実行・確認していない。`source_coverage_unknown` とcoverageなしのpartialは意図した動作。
- PC活動では対象期間中の2イベントにアプリ情報がなく、日時とイベント種別だけでは活動として採用できなかった。捏造せずスキップし、`invalid_event` を記録した。保持・観測停止期間の完全性も確認できないため、`history_coverage_unknown` とcoverageなしのpartial。
- Langfuseのsuccessは、この時点で対象条件のAPI全ページを読み取れた意味。後から反映される会話や、当日の他サービスの会話まで網羅する意味ではない。
- 別のMacでの新規導入、OS権限の設定、LaunchAgent登録・スリープ復帰後の定期実行は未確認。既存のAgent・認証・OS設定は変更していない。

専用runtimeは `.daily-diary-runtime/script-live-20261019-112012/` に保持。`live-report.json` に実行結果・照合結果・再実行結果を保存した。原文を含むbatch/recordsとともにGitのignore対象であることを確認した。通常の日記用runtimeへは今回の実データを取り込んでいない。
