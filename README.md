# nsysu-program-api

國立中山大學學程資料的非官方、可版本化靜態 JSON API。專案抓取教發中心公開總表與公開 PDF，保存來源雜湊、保守擷取規則候選，經人工審核後才可將規則標為 `approved`。

> **重要聲明：本專案不是國立中山大學官方服務。**資料來自校方公開頁面與文件；畢業與學程認定仍以校方審查為準。使用者應逐筆查看官方來源、academic version 與 review status。程式碼採 MIT License，不表示校方原始 PDF 或其內容採相同授權。

## 快速開始

需要 Python 3.11+。本機執行：

```bash
python -m venv .venv
. .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q && ruff check .
python -m nsysu_program_api.cli --root . --academic-version 115-1 full
python scripts/validate.py
```

設定 `NSYSU_API_USER_AGENT` 為含 repository 或聯絡方式的識別字串。下載器有 timeout、重試、指數 backoff 及請求間隔；PDF 只存於被 Git 忽略的 content-addressed `cache/`。

## API

公開入口位於 `api/v1/`：

- `manifest.json`：schema 與最新 academic version。
- `latest/programs.json`：方便瀏覽的最新資料；正式整合不應盲目追隨。
- `semesters/115-1/programs.json`：固定學期完整 catalog。
- `semesters/115-1/program-index.json`：精簡索引。
- `programs/{program_id}/index.json`：單一學程與版本清單。
- `programs/{program_id}/versions/115-1.json`：指定學程版本。
- `schemas/program.schema.json`：JSON Schema。

```bash
curl -fsSL https://brian-yah.github.io/nsysu-program-api/api/v1/manifest.json
curl -fsSL https://brian-yah.github.io/nsysu-program-api/api/v1/semesters/115-1/programs.json
```

回應 envelope：

```json
{
  "schema_version": "1.0",
  "academic_version": "115-1",
  "data_revision": 1,
  "retrieved_at": "2026-08-15T00:00:00Z",
  "programs": []
}
```

`source`、`extracted`、`published` 分層保存。機器或 AI 擷取結果只進入 `extracted`；沒有審核紀錄不得改成 `approved`。未設定模型 API key 時，抓取、PDF 擷取、hash、diff、validation 與待辦清單仍可完整執行。AI provider/model 預留由環境變數或後續 adapter 設定，secret 不進 Git。

## 版本與 consumer 整合

`schema_version` 管 API 結構，`academic_version` 使用 `115-1`／`115-2`／`115-S`，`data_revision` 表示同學期資料修正。新學期新增目錄，絕不覆寫舊版。`program-id-registry.json` 是更名時維持 ID 的人工 registry；更名前先把新名稱 mapping 到舊 ID。

ClearGrad 或其他 consumer 應固定 academic version 或 release、檢查 manifest/schema version、在自己的 CDN 或本地快取，且先審核差異再更新。`latest` 不是核准訊號。遇到 `needs_review`，UI 應顯示「需人工確認」，不得宣稱學生不符合。

## 完成度 evaluator

`evaluator.py` 支援 `all_of`、`any_of`、`course_set`、aliases、已修／修課中／缺少、不重複採計及 `manual_review` 傳播。Schema 亦保留最低／最高學分、至少門數、跨系院學分、學生系所條件與召集人核准欄位。這是規則模型的參考實作，不取代校方判定。

詳見 [DATA_SOURCES.md](DATA_SOURCES.md)、[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)、[docs/REVIEW.md](docs/REVIEW.md)、[docs/SCHEMA.md](docs/SCHEMA.md) 與 [DISCLAIMER.md](DISCLAIMER.md)。
