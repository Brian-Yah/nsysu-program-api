# Development and operations

## Pipeline

`fetch` 動態解析四張學程表；`full` 再下載公開 PDF、計算 binary hash、用 pypdf 原生取字、對低品質文件標記 OCR 待辦、產生 extracted candidates、報告及 static API。OCR optional dependencies 存在，但排程不會因沒有 OCR 或模型 key 失敗。

PDF 文字與表格擷取套件使用精確版本 pin，且每筆來源保存 `extractor_versions`。更新 pypdf 或 pdfplumber 時必須另開資料維護 PR、全量比較 binary/text hash，避免把工具輸出差異誤報為官方資料異動。

Parser 會把「至多採認 N 科」「N 擇一」「至少擇一」「任選 N 門」「每一學程科目僅採計一門」「學程科目應選 N 門」及「不得重複計入」轉成對應 constraints，並處理備註欄、合併儲存格、跨頁欄數變化與表格 entry 別名。「至少擇一」只能產生 `min_entries: 1`，不得自行推導上限；無正式上限時 `max_entries` 必須為 `null`。修改這段邏輯時須以 `extract-cache` 對全部 PDF 重抽，確認 constraint 所列課名、entry ID 與學程科目都存在於同版 `course_catalog`，且不得僅手改發布 JSON。

全量重抽後還須掃描所有 selected PDF version 的規則關鍵字，確認沒有明文規則缺少 constraint；`compound_rows_needing_review` 必須逐列檢視。`option_count_matches: false` 可以保留，但必須是官方宣告數與實際表列數的真實差異。

```bash
python -m nsysu_program_api.cli --root . --academic-version 115-1 fetch
python -m nsysu_program_api.cli --root . --academic-version 115-1 full
python -m nsysu_program_api.cli --root . build
python -m nsysu_program_api.cli --root . diff old.json new.json --output reports/diff.json
python -m nsysu_program_api.cli --root . --entry-year 115 graduation-fetch
python -m nsysu_program_api.cli --root . --entry-year 115 graduation-build
```

先比較 URL、binary hash、normalized text hash，再審閱 extracted course/rule 差異。semantic diff 區分 catalog metadata 與內容 hash；PDF binary 變但 normalized text 不變時可判為版面／metadata 變動候選，仍保留證據。

`graduation-fetch` 從公開必修科目表枚舉 `B` 開頭的學士班代碼，逐系讀取最低畢業學分；0 學分及該年度無表的舊制代碼只進 unavailable 清單。`graduation-build` 產生年度、latest、全量及單系端點，並清除單系目錄中已失效的舊 JSON。

## GitHub Actions and Pages

CI 僅需 `contents: read`。排程也只有 read 權限並上傳 artifact，不自動發布、開 Issue 或寫 branch，避免未審核內容進 main。maintainer 下載 artifact、審閱後另開 PR。若希望自動開 Issue，應新增獨立 job，限定 `issues: write` 且只傳 diff 摘要。

Pages workflow 只接受手動觸發，具 `pages: write` 與 `id-token: write`。在 repository Settings → Pages 將 Source 設為 GitHub Actions，再手動執行。

## AI structured output

未審核 AI 不會進 published。新增 provider adapter 時須：以環境變數讀 provider、model、key；把 program schema 的 rule 子結構作 structured-output JSON Schema；本地再次驗證；保存模型名稱、prompt/parser version、信心與 warnings；輸出只進 `data/extracted`。reviewer 需逐條比對 PDF 才能升格。
