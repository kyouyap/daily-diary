# 収集・保存・復旧の運用

現在は手動で呼び出す収集スキルとローカル保存処理。毎朝7時の登録、Mac復帰時のCodex起動、配置済みスキルの更新は未実施。停止中の日記自動実行を再開しない。

## 手動収集

リポジトリのルートで、[導入](setup.md)に従って準備した私有runtimeの絶対パスを指定する。

```sh
python3 -B -m scripts.activity_collection.cli --root <runtime> plan
python3 -B -m scripts.activity_collection.cli --root <runtime> import --input <batch.json>
python3 -B -m scripts.activity_collection.cli --root <runtime> status
python3 -B -m scripts.activity_collection.cli --root <runtime> read-day --date YYYY-MM-DD
```

planは取得要求だけを出す。収集スキルが資料別に読み、schema_version=1のbatchへしてimportする。importだけではブラウザやAPIを読まない。read-dayだけが個人本文を返すので、報告・ログへ丸ごと貼らない。statusは対象別名・範囲・状態を返す。

収集は直列。同時のChrome操作を始めない。CLIの保存ロックはブラウザ操作全体を排他しない。busyなら後で同じbatchを取り込む。終了コード0は保存処理の成功であり、partialやlogin_requiredを全件取得としない。

ログイン切れは対象だけ保留してほかを続ける。Codexは戻り値の通知要求を確認して必要な対象を知らせる。再ログイン後、次の手動/定期実行で本人識別を再確認し、同じ未確認範囲を回収する。再ログイン検知・通知の自動経路は未接続。

## バックアップと復旧

backup/restoreのCLIを使う。バックアップはこのMac内のruntime/backupsに作る。外部・クラウドには送らない。元データは無期限、バックアップは最大7世代。

```sh
python3 -B -m scripts.activity_collection.cli --root <runtime> backup
python3 -B -m scripts.activity_collection.cli --root <runtime> restore --backup <世代> --destination <空の復旧先>
```

日別記録・進捗・個人設定を一組で保存し、認証情報や既存Screen Time DBは含めない。復旧は空フォルダのみ。稼働中の保存先を上書きせず、ハッシュ・初回取得日・対象別の未確認範囲を確認してから切替を判断する。

保存中断は `recover`、または次のimport/read-day/statusで完成済みpendingを再適用する。壊れたpending・進捗を空ファイルに置き換えない。元ファイルを保持し、検証済みバックアップへ復旧する。

## 更新

更新前にバックアップし、Pythonの架空データテストとスキル検証を行う。Screen Time更新では上流の固定リビジョン、非公開API、保管した追加処理との差を確認する。既存環境を再インストールして直す運用にしない。専用Pythonや権限の変更は本人と調整する。

## 動作検証の範囲

[検証結果](verification.md)を参照する。自動テストは保存・検証処理を確かめるもので、X/YouTube全件取得、Langfuse実認証、iPhone同期、Macの復帰後起動を証明しない。取得不能・データゼロ・資料不足を別に報告する。
