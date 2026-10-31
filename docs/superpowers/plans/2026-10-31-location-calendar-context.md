# 位置情報とカレンダーの日記参照 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 日記の都度、既存の位置情報CSVと本人の予定を参考に主な出来事を拾い、本人の感情を聞いて残す。

**Architecture:** 日記スキルに参照手順を追加する。標準ライブラリの読み取り例は同梱の小さなスクリプトとして再利用し、指定期間の行と欠落だけを返す。施設の判定や定期保存は行わず、地図の補足と対話はスキルが判断する。

**Tech Stack:** Python 3.12+ 標準ライブラリ、Markdown、unittest、既存のGoogle Calendar接続。

**Spec:** `docs/superpowers/specs/2026-10-31-location-calendar-context-design.md`（本人承認済み）。

## Global Constraints

- 方式1。位置情報とカレンダーを定期収集先や日別JSONへ複製しない。
- 任意の `location_csv` は既存CSVの絶対パス。既存の `repository`・`runtime` を維持する。
- 対象期間は日本時間の半開区間。代表座標や予定だけで行動・参加・感情を確定しない。
- 感情は本人の言葉から拾う。最新全文への明示承認後にNotion保存する。
- 実データ・個人設定・試行画像をGitへ入れない。追加の地図API契約・常駐処理は作らない。

## Review Focus

- 終了不明・長さゼロ・境界を跨ぐ行を、一日中の滞在へ拡張しない。
- 不正な日時・座標を含む行でも、読めた他の行と欠落を区別する。
- 同じ施設名・重なる滞在と移動を、時間や施設利用の確定情報へ変えない。
- 地図取得不可・カレンダー部分取得時も、本人との振り返りを継続できる。
- 配置先がコピーでも、同梱スクリプトがworktreeや他のPythonパッケージに依存せず動く。

---

### Task 1: 参考資料の読取・振り返り・配置をつなぐ

**Files:**
- Create: `skills/daily-diary/scripts/read_location.py`, `skills/daily-diary/references/location.md`
- Modify: `skills/daily-diary/SKILL.md`, `skills/daily-diary/references/collect.md`, `skills/daily-diary/references/diary-format.md`
- Modify: `README.md`, `docs/collection/setup.md`, `docs/collection/verification.md`
- Modify: 設計書の承認状態、`tests/fixtures/collection/diary-cases.md`
- Test: `tests/collection/test_location_reference.py`

**Interfaces:**
- Consumes: UTF-8/BOM CSV (`record_type,start_time,end_time,title,latitude,longitude,transport_mode`)、設定の `location_csv`、タイムゾーン付き `--start` / `--end`。
- Produces: `read_location(path: Path, start: datetime, end: datetime) -> dict`。JSONは `status` (read/partial/unavailable)、`records`、行番号付き `issues`。`records` は期間と重なる行の原時刻をJSTに換算し、終了不明をnullのまま返す。時間の合算・施設判定をしない。
- CLI: `python3 -B <skill>/scripts/read_location.py --config <local-config> --start <ISO> --end <ISO>`。引数不正は2、設定・入力不可は1、read/partialは0。ファイル・設定を書かず、ネットワークへ接続しない。

- [x] **Step 1: 旧スキルの基準確認と読取テストを先に作る**
  架空資料だけの通常の日記依頼を独立エージェントへ渡し、位置情報を拾えるか観察する。実データ・外部サービス・書き込みは使わせない。読取テストは、UTC→JST、半開境界、日跨ぎ、終了不明、長さゼロ、重なるplace/move、壊れた行と有効行、座標欠落、不正設定・範囲、ファイル無変更を実際の入力・CLIで確認する。
- [x] **Step 2: 新しい読取テストが機能不足で失敗することを確認**
  Run: `python3 -B -m unittest discover -s tests/collection -p test_location_reference.py -v`
  Expected: 新しい読み取り例が未実装のため失敗。
- [x] **Step 3: 読み取り例と参照手順を追加**
  日時をawareで検証し、ファイル行番号を保持。日時不正の行は除外・partial、座標不正は座標をnullにして行を保持・partial、移動の座標空欄は正常。終了不明は対象期間内に開始した行だけを返す。カレンダーはprimary・明示期間・全ページとし、地図照合は必要な候補のみ。本文への感情補完や地点ごとの質問を増やさない。
- [x] **Step 4: GREENと受入ケースを確認**
  Run: `python3 -B -m unittest discover -s tests/collection -p 'test_*.py' -v`
  Expected: 既存72件と追加ケースがすべて成功。
  更新スキルで架空ケースを独立確認し、位置情報の参照、施設名の不確かさ、予定と実施の区別、感情未確認、資料不足時の継続を確認する。スキル形式検証と `git diff --check` も成功すること。
- [x] **Step 5: 検証結果と承認済み設計を記録してコミット**
  個人設定や実資料を含めず、対象ファイルのみコミット。task-doneに上記の全体テストを渡して完了記録を残す。

## レビュー後の配置

独立した全変更レビューの重大な問題を解消してから、配置済みスキルと既存設定を所有者限定の退避先へコピーする。`location_csv` だけを実在するiCloud CSVの絶対パスへ追加し、他のキーを保持する。スキルはworktreeへのリンクにせずコピーし、全ファイルの一致と配置先からの実行を確認する。実資料は対象日だけ読み、元CSV・runtimeを変更していないことを照合する。元チェックアウトへmerge/pushはせず、作業用ブランチを保持する。

## 実行上の判断

本人の「ワークツリーを切って作業を進める」という指示に従い、このセッションで順次実装する。承認済みの設計を変えない計画の再確認では停止しない。読み取り例を同梱スクリプトとするのは、配置後の再利用と日付境界の実行検証を可能にする実装上の選択。
