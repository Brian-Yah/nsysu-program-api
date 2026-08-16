# Human review workflow

## 快速審核台

在 repository 根目錄執行：

```powershell
python scripts/review_dashboard.py
```

本機頁面會並排顯示官方 PDF、結構化規則、課程清單與 conflict 候選值。審核紀錄寫入
`data/review-decisions/115-1/`（本機工作檔，已由 gitignore 排除），並固定來源 PDF/text hash、
parser 版本與所選 PDF 版本。來源改變時舊紀錄會顯示為過期。

- `A`：基本正確。
- `C`：基本正確但小心使用；適合規則可暫用、但官方文字或選項數量仍有 conflict 的資料，必須寫備註。
- `F`：需要修正，必須寫備註。
- `S`：略過。

「基本正確但小心使用」不會解決 source conflict，也不會產生正式 approved override。
它只是第一階段人工判斷；正式 `approved` 仍須以下完整 replacement 與雙人覆核流程。

全部完成且沒有過期紀錄後，可輸出可提交的來源鎖定稽核快照：

```powershell
python scripts/review_dashboard.py --academic-version 115-1 --export-results
```

結果寫入 `reports/manual-review-results-115-1.json`。此報告保存第一階段判斷，仍不等同
雙人覆核的正式 `approved`。

1. 開啟 `reports/initial-115-1.json` 與 `reports/semantic-diff.json`，先處理下載／擷取失敗及 OCR 待辦。
2. 對照 `data/extracted/115-1/{program_id}.json` 的 raw text 與官方 PDF；記錄頁碼、表格、總學分、群組、跨系院、替代、上限、重複採計與核准條件。
3. 課程以課號為主。課名只輔助；更名、舊課號放 aliases，沒有正式依據的跨版本認列只能列 recognition candidate 或 `manual_review`。
4. 將完整 replacement 寫入 `data/reviewed/115-1/{program_id}.json`，`based_on` 必須包含 `pdf_binary_sha256`、`normalized_text_sha256`、`selected_pdf_academic_version`、`parser_version` 與 extracted `course_catalog_sha256`。不可直接修改會被 build 重寫的 `data/published`。
5. 執行 tests、schema validation、build；PR 附 semantic diff。不要把各版本課程任意合併成校方未核准的拼裝規則。

Reviewer checklist：總學分可由群組規則解釋；min/max 不矛盾；課號唯一或明確 aliases；課程未重複採計；學生系所差異有條件分支；召集人核准與無法自動判斷項目均保留 `manual_review`。

Build 採 `source → extracted → reviewed full replacement → published → api`。任一來源 hash 或所選 PDF 版本與 reviewed 記錄不符時必須 fail closed；不得 deep-merge constraint arrays，也不得靜默忽略 stale override。
