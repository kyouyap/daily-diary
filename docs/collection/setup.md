# 導入・同期・再現の手順

この文書は将来の導入用。今回、既存環境の再設定・再インストール・権限変更・定期実行登録は行っていない。

2026-10-19追記: 新規利用者は[Screen Timeの導入・権限・初回確認](screentime-setup.md)と[3資料のスクリプト設定・実行](script-collectors.md)を参照する。以下の既存Macの配置は確認時点の記録であり、新しい利用者に必須の絶対パスではない。

## 活動記録の保存処理

Python 3.12以上の標準ライブラリを使う。追加の有料APIやAI APIキーは不要。リポジトリのルートから実行する。macOS上でのローカルファイルのロックを使うためWindows向けではない。

導入時に `.daily-diary-runtime/` を所有者限定のフォルダとして作り、[設定例](../../config/collection.example.json)をconfig.jsonへコピーして本人の対象へ置き換える。新規導入時の手順。既存の実設定がある場合はそれを使い、作り直さない。Xの4対象は4行にし、実ハンドル・本人IDと保存別名を対応させる。YouTubeは承認済み1対象。Langfuseは既存の本人ID、PC記録とScreen Timeはローカル所有者の識別子を設定する。認証キーは入れない。

runtimeはGit管理外で、クラウド同期対象でないローカル保存先を使う。設定例のREPLACE値を残したまま収集せず、ブラウザで見える識別子と照合する。実データをシェル引数・Git・公開診断へ貼らない。日記スキルへの接続は次節を参照する。

## 日記スキルの配置と保存先の接続

`skills/daily-diary/` を `~/.codex/skills/daily-diary/` へ配置する。既存配置があればMac内へ退避してから更新する。収集スキルは `~/.codex/skills/collect-activity` からリポジトリの `skills/collect-activity` へのシンボリックリンクで配置する。リンク先は残る通常のチェックアウトを使い、削除予定の作業用worktreeを指定しない。収集スキルのリポジトリ文書への相対参照はリンク実体を基準に解決する。

次の形式の `~/.codex/daily-diary.json` を所有者だけが読めるファイルとして用意する。両パスは既存の絶対パスへ置き換え、実設定はGitへ入れない。runtimeはconfig.jsonと保存済み記録のある場所を指定し、検証用のコピーや復元先を混ぜない。

```json
{
  "repository": "/absolute/path/to/daily-diary",
  "runtime": "/absolute/path/to/private-runtime"
}
```

この接続設定を置くだけでは収集は始まらない。「日記を書いて」では保存済み記録と取得状況を先に確認し、収集スキルを対象日だけの取得・保存として使ってから読み直す。「保存済み資料だけで」の指定があれば追加取得を省く。定期実行は登録・再開しない。導入後はリポジトリ外を作業ディレクトリにして、配置済みスキルの説明だけで対象日のread-dayへ辿り、部分取得も認識できるか確認する。Notionへは本文の承認後に保存する。

## 位置情報CSVの接続

既存のiCloud位置情報CSVを日記の参考資料にする場合、同じ `~/.codex/daily-diary.json` に任意の `location_csv` を追加する。実在するCSVの絶対パスを指定し、既存の `repository`・`runtime` と他の設定値は保持する。位置情報を使わない場合はキーを省略できる。

```json
{
  "repository": "/absolute/path/to/daily-diary",
  "runtime": "/absolute/path/to/private-runtime",
  "location_csv": "/absolute/path/to/iCloud Drive/location.csv"
}
```

設定変更は導入時の操作。通常の日記作成では未設定ならそのまま続け、CSVや空の保存先を自動作成しない。iCloud側の書き出しと同期は別途必要で、この設定は記録・同期を開始しない。後日の日記で使えるのは、その時点のCSVに残る記録に限られる。

