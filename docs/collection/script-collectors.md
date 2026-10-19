# スクリプトで活動記録を収集する

Screen Time・Langfuse・PC活動は、AIに取得・JSON作成を任せずPythonで取得して保存できる。2026-10-19時点の仮実装。XとYouTubeは従来の収集スキルを使う。既存の日記スキルの呼出し方法と定期実行は変更していない。このMacでは私有collectors.jsonの追加と3本の `--check` に続き、[実データで取得・保存・元資料照合・再実行](live-verification-2026-10-19.md)を確認した。既存の認証・OS設定は変更していない。

## 1. 必要な環境

- macOS、Python 3.12以上。3本の収集スクリプト自体は標準ライブラリだけを使う。
- Screen Time: 別途用意した収集器の `exports/current.json`。[新しいMacへの導入](screentime-setup.md)を先に行う。
- Langfuse: 本人のCodex Turnが記録されたプロジェクトと、そのプロジェクト用の既存読取認証。
- PC活動: Computer Historyを有効にした環境と、既存のイベント出力。取得スクリプトは観測の開始・設定変更をしない。

取得→共通batch→既存import→日別JSONの流れ。[保存仕様](storage-contract.md)は変更しない。AIが不要なのは取得・整形・保存まで。日記を書くときに全文を読めばその分のトークンは必要になる。

## 2. 保存先を初めて作る

既存のruntimeがある場合はその絶対パスを使い、作り直さない。新規の場合は、リポジトリのルートから次を実行する。パスの例は変更できる。クラウド同期対象外の場所を選ぶ。

```sh
python3 -B - <<'PY'
from pathlib import Path
import json, os
root = Path('.daily-diary-runtime/local').absolute()
root.mkdir(parents=True, exist_ok=False, mode=0o700)
config = {'schema_version': 1, 'backup_generations': 7, 'targets': [
    {'source': 'screentime', 'account': 'self', 'identity': 'local-owner'},
    {'source': 'langfuse', 'account': 'self', 'identity': 'REPLACE_WITH_LANGFUSE_USER_ID'},
    {'source': 'computer-history', 'account': 'self', 'identity': 'local-owner'},
]}
for name, content in [
    ('config.json', json.dumps(config, ensure_ascii=False, indent=2)),
    ('collectors.json', Path('config/collectors.example.json').read_text()),
]:
    with os.fdopen(os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as f:
        f.write(content + '\n')
print(root)
PY
```

未使用の対象は両方の設定から削除してよい。生成した設定には未入力欄があるため、そのままでは収集できない。

## 3. 設定とパスの責任

| 場所 | 設定内容 |
|---|---|
| `<runtime>/config.json` | 既存の保存設定。資料・保存用アカウント別名・本人識別子 |
| `<runtime>/collectors.json` | 新しい取得設定。取得元の場所、対象別名、上限。認証キーは置かない |
| Langfuse認証ファイル | `base_url`, `public_key`, `secret_key`, `user_id`。既存の専用ファイルを参照 |
| `~/.codex/daily-diary.json` | 日記スキル用のrepository/runtime接続。今回のコマンドは自動参照しない |

コマンドの `--root` は必須。`--settings` を省略すると `<runtime>/collectors.json` を読む。設定内のパスは絶対パス・`~/...`・相対パスを指定できる。**相対パスの基準はcollectors.jsonがあるフォルダ**。カレントディレクトリには依存しない。`$HOME` 等の環境変数展開や、別のMacのパスの推測は行わない。コマンド引数の相対パスだけは通常どおり起動ディレクトリ基準になる。

未知の設定キー、未入力のREPLACE値、対象別名や本人識別子の不一致は取得前に拒否する。保存先には既存storeのシンボリックリンク禁止が適用される。読み取り元も所有者を確認するが、共有フォルダから本人の活動だけを自動識別する機能はない。

### Screen Time

`export_root` を、導入時に指定したDBと同じフォルダの `exports` にする。例としてDBが `~/Library/Application Support/screentime-diary/screentime.db` なら、`export_root` は `~/Library/Application Support/screentime-diary/exports`。コードにこの例のパスは埋め込まない。

