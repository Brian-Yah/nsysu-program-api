# nsysu-program-api

國立中山大學學程與各學系最低畢業學分的非官方、可版本化靜態 JSON API。專案抓取校方公開總表、PDF 與必修科目表，保存來源雜湊；學程規則經人工審核後才可標為 `approved`。

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
- `graduation-requirements/index.json`：最低畢業學分資料集索引。
- `graduation-requirements/latest/bachelor.json`：最新入學年度全部學士班。
- `graduation-requirements/115/bachelor/{department_code}.json`：指定年度與系所。
- `schemas/graduation-requirement.schema.json`：單一系所畢業學分 Schema。

```bash
curl -fsSL https://brian-yah.github.io/nsysu-program-api/api/v1/manifest.json
curl -fsSL https://brian-yah.github.io/nsysu-program-api/api/v1/semesters/115-1/programs.json
curl -fsSL https://brian-yah.github.io/nsysu-program-api/api/v1/graduation-requirements/115/bachelor/B4020.json
```

回應 envelope：

```json
{
  "schema_version": "1.2",
  "academic_version": "115-1",
  "data_revision": 6,
  "retrieved_at": "2026-08-15T00:00:00Z",
  "programs": []
}
```

`source`、`extracted`、`reviewed`、`published` 分層保存。機器或 AI 擷取結果只進入 `extracted`；人工修正寫入 `data/reviewed/{academic_version}/{program_id}.json`，並鎖定 PDF binary hash、文字 hash、所選 PDF 版本、parser version 與 course catalog hash。任一值不符時 build 會中止，避免新版文件或解析器誤套舊規則；`published` 是建置產物，不可直接編輯。只有完整、第一與第二位不同 reviewer 都覆核的紀錄可改成 `approved`。

## 版本與 consumer 整合

`schema_version` 管 API 結構，`academic_version` 使用 `115-1`／`115-2`／`115-S`，`data_revision` 表示同學期資料修正。新學期新增目錄，絕不覆寫舊版。`program-id-registry.json` 是更名時維持 ID 的人工 registry；更名前先把新名稱 mapping 到舊 ID。

ClearGrad 或其他 consumer 應固定 academic version 或 release、檢查 manifest/schema version、在自己的 CDN 或本地快取，且先審核差異再更新。`latest` 不是核准訊號。`review_status` 的 `ai_approved`（UI 可顯示 `AI-Approved`）只用於沒有任選、互斥、上限、溢出、人工條件或衝突的普通學程，且候選集合與 3 份隨機 PDF 抽查均以 hash 鎖定；它不等同雙人覆核的 `approved`。遇到 `needs_review`，UI 應顯示「需人工確認」，不得宣稱學生不符合。

`structured_requirements` 會分開保存最低完成門檻、官方宣告的課程池、分類學分、entry 任選、課程互斥、命名領域、不可重複採計、人工驗證條件及官方來源衝突。consumer 應優先讀 `completion_summary`、`credit_constraints` 與各 selection constraints；`core_credits_text_value` 僅為相容舊版的最低核心學分 mirror，已標示 deprecated。STREAM 的核心課程池 15 學分與最低核心 3 學分不再混用；海洋天然物的課程池 31 學分與完成門檻 12 學分亦分欄保存。

`completion_summary.model_status` 只有在 `complete` 時才能單獨用於自動判定。`ai_approved` 的普通規則模型可為 `complete`，但 consumer 仍可依產品風險政策要求人工 `approved`。`partial` 表示仍有 `manual_requirements` 或尚待目標式審核；`conflicted` 表示官方文件本身有未解衝突，此時相應 minimum 為 `null`，UI 必須顯示「需人工確認」。AI 聯盟五學程另保存五向度各至少一門、總學分 15、TAICA 證明的系外 9／聯盟課程 8 學分門檻，以及認抵上限。

抽樣稽核保存在 `data/ai-review/115-1.json`；其候選數、候選 ID 集合 hash、三份樣本 PDF/text hash 或所選版本只要改變，build 就會 fail closed。其餘待審項目會輸出到 `reports/manual-review-115-1.json`。

`manual_requirements.requirement_context` 區分一般 `program_completion` 與額外 `certificate` 條件。證書活動、時數或報告不得反向阻擋一般學程完成；consumer 若要判斷證書資格，才需另行評估 certificate context。

consumer 應以 `catalog_entry_id`、`requirement_label`、`program_course_name_snapshot` 與 constraint 內的穩定 ID 計算，不能只累加畫面上的全部課程。`max_entries: null` 只代表沒有修課上限；若同時有 `max_entries_counted_for_requirement` 與 `excess_credit_destination`，額外課仍可修，但只能流向指定的核心／選修／學程總學分桶。

若 `option_count_matches` 為 `false`，表示 PDF 宣告的選項數與實際表列數不一致；consumer 應保留全部表列課程並提示人工確認，不得自行刪除選項。

初次設定最低畢業學分時，consumer 應以「入學年度＋學制＋校方系所代碼」查詢。例如 `B4020` 是資訊管理學系；115 學年度端點回傳 `minimum_graduation_credits: 135`。若查無系所或來源暫時不可用，UI 應要求使用者確認或手動輸入，不得靜默回退成 128。

## 完成度 evaluator

`evaluator.py` 支援 `all_of`、`any_of`、`course_set`、aliases、已修／修課中／缺少、不重複採計及 `manual_review` 傳播。Schema 亦保留最低／最高學分、至少門數、跨系院學分、學生系所條件與召集人核准欄位。這是規則模型的參考實作，不取代校方判定。

詳見 [DATA_SOURCES.md](DATA_SOURCES.md)、[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)、[docs/REVIEW.md](docs/REVIEW.md)、[docs/SCHEMA.md](docs/SCHEMA.md) 與 [DISCLAIMER.md](DISCLAIMER.md)。
