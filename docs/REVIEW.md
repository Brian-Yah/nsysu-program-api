# Human review workflow

1. 開啟 `reports/initial-115-1.json` 與 `reports/semantic-diff.json`，先處理下載／擷取失敗及 OCR 待辦。
2. 對照 `data/extracted/115-1/{program_id}.json` 的 raw text 與官方 PDF；記錄頁碼、表格、總學分、群組、跨系院、替代、上限、重複採計與核准條件。
3. 課程以課號為主。課名只輔助；更名、舊課號放 aliases，沒有正式依據的跨版本認列只能列 recognition candidate 或 `manual_review`。
4. 將 reviewed rule 寫入 `data/published/115-1/{program_id}.json`，保留 source hash；只有完整且第二人覆核者可設 `review_status: approved`。
5. 執行 tests、schema validation、build；PR 附 semantic diff。不要把各版本課程任意合併成校方未核准的拼裝規則。

Reviewer checklist：總學分可由群組規則解釋；min/max 不矛盾；課號唯一或明確 aliases；課程未重複採計；學生系所差異有條件分支；召集人核准與無法自動判斷項目均保留 `manual_review`。