`identity` はconfig.jsonと同じ `local-owner`。これは「このOSユーザーのローカル資料を使う」という明示的な設定上の対応であり、Apple Accountの本人確認を代行するものではない。Apple Account・端末の照合は導入時に行う。

### Langfuse

`credentials_file` に既存のCodex専用認証ファイルを指定する。設定例の `~/.codex/langfuse.json` は既存利用者向けの例であり、必須の保存場所ではない。初回はそのプロジェクトの認証を私有ファイルへ用意する。認証ファイルの形は以下。実値をGit・シェル引数・チャットへ貼らない。

```json
{
  "base_url": "https://YOUR_LANGFUSE_HOST",
  "public_key": "YOUR_PROJECT_PUBLIC_KEY",
  "secret_key": "YOUR_PROJECT_SECRET_KEY",
  "user_id": "YOUR_LANGFUSE_USER_ID"
}
```

所有者本人だけが読める権限にする（`chmod 600 <認証ファイル>`）。`user_id` をruntime/config.jsonのlangfuse対象のidentityと一致させる。一般プロジェクトのキーや他人のキーへ自動フォールバックしない。環境変数経由だけの認証はこの仮実装では未対応。

HTTPSを使う。HTTPはローカル検証のloopbackホストだけ許可する。認証付きHTTPのリダイレクトは拒否する。`--check` は認証項目・権限・本人IDの設定を検査するだけで、サーバーへ接続しない。