日記スキルを更新するときは `scripts/` も含めてコピーする。[位置情報の参照](../../skills/daily-diary/references/location.md)の読み取り例はPython 3.12以上の標準ライブラリだけで動き、作業用worktreeへのリンクや収集用runtimeへの保存は不要。コピー後に参照文書と同梱スクリプトの一致を照合する。資料の状態と行番号付きの対象記録を読み、施設名・代表座標だけで行動や感情を確定しない。

カレンダーは日記作成時に接続済みAPIで本人の `primary` を読む。共有・祝日等は本人が対象に指定した場合に限って使う。今回の導入では権限や予定の編集、定期複製を設定しない。

## Screen Timeで確認した構成

- 上流: nichtlegacy/screentime、2.0.0、固定リビジョンは[来歴](../../resources/screentime-local/provenance.json)に記録。
- 実行用Python: `~/.local/share/screentime-local/app/bin/python`（3.12.9）。DB読取の権限は実行ファイルに依存し、汎用Pythonでの成功を前提にしない。
- 追加処理: `~/.local/share/screentime-local/collector/local_screentime.py`。2026-10-17時点では[保管ソース](../../resources/screentime-local/local_screentime.py)と同一。2026-10-19に保管ソースへ明示的な設定パス指定などを追加したが、配置済みコードは未更新。上流MITライセンスも保存。
- 設定: `~/.config/screentime/config.toml`。timezone=Asia/Tokyo、db_pathは `~/Library/Application Support/screentime/screentime.db`、sources.biome=true、sources.knowledgec=true、lookup.enabled=false。
- Agent: `~/Library/LaunchAgents/io.github.nichtlegacy.screentime.plist`。StartInterval=900、RunAtLoad=true、TZ=Asia/Tokyo、専用Pythonの `-I -B` から追加処理のcollectを呼ぶ。ログは `~/Library/Logs/screentime`、umask=077。
- local_sessionsはビュー。遅延同期されたfocus遷移をlocal_focus_transitionsへ保持して再構築し、local_event_lifecycleで訂正を整理する。
- exports/current.jsonは固定世代とJSON/CSVのハッシュを指す。元の初回出力と直前世代を保持する構成。

## 将来同じ環境を構築する順序

1. データと進捗をバックアップし、既存導入がある場合は上書きしない。上流の固定リビジョンとMITライセンスを確認する。
2. 3.12.9の独立環境を用意し、固定上流からwheelを作る。保管したbuild/runtime requirementsのバージョンとハッシュを使う。作成wheel・Python実行体の記録ハッシュと[来歴](../../resources/screentime-local/provenance.json)を比較する。ビルド成果物が違う場合、同一バイナリを再現したとは扱わない。
3. 上流の `screentime.sources.biome` の非公開関数等へ依存するため、追加処理と上流の組合せを架空データで確認する。アップストリームを無条件に最新へ上げない。
4. 設定ファイルを作り、lookup・Influx・Home Assistant等の送信先を無効にする。追加処理にはネットワーク拒否と環境変数上書きの除去がある。追加処理を通さず上流CLIで収集しない。
5. iPhoneとMacで同じApple Accountを使用し、両方のスクリーンタイムを有効にして「デバイス間で共有」を確認する。iPhoneの使用がMac側で見えるまで待ち、端末名・対象日を照合する。この設定は今回実機確認していない。
6. macOSのプライバシー設定で、実際の専用Pythonに必要な保護データの読取権限を与える。別のPythonやターミナルに与えた権限で代用できると考えない。現在の権限一覧は未検証。
7. 本人が許可した一回の収集でSQLiteのlocal_sessions、エクスポート世代、CSV/JSONのハッシュ、ソース別警告とネットワーク試行数を確認する。生ログを外部へ貼らず集計と状態を確認する。
8. その後に専用Agentの例を実際の絶対パスへ展開し、900秒間隔とログイン時起動を登録する。現在のAgentは変更しない。このAgentは新しい活動収集の毎朝7時とは別物。

インストール資材の元一式は導入したMacに残るが、wheel・依存ライブラリ・Python本体はGitへ複製しない。来歴とコードの保管は、iPhone同期や新しいMacでの再導入に成功したことを意味しない。
