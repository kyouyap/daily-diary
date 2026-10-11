# ローカル実装と検証の記録

2026-10-09。専用作業場所の `daily-diary-local` ブランチで実装した。既存の他プロジェクトは変更していない。

## 実装済み

- `core.py`: JST 暦日、本文 bytes と SHA-256、版への承認、原子的な状態更新、日別ロック、専用場所と symlink の検査。
- `collect.py`: 対象 project/user/session の限定、期間・ページング、重複除外、日跨ぎ・欠落・遅延、必要な資料だけの除外処理。生ログは永続保存しない。
- `cli.py`: 合成資料の prepare、質問・全文の present、表示結果の ack、本人回答の answer、日付・表示 ID・版・hash に結ぶ approve、show/status。
- `git_save.py`: ローカル bare repo 専用の save-local。手編集、競合、Git の危険な設定を止め、承認 bytes 一ファイルだけを commit。push の応答不明は同じ commit と remote blob で照合する。
- `tests/`: 実在の人・秘密・実ログを含まない資料と一時ローカル Git のテスト。外部通信なし。

## 検証

実装時に各タスクの RED（未実装・不足機能）を確認し、その後 GREEN を確認した。CLI の保存失敗が成功 exit を返すテストは実際に FAIL し、exit 2 へ修正して PASS した。

`python3 -m unittest discover -s tests -v`: 最終 36 件 PASS（修正前 32 件）。`git diff --check`: 問題なし。

CLI を別プロセスで呼び、合成取得→一問→文字化された架空の音声回答→全文表示→表示確認→現在の本人 OK の確認宣言→ローカル bare 保存を検証。表示 bytes の SHA-256 と remote blob が一致した。

## 独立レビューと修正

独立した reviewer が baseline `620ac86`〜実装 `d02ea25` の全変更を一度レビューした。Critical 1 件、Important 2 件、Minor 0 件。合成資料と一時 Git だけで再現した。

- Critical: checkout `.git/refs` または bare `refs` の symlink、`core.worktree` の変更により、runtime 外へ Git が書き込めた。Git metadata 全体の symlink、設定と実効 worktree/common directory を事前検査するよう修正。外側ファイルが変わらない回帰試験を追加した。
- Important: `{"day":"2026-10-09"}` のような欠けた状態を既定値で復元していた。全永続フィールドを必須とし、型、許可値、承認との整合を検証するよう修正。破損ファイルを書き換えず停止することを検証した。
- Important: 別人の記録や falsey な不正カーソルが取得完了と扱われた。scope 不一致を省略警告にし、終端は `None` のみとする。部分取得の回帰試験を追加した。

追加した 4 テストが修正前に FAIL する RED を確認し、一回の修正パス後に全 36 件 GREEN を確認した。修正後は実装者による試験で確認し、二度目の独立レビューは行っていない。延期した Minor 指摘はない。

レビュー対象外: live Langfuse、本番 dot 配信・返信の本人性、実日記・GitHub 保存、認証・tracing・定期設定。これらは今回許可されたローカル範囲に含まれない。

## 実装時の判断と制限

1. 本番接続ゲートの不足は合成試験を止めない。本番 owner/repo/private/権限を確認前に使うと誤送信になるため、save はローカル bare 専用に制限した。
2. 日次の原文選別を本人に要求しない。安全に扱えない本文は除外・未確認とし、情報欠落を取得状況へ表示する。非定型の機密・人名の完全検出は保証しない。
3. 承認日時を状態だけに保持する。承認後の本文を変更しないため、日記本文には承認日時を自動追記しない。
4. live Langfuse fetch adapter は今回追加しない。過去の既存接続 HTTP 200 確認と、この helper の本番接続は別。本番 adapter と対象 session の確定は次段階。
5. Task 6 の実資料による日記試行と定期設定は延期し、合成 CLI end-to-end だけを実行した。本番の文章品質、負担 2〜3 分、日次起動は未検証。

## 次段階の最小確認

- 保存する GitHub owner/repo、private・閲覧者、作成・書込の許可。
- 日次時刻（21:30 JST は提案中）と実行ホスト・通知経路。
- 本番 runtime の保持・バックアップ範囲、Langfuse 対象本人/session の対応。

実 GitHub repository 作成・push、実日記保存、定期設定、新しい認証・権限、tracing 変更は今回行っていない。新しい LLM API・課金・依存・Web アプリ・専用 DB・常駐サーバーも追加していない。ブランチと専用作業場所をそのまま保持する。