取得は[公式Observations API v2](https://langfuse.com/docs/api-and-data-platform/features/observations-api)の期間・本人・名前・種類のフィルタとcursorを使う。`fields=core,basic,io`、`name=Codex Turn`、`type=AGENT`。取得行でも条件を照合する。self-hosted環境のv2対応や実際に記録される種類は導入先で確認する。APIに反映されるまでの遅延はあり、API全ページの読取成功は未反映データも含めた永久的な完全性の保証ではない。

### PC活動

導入先のCodexでComputer Historyの状態を確認し、`computer_history_status` の **eventStreamRootPath** を `event_stream_root` に設定する。既存の `segments/<segment>/metadata.json` と `events.jsonl` を使う。ホームディレクトリ内を探索したり、アプリのGroup Container名を固定したりしない。設定が不明なら収集を止める。

新しい利用者は先にComputer Historyを利用可能なCodex環境で有効にする。機能がない・停止中なら過去の保存ファイルが存在する範囲だけが対象で、新しい活動は取得できない。状態・観測設定の変更はComputer Historyのツール/UIで行い、設定ファイルを直接編集しない。

`identity` の意味はScreen Timeと同じ。対象ユーザーの出力であることを設定時に確認する。イベントの `timestamp`, `app.name`（なければ`app.bundleIdentifier`）, `kind` を採用する。本文・AXツリー・入力文字列・ウィンドウタイトル・URLはこの仮実装ではコピーしない。出典はセグメント名＋行番号。同じファイルを追記・再読取しても重複保存しないが、過去行の並替え・書換えには未対応。

## 4. 診断してから実行する

同じシェルで、既存runtimeの実際の絶対パスを指定する。以下は手順2で新規作成した場合の例。

```sh
diary_runtime="$(pwd -P)/.daily-diary-runtime/local"
python3 -B -m scripts.activity_collection.collect_screentime --root "$diary_runtime" --check
python3 -B -m scripts.activity_collection.collect_langfuse --root "$diary_runtime" --check
python3 -B -m scripts.activity_collection.collect_computer_history --root "$diary_runtime" --check
```

`ready` はローカル設定・取得元の場所を確認できた状態。HTTP認証の成功、exportのハッシュ整合性、イベント全件の読取、iPhone同期、観測の稼働状態までは診断しない。`--check` ではファイルを作成・更新しない。

対象期間は開始を含み終了を含まない。必ずタイムゾーン付きの過去時刻を指定する。日付は例なので実行したい日に置き換える。Screen Timeは日別集計のため、開始を日本時間の0時にする。

```sh
python3 -B -m scripts.activity_collection.collect_screentime --root "$diary_runtime" \
  --start '2026-10-16T00:00:00+09:00' --end '2026-10-17T00:00:00+09:00'
python3 -B -m scripts.activity_collection.collect_langfuse --root "$diary_runtime" \
  --start '2026-10-16T00:00:00+09:00' --end '2026-10-17T00:00:00+09:00'
python3 -B -m scripts.activity_collection.collect_computer_history --root "$diary_runtime" \
  --start '2026-10-16T00:00:00+09:00' --end '2026-10-17T00:00:00+09:00'
```

別の取得設定なら `--settings /path/to/collectors.json` を追加する。3本は直列に実行する。スケジューラは登録しない。

## 5. 結果と再実行

標準出力は1個のJSONで、会話・イベント本文を出さない。

```json
{
  "source": "langfuse",
  "status": "partial",
  "save_status": "saved",
  "records_read": 20,
  "issues": ["http_failed"],
  "batch_id": "実行ごとのUUID",
  "changed_records": 20,
  "notifications": [],
  "backup": {"status": "created"}
}
```

`records_read` は今回読んで保存処理に渡した記録数。`changed_records` は既存storeが返す新規追加件数であり、既存記録への出力補足・集計更新の件数ではない。`save_status=saved` と取得全体の成功は別。

| 終了コード | 意味 |
|---|---|
| 0 | 全ページ取得・保存成功、または `--check` がready |
| 1 | 部分取得・取得失敗・認証待ち。結果の保存ができていても1 |
| 2 | 引数・設定・読取元に不足。取得前の不足は進捗へ保存しない |
| 3 | 保存ロックの競合。batchを保持 |
| 4 | 保存・復旧の問題。`save_status` とbatchの有無を確認 |

Screen Timeはexportの整合性だけではiCloudの全期間同期を証明できず、PC活動は保持ファイルだけでは観測停止区間を証明できない。そのため今回の仮実装はどちらも `partial`・coverageなし。記録0件でも全件確認済みにしない。

取得後のbatchは `<runtime>/batches/<batch_id>.json` に所有者限定で保持する。入力・AI出力を含み得るのでGitや報告へ貼らない。保存に失敗した場合は同じbatchを保持し、復旧後に既存CLIへ渡す。

```sh
python3 -B -m scripts.activity_collection.cli --root "$diary_runtime" \
  import --input "$diary_runtime/batches/実際のbatch_id.json"
python3 -B -m scripts.activity_collection.cli --root "$diary_runtime" status
```

同じIDと同じ内容の再取込は安全に重複排除される。batchを編集して同じIDを使い回さない。正常終了後のbatchも自動削除しないため、容量・保持方針は利用者が管理する。既存storeのバックアップ対象はrecords/state/configであり、この再試行用batchesと取得設定・認証ファイルは含まれない。

日記用の本文が必要なときだけ既存の `read-day --date YYYY-MM-DD` を使う。このコマンドは本文を返すので、収集結果の確認だけなら `status` を使う。

## 6. 代表的な不足の解決

| issuesのコード | 確認すること |
|---|---|
| `runtime_not_configured` / `settings_missing` | root、config.json、collectors.jsonの場所 |
| `invalid_settings` / `target_not_configured` | キーの綴り、REPLACE欄、sourceとaccountの対応 |
| `identity_mismatch` | ローカル設定の対応、Langfuse user_id。推測で他人に合わせない |
| `credentials_permissions` | 認証ファイルの所有者、0600相当の権限 |
| `export_unavailable` | export_rootと初回collectの実施状況 |
| `export_invalid_or_unreadable` | current.jsonと固定世代の存在・ハッシュ・読取権限 |
| `history_unavailable` | statusが返したroot、segmentsの存在・所有者・権限 |
| `invalid_event` / `invalid_segment_metadata` | イベント形式の変化・壊れた行。読めた記録は部分取得として保存 |
| `event_limit` / `segment_limit` / `page_limit` | 設定上限。期間を狭めるか上限を見直す |
| `login_required` | Langfuseの認証・プロジェクトの読取権限 |
| `recovery_required` | 元ファイルを保持し、[復旧手順](operations.md)を使う |

実機検証の範囲は[検証結果](verification.md)へ記録する。別のMacでのOS権限・iPhone同期の確認は、このリポジトリの架空データテストでは代替できない。
