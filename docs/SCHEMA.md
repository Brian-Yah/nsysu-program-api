# Schema notes

公開資料具有四個版本欄位：`schema_version`、`academic_version`、`data_revision`、`retrieved_at`。每筆 program 有穩定 `program_id`、名稱歷史、類型、狀態、責任單位、網站、來源與 review status。

規則是遞迴 AST：

- `all_of`：所有子規則成立。
- `any_of`：至少一個子規則成立，用於擇一／替代。
- `course_set`：課程集合、全部必修、`min_courses`、`min_credits`、`max_credits`、不可重複採計、跨系院最低學分、學生系所條件及召集人核准。
- `manual_review`：不能可靠自動判斷，會由 evaluator 傳播為 `needs_review`。

每個 course ref 保存課號、PDF 課名 snapshot、學分 snapshot、aliases、驗證狀態與不確定原因。實作 evaluator 目前執行核心組合、課程集合、aliases、狀態與不重複採計；較複雜的跨系院及 department branch 在資料獲人工核准後擴充，未支援者不得默認通過。

`course_catalog` 是由官方 PDF 表格擷取的可查詢候選資料，包含 `opening_units`、`course_name_snapshot`、`credits_snapshot`、核心／選修群組、備註與來源頁碼。多數規劃表沒有課號，此時 `course_code` 必須是 `null`，並標為 `needs_course_code_verification`。PDF 內所有新舊規劃版本保存在 extracted 層的 `rule_versions`，API 的 `selected_pdf_academic_version` 明示目前呈現哪一版，避免跨版本拼裝。

最低畢業學分是獨立資料集，以 `entry_academic_year`、`degree_level`、`department_code` 組成查詢鍵。`minimum_graduation_credits` 只接受大於 0 的整數；`required_course_ratio` 可為 null。每筆保留官方結果 URL、binary hash、取得時間、HTTP status 及畢業門檻 parser version。系所名稱只供顯示，不應取代穩定的校方系所代碼。
