# Screen Timeを新しいMacで準備する

これは既存環境の修復・上書き手順ではなく、新規利用者のための導入手順。Screen TimeのOS資料を読む収集器と、日記用に既存出力を読むスクリプトは別の処理である。

```text
macOSの保護資料（Biome / KnowledgeC）
  → 専用Python + local_screentime.py collect
  → 設定したDBの隣のexports/current.json
  → collect_screentime
  → 日記用runtimeの日別JSON
```

## 前提と確認範囲

- macOSで実行する。Windows/LinuxにAppleの元資料があるとは扱わない。
- Gitとuvが必要。uvは[公式の導入手順](https://docs.astral.sh/uv/getting-started/installation/)で準備する。Python 3.12.9はuvで選択する。
- 上流は [nichtlegacy/screentimeの固定リビジョン](https://github.com/nichtlegacy/screentime/tree/c25696a3ddac32058ae809f63c04e2bfc0c8dc0f)。2.0.0と、その非公開関数に依存する追加処理を組み合わせる。最新の上流へ無条件に置き換えない。
- ビルド・実行依存は `resources/screentime-local/*-requirements.txt` のバージョンとハッシュに固定する。
- 現在の追加処理はMacとiPhoneの両ソースを検査する。Macだけの場合やiPhone同期がない場合、出力ができてもhealth/collectがpartialになることがある。判定を避けるために設定を偽らない。
- 実行ユーザーの `~/Library` 以下にあるmacOS所定の資料を読む。このOS上の標準位置と、変更可能なアプリの設定・DB・ログ・保存先を区別する。

2026-10-19には一時環境で固定上流・依存のインストールと追加処理のCLI起動を確認した。新しいMacでの保護資料読取・権限・iPhone同期・定期収集の成功は未確認。以下の導入後の確認が必要であり、資材のインストール成功だけで再現完了としない。

## 1. 配置場所を選び、依存を入れる

リポジトリのルートで実行する。以下の配置場所は例であり変更可能。**既存環境がある場合には実行しない**。同名のLaunchAgentを既に使っていれば手順5も登録しない。

```sh
(
  set -eu
  diary_repo="$(pwd -P)"
  sct_prefix="$HOME/.local/share/screentime-diary"
  sct_config="$HOME/.config/screentime-diary/config.toml"
  sct_database="$HOME/Library/Application Support/screentime-diary/screentime.db"
  sct_logs="$HOME/Library/Logs/screentime-diary"
  sct_agent="$sct_prefix/io.github.nichtlegacy.screentime.plist"

  # 新規配置のみ。失敗後の再実行でも、既存ファイルを消して続けない。
  test ! -e "$sct_prefix"
  test ! -e "$sct_config"
  test ! -e "$sct_database"
  umask 077
  mkdir -p "$sct_prefix"
  git clone --no-checkout https://github.com/nichtlegacy/screentime.git "$sct_prefix/source"
  git -C "$sct_prefix/source" checkout --detach c25696a3ddac32058ae809f63c04e2bfc0c8dc0f
  uv venv --python 3.12.9 "$sct_prefix/venv"
  uv pip install --python "$sct_prefix/venv/bin/python" --require-hashes \
    -r "$diary_repo/resources/screentime-local/build-requirements.txt" \
    -r "$diary_repo/resources/screentime-local/runtime-requirements.txt"
  uv pip install --python "$sct_prefix/venv/bin/python" --no-deps --no-build-isolation "$sct_prefix/source"
  python3 -B -m scripts.setup_screentime \
    --prefix "$sct_prefix" --python "$sct_prefix/venv/bin/python" \
    --config "$sct_config" --database "$sct_database" \
    --logs "$sct_logs" --launch-agent "$sct_agent"
)
```

`setup_screentime` は設定ファイル・収集コードのコピー・LaunchAgentのplistを作る。既存の設定・収集コード・plist・DBがあれば拒否する。パスは引数から絶対パスへ変換し、plistには `~` や `__HOME__` を残さない。Pythonや上流のインストール、OSの権限変更、収集の実行、Agent登録はこのスクリプト自身では行わない。

失敗時は出力と既存ファイルを確認し、別の新規配置を選ぶか、失敗した配置だけを確認して処理する。既存環境を削除するコマンドはこの手順に含めない。

上流のソース・依存バージョンを固定して再構築する手順であり、異なるOS/CPUでwheelやPythonのバイナリハッシュまで一致する保証ではない。[来歴情報](../../resources/screentime-local/provenance.json)のwheel/Pythonハッシュは以前のMac上の資材の記録。

## 2. Apple側の共有と読取権限を設定する

1. Macの「システム設定」→「スクリーンタイム」で利用状況の記録を有効にする。
2. iPhone分も使う場合は、同じApple AccountでサインインしたiPhoneの「設定」→「スクリーンタイム」でも記録と「デバイス間で共有」を有効にする。Mac側でも共有を確認する。OSによって名称が異なる場合がある。[Appleの共有に関する案内](https://support.apple.com/guide/mac-help/set-up-screen-time-for-yourself-mchle4e2edfe/mac)を参照する。
3. Macの「システム設定」→「プライバシーとセキュリティ」→「フルディスクアクセス」で、**実際に収集を実行する専用Python**を許可する。追加画面で `⌘⇧G` を使い、選んだ `<prefix>/venv/bin/python` の場所へ移動できる。実体がシンボリックリンクの場合は実行体も確認し、権限変更後に手順3で読取を再検証する。
4. Pythonを作り直した場合は同じパスでも権限を引き継げると仮定しない。ターミナルに権限があることだけでも成功としない。

スクリーンタイム画面で端末と利用日が見えることを確認し、同期を待つ。共有を有効にしても過去の全期間が同期されるとは限らない。権限追加・同期の確認は利用者が行う。スクリプトが設定や権限を自動変更することはない。

## 3. 初回を手動実行する

同じ配置例なら次を使う。別の配置なら両パスを置き換える。別プロジェクトの設定へフォールバックしないよう、常に `--config` を明示する。

```sh
"$HOME/.local/share/screentime-diary/venv/bin/python" -I -B \
  "$HOME/.local/share/screentime-diary/collector/local_screentime.py" health \
  --config "$HOME/.config/screentime-diary/config.toml"

"$HOME/.local/share/screentime-diary/venv/bin/python" -I -B \
  "$HOME/.local/share/screentime-diary/collector/local_screentime.py" collect \
  --config "$HOME/.config/screentime-diary/config.toml"
```

初回collect前のhealthではDBなしのpendingもあり得る。collectはローカルDBとexportsを作成・更新する。設定はネットワーク送信先とlookupを無効にし、追加処理にも通信拒否がある。上流の汎用CLIを代わりに実行しない。

| 結果 | 確認・対応 |
|---|---|
| `ModuleNotFoundError` | 手順1の専用Pythonと依存インストールを確認。汎用Pythonで代用しない |
| `permission_required` / `PermissionError` | 実行体のフルディスクアクセスを確認して再実行 |
| `source_unavailable` | Mac/iPhoneの記録・共有、Apple Account、同期待ちを確認 |
| `pending` | DBの場所を確認。初回collect後も同じか確認 |
| `partial` | `source_errors`, `sources_failed`, `source_warnings`, `current_source_data_ok`, `current_platforms` を確認。読めた分と不足を分ける |
| `export_invalid` | exports/current.jsonと固定世代の欠落・ハッシュ不一致を確認。元データを消して直さない |
| `stale` | 最新の手動/定期収集時刻を確認。Agent未登録なら定期収集されない |
| `failed` + `ValueError` | 指定configの存在、JST、sources、lookup・外部送信先の設定を確認 |

healthの端末別件数・最新日時、`export_integrity_ok`、`network_attempts=0`を確認する。件数だけでは同期完了としない。既知の利用日・端末・アプリが反映されているか、元のスクリーンタイム画面と照合する。healthが含む端末別統計も個人データなので公開ログへ貼らない。

## 4. 日記用の取得設定へ接続する

[スクリプトの設定](script-collectors.md)のcollectors.jsonに、今回の `db_path` と同じフォルダにあるexportsを指定する。この例なら次の値になる。

```json
{
  "account": "self",
  "identity": "local-owner",
  "export_root": "~/Library/Application Support/screentime-diary/exports"
}
```

その後 `collect_screentime --check` → 期間指定で収集 → 既存CLIのstatus/read-dayで保存を確認する。日記用スクリプトはDBを読まず、初回collectやOS設定も実行しない。

## 5. 必要な場合だけ定期収集を登録する

生成したplistはprefix内に置いてあり、この時点では未登録。まず初回の手動結果を確認する。すでに同じLabelのAgentがある場合は以下を実行せず、既存設定との関係を確認する。

```sh
launchctl print "gui/$(id -u)/io.github.nichtlegacy.screentime"
```

未登録であり、定期収集を利用したい場合だけ、配置例に合わせて次を実行する。

```sh
(
  set -eu
  sct_agent_source="$HOME/.local/share/screentime-diary/io.github.nichtlegacy.screentime.plist"
  sct_agent_destination="$HOME/Library/LaunchAgents/io.github.nichtlegacy.screentime.plist"
  test ! -e "$sct_agent_destination"
  mkdir -p "$HOME/Library/LaunchAgents"
  (set -C; cat "$sct_agent_source" > "$sct_agent_destination")
  chmod 600 "$sct_agent_destination"
  launchctl bootstrap "gui/$(id -u)" "$sct_agent_destination"
)
```

15分間隔・ログイン時起動。launchctl printだけで成功とせず、次回実行後のhealthとexport更新を確認する。スリープ中・ログアウト中の連続収集を保証しない。ログは設定したlogs内へ出る。今回の追加処理は別環境の既定ログを回転させない。ログの容量管理は別途必要。

これはOS資料→exportsの定期収集。exports→日記runtimeの取込や日記の自動作成は別途で、登録しない。

## 更新時

既存DB・設定・plist・コードをMac内にバックアップする。新しい場所で準備・手動確認してから切り替える。今回リポジトリ内の追加処理に `--config` を追加し、上流の既定ログ回転呼出しを外した。既に配置済みの収集器は自動更新しない。`provenance.json` のoriginal_collector_sha256は以前の保管コード、collector_sha256は現在のコードを表す。

他のMacで「再現できた」と報告するには、インストールだけでなく、権限・初回collect・既知の利用との照合・日記用保存の再読込を確認する。iPhoneを対象にする場合はその同期確認も含める。
