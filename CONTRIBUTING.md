# Contributing

歡迎修正 parser、測試、文件及經查證的資料。請勿提交學生資料、Cookie、token、API key、需要登入取得的內容或校方原始 PDF。

資料 PR 應：

1. 指明官方來源 URL、academic version 與取得日期。
2. 保留既有歷史版本；同學期修正增加 `data_revision`。
3. 附 semantic diff 及 PDF/text hash。
4. 對不確定內容使用 `manual_review`，不得猜測。
5. 提供可核對的頁碼／文字證據，由另一位 reviewer 核准後才設為 `approved`。
6. 執行 `ruff check .`、`pytest -q`、static API build 與 `python scripts/validate.py`。

更名時不要產生新 ID：先更新 `data/program-id-registry.json`，將新名稱 key 指向舊 `program_id`，並在 `previous_names` 保存舊名稱。真正新增的學程才建立新 ID。

