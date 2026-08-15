# Changelog

## v0.2.0 — 2026-08-15

- 新增依入學年度與系所代碼查詢最低畢業學分的獨立 static API。
- 收錄 115 學年度 28 個有效學士班，並保存官方必修科目表 URL 與 hash。
- 提供全量、latest 與單系端點，供 ClearGrad 初次設定自動填入。

## v0.1.1 — 2026-08-15

- 提升排程來源檢查的網路逾時與重試韌性，失敗時保留診斷 artifact。
- 固定 `pypdf` 與 `pdfplumber` 版本，並在來源資料記錄實際擷取器版本。
- 修正 cache 重建未刷新 `source.parser_version` 的問題，避免 API 中混用新舊解析器版本。
- 經 Scheduled source check #4 驗證，官方來源 semantic diff 為零筆新增、移除或變更。

## v0.1.0 — 2026-08-15

- 首次發布 115-1 學程靜態 API、PDF 來源證據、課程擷取結果與 GitHub Pages 部署流程。
