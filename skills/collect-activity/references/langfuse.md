# 本人のAI会話

既存の[資料収集のLangfuse節](../../daily-diary/references/collect.md)にあるCodex専用接続を使う。既存の認証情報をプロセス内だけで使い、キー・Authorization・Cookieをbatchや診断へ入れない。別プロジェクトの一般キーで代用しない。

JSTのrequested_rangeをUTCに変換し、Observations v2へ name=Codex Turn、type=AGENT、fields=core,basic,io、userId=設定済み本人IDを付ける。全sessionを対象に、meta.cursorがなくなるまで読む。APIの現行応答形は利用時に公式資料で確認し、userIdを照合できなければ本人分と断定しない。

`read_observation_pages(fetch_page, user_id, max_pages=100)` は認証済みの読取関数を受け、本人ID・ID・重複・カーソル循環を検査する補助。認証・HTTP・スケジュールを実装してはいない。例外・カーソル循環・上限はpartial/failedで返る。途中まで読めた分を残し、終了していない要求にsuccessを付けない。

kind=conversation、external_id=observation ID。dataはuser_id、observation_id、trace_id、session_id、input（本人）、output（AI）を分離。全履歴を含む繰返し入力を当日の本人発言へ重複計上しない。後着出力は同じIDへ補足する。日跨ぎturnは開始日時と本文中の実施日時を混同しない。
