# Codex会社用日誌 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** `$work-diary` で会社のCodex記録を証跡にして一日を振り返れる、個人版から独立したスキルを作る。

**Architecture:** スキルと参照手順で対話・Notion保存を定義する。同梱のPythonは既存Codex JSONLを読み、期間・会社の作業フォルダで絞った出典付きJSONを標準出力へ返す。Notion・Slack・Computer Historyは接続できた範囲を使う。

**Tech Stack:** Markdown、YAML、Python 3.12以上の標準ライブラリ、unittest。

**Spec:** `docs/superpowers/specs/2026-11-03-work-diary-design.md`

## Global Constraints

- 個人用の `daily-diary` とは別のスキルを用意する。
- 日誌を作るときに対象日の資料を参照する方式Aを使う。
- 初版の資料はCodexのローカル会話記録、Notion、Slack、Computer History。ClaudeとChatGPTの会話履歴は後続対応とする。
- 会社固有の設定はスキルの配布物やGitへ含めない。
- 最新全文への本人の明示的なOK後に会社側のNotionへ保存し、本文と日付・リンクを読み戻して照合する。

## Review Focus

- 以前開始した会話の対象日の追記を読み落とさない。
- 別の作業フォルダ、子エージェント、システム指示を本人の行動にしない。
- 壊れた行・欠落・省略を「活動なし」「全件取得」に変換しない。
- AIの完了報告をテスト結果や本番の効果に読み替えない。
- 会社用保存先がない場合に個人版のNotionへ保存しない。

## Task 1: 出典付きCodex記録の読み取り

**Files:** Create `skills/work-diary/scripts/read_codex_day.py`; Create `tests/collection/test_work_diary_codex.py`.

**Interfaces:** CLIは `--start`・`--end`（タイムゾーン付きISO、開始を含み終了を含まない）、`--workspace-root`（複数指定可、必須）、`--codex-home`、`--offset`、`--limit`、`--max-chars` を受ける。JSONには `status`、`range`、`records`、`issues`、`next_offset` を含め、各記録は時刻・種別・本文・session ID・cwd・元ファイルと物理行番号を持つ。ファイル・ネットワークへの書き込みは行わない。

- [x] 再開会話とJST境界、フォルダ範囲、アーカイブ、本文とツール結果、破損、ページング、省略、読み取り専用のCLIテストを書く。
- [x] `python3 -B -m unittest discover -s tests/collection -p 'test_work_diary_codex.py' -v` を実行し、未実装による失敗を確認する。
- [x] 記録形式の実物に合わせた最小の読み取り処理を実装する。
- [x] 同じテストと既存のcollection全体を実行する。Expected: 全件成功。
- [x] 読み取り処理とテストをコミットする。

## Task 2: 会社用スキルと導入導線

**Files:** Create `skills/work-diary/SKILL.md`, `agents/openai.yaml`, `references/sources.md`, `references/notion-save.md`; Modify `README.md`.

**Interfaces:** 会社側のローカル設定は `$CODEX_HOME/work-diary.json` の `workspace_roots` と `notion_destination`（URL）を中心に、必要ならSlack・Notionの本人IDと検索範囲を記す。読み取りスクリプトはTask 1のCLIで呼ぶ。設定がなければ初回に会社側で指定し、ここでは個人の接続や保存先を複製しない。

- [x] スキルなしの模擬ケースで、主な出来事の紹介前に質問し、本人記入用の固定欄を出す振る舞いを確認済み。対話の入口と任意項目の扱いを明記する。
- [x] 会社用資料・自然な振り返り・出典・保存後照合を、個人用のIDや実行パスを含めずに記述する。
- [x] READMEに呼出し方、会社PCへフォルダをコピーする方法、初回設定、完了確認を追記する。
- [x] `quick_validate.py`、リンク解決、架空資料による独立した行動確認を行う。Expected: 有効なスキル、本人の回答欄を強制せず、根拠と未確認を区別する。
- [x] 実ログを読み取り専用で参照し、時刻・出典行を元ファイルと照合する。原文は検証記録・Gitへ保存しない。
- [ ] レビュー後、検証済みスキルをローカルのskillsへ新規配置し、ファイルを読み戻して一致を確認する。会社環境の取得・Notion保存は未確認として報告する。

## 検証記録

開始時の既存collectionテスト: 81件成功。実装と動作確認の結果は完了時に追記する。

- 自動テスト: 94件成功（既存81件、新規13件）。
- スキル形式と参照リンク: 検証成功。
- 架空資料での独立した行動確認: 取り組み紹介後に自然な一問、テスト失敗を解消済みにしない、未指定の保存先へ書かないことを確認。
- このMacの実ログ: 出典行・日時・本文の照合とページングを確認。旧形式で時刻・作業フォルダを確認できないセッションは取得不足として明示する。
- 独立レビューのImportant 3件: 日付をまたぐ重複判定、自動投入文の混入、本文破損の無警告欠落を、再現テストの失敗後に修正。reader 13件とcollection全体94件が成功。
