# Daily diary

記録をきっかけに「どんな一日だったか」を一緒に振り返り、本人の感想と、その日に行ったことをNotionへ残すCodexスキルです。話し言葉の温度感を残して読みやすく整えます。

## 使い方

```text
$daily-diary 今日の業務日誌をまとめて
```

日付指定がなければ、日本時間の今日0時から実行時点までを対象にします。

1. 保存済み記録と取得状況を確認し、収集スキルで対象日だけのX投稿・YouTube再生履歴・AI会話・PC活動・Screen Timeを取得・ローカル保存して読み直す。メール・本人のカレンダーと、設定済みの位置情報CSVは都度参照する。保存済み資料だけの指定や保存先の未導入時は、その範囲で進める。
2. 確認できた事実と解釈を分け、テーマを3〜5項目・各1〜2文程度で提示する。「どんな一日だった？」から本人の話で補う。活動ごとの感想や学びは必須にしない。
3. 折りたたむ詳細・出典も含めて保存する全文を表示し、本人のOKを受ける。
4. Notionへ保存して読み戻し、日誌のリンクを返す。

取得できない資料は欠落を伝え、本人の補足と全文確認があれば保存できます。同日の追記は既存本文と本人の編集を保ち、統合した全文を確認します。

位置情報は「その日にどんな外出があったか」を思い出す参考資料です。施設名が曖昧な場合は必要に応じて地図で補い、出来事と本人が話した感情を残します。[位置情報の参照](skills/daily-diary/references/location.md)と[保存先の設定](docs/collection/setup.md#位置情報csvの接続)を参照してください。

## 保存先

- [個人業務日誌のカード一覧](https://app.notion.com/p/75307e0c43634c2784a83c4814808053?v=3f8d2916bbfe816bba26000c9d45d9ff)
- [目的と記録ルール](https://app.notion.com/p/3f8d2916bbfe8104b720e2589964c197)

一日一ページ。ページ名は日付だけ、DB項目はタイトルと日付だけ。一覧は新しい日付順のカード、本文は冒頭に本人の振り返り「今日のこと」を置き、区分ごとの背景色と広めの余白で読みます。取り組みごとの短い要点は表示し、具体的な経緯・作業内容はトグルで折りたたみます。短い記録には詳細欄を作りません。感想を話さない日は「今日のこと」を省略できます。出典・記録範囲は末尾の折りたたみに置きます。日誌本文・生ログ・資格情報はGitへ保存しません。

カードの本文プレビューは現在のMCPでは設定できず、有効化は未確認です。Notionの「日誌一覧」→レイアウトで、カードプレビューを「ページコンテンツ」に設定してください。カードサイズもそこで調整できます。

## スキルの管理

- [スキル本体](skills/daily-diary/SKILL.md)
- [資料収集](skills/daily-diary/references/collect.md)
- [位置情報の参照](skills/daily-diary/references/location.md)
- [本文テンプレート](skills/daily-diary/references/diary-format.md)
- [Notion保存](skills/daily-diary/references/notion-save.md)

`skills/daily-diary/` を `~/.codex/skills/daily-diary/` に配置して使います。蓄積資料への接続には `~/.codex/daily-diary.json` のローカル設定を使います。[配置と保存先の接続](docs/collection/setup.md#日記スキルの配置と保存先の接続)を参照してください。コピーで配置した場合、リポジトリ更新は自動反映されません。Langfuseの既存認証と、メール・カレンダー・Computer History・Notionの接続が必要です。取得できない接続は不足として扱います。

## 活動記録の収集

[収集スキル](skills/collect-activity/SKILL.md)は単独での収集にも、日記の資料収集からの取得・保存にも使います。Chromeや既存接続の読取はCodexが行い、Pythonの保存処理は渡された収集結果だけを日別JSONへ保存します。CLIを起動するだけではXやYouTubeを収集しません。

Screen Time・Langfuse・PC活動には、AIを介さず取得・保存する[手動実行スクリプト](docs/collection/script-collectors.md)もあります。設定例・`--check`・[新しいMac向けのScreen Time導入手順](docs/collection/screentime-setup.md)を用意しています。取得元や保存先は利用者が設定します。既存の日記スキルや定期実行への切替は行っていません。

- [導入とScreen Timeの再現](docs/collection/setup.md)
- [手動収集・バックアップ・復旧](docs/collection/operations.md)
- [保存仕様](docs/collection/storage-contract.md)
- [検証結果と未確認事項](docs/collection/verification.md)

Python 3.12以上の標準ライブラリを使います。実データ・個人設定・バックアップはGit管理外のローカル保存先へ置き、外部へ自動転送しません。YouTube Musicは収集しません。

毎朝7時、Mac復帰後の回収、ログイン切れの通知は設計上の方針です。無人実行の接続・定期実行登録は保留しており、収集が自動で動いている状態ではありません。既存Screen Timeの15分収集は変更していません。

検証は `python3 -B -m unittest discover -s tests/collection -p 'test_*.py' -v`。架空データを一時フォルダだけに保存します。

2026-10-13、以前のPythonツール・テスト・旧設計文書を削除し、合意した手順でスキルを作り直しました。旧Notion DBと13日分の日誌はゴミ箱へ移し、新DBは空の状態から開始しています。旧記録は復元していません。

スキル形式、Langfuseの全ページ取得、メール検索、PC活動記録の読み取り、DBのスキーマを確認済みです。模擬ケースでタスク分類、必要に応じた質問、本人の手編集、資料取得失敗と保存結果不明への対応も確認しました。10/1・10/2の日誌は本人の全文確認後に保存し、区分別の背景色・出典・日付を読み戻して確認しました。Notion実画面の余白・カードプレビューは未確認です。定期実行は未設定です。
