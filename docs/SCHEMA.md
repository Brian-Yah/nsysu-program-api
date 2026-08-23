# Schema notes

公開資料具有四個版本欄位：`schema_version`、`academic_version`、`data_revision`、`retrieved_at`。每筆 program 有穩定 `program_id`、名稱歷史、類型、狀態、責任單位、網站、來源與 review status。

規則是遞迴 AST：

- `all_of`：所有子規則成立。
- `any_of`：至少一個子規則成立，用於擇一／替代。
- `course_set`：課程集合、全部必修、`min_courses`、`min_credits`、`max_credits`、不可重複採計、跨系院最低學分、學生系所條件及召集人核准。
- `manual_review`：不能可靠自動判斷，會由 evaluator 傳播為 `needs_review`。

每個 course ref 保存課號、PDF 課名 snapshot、學分 snapshot、aliases、驗證狀態與不確定原因。實作 evaluator 目前執行核心組合、課程集合、aliases、狀態與不重複採計；較複雜的跨系院及 department branch 在資料獲人工核准後擴充，未支援者不得默認通過。

`course_catalog` 是由官方 PDF 表格擷取的可查詢候選資料。每列有穩定的 `catalog_entry_id`、`opening_units`、`course_name_snapshot`、`credits_snapshot`、`requirement_group`、更細的 `requirement_section`、可選的 `requirement_label` 與 `program_course_name_snapshot`、備註及來源頁碼。同一 PDF 格內的替代課名共用 entry ID。多數規劃表沒有課號，此時 `course_code` 必須是 `null`，並標為 `needs_course_code_verification`。PDF 內所有新舊規劃版本保存在 extracted 層的 `rule_versions`；API 選擇不晚於請求學期的最新版本，不依 PDF 頁序，也不跨版本拼裝。

結構化採計規則分為：

- `course_count_constraints`：`max_courses`、同一 entry 的 `course_equivalence`、明文 `select_courses`，以及同一半導體學程科目的 `program_course_equivalence`。
- `entry_selection_constraints`：對一組 `catalog_entry_ids` 套用最少／最多 entry 數；只有「至少」而沒有上限依據時，`max_entries` 為 `null`。
- `program_course_selection_constraints`：對半導體學程的必修、必選修或選修科目群套用門數。
- `named_group_selection_constraints`：先選命名領域，再滿足該領域最低學分。
- `no_double_count_constraints`：同一課程不得在核心與選修重複計入。
- `credit_constraints`：學程、核心／選修、A/B/C/D 等命名分類、系外與 TAICA 證明的最低或最多採計學分。
- `course_pools`：官方表列課程池的宣告學分；不是完成門檻。
- `manual_requirements`：活動、工作坊、服務學習、報告、先修、核准、認證及學生身分類別等不能安全自動判定的條件；`requirement_context` 區分一般完成、證書、學分認抵與學程申請。
- `institutional_policy_ids`：套用的校級通用規定版本；完整內容由 `api/v1/policies/program-requirements.json` 提供。
- `catalog_entry_group_id`：同一規劃表項目中的替代／等價課群；每門課仍有唯一 `catalog_entry_id`。
- `source_conflicts`：同一官方文件的候選規則互相衝突；未解決時不得輸出單一 canonical minimum。

每筆保留穩定 `constraint_id`、來源頁、原文與驗證狀態。`declared_option_count` 與 `option_count_matches` 同時保存 PDF 宣告數和實際表列數；不一致時不得猜測或刪課。

Consumer 判斷 `entry_selection_constraints` 時，必須逐條檢查 `selected_entries >= min_entries`；只有 `max_entries` 非 `null` 時才限制可修門數。`max_entries_counted_for_requirement` 是該分類最多採計門數，溢出課程依 `excess_credit_destination` 流向其他學分桶，不能直接排除。核心完成條件以 `completion_summary.minimum_core_credits`、分類 credit constraints 與所有核心最低門數 constraints 共同判定；不得只看舊欄位或核心總和。

`review_status` 的 `ai_approved` 表示學程符合鎖定的普通邏輯 policy，且隨機 PDF 樣本抽查通過；它與兩位不同 reviewer 核准的 `approved` 不同。若候選集合、樣本 PDF/text hash 或 parser 產物改變，build 必須 fail closed。

`completion_summary.model_status` 為 `partial`／`conflicted` 時，consumer 不得把未建模或衝突規則視為通過。`core_credits_text_value` 在 schema 1.2 僅為 `minimum_core_credits` 的 deprecated mirror。

`credit_constraints.scope` 是判斷採計資格的語意範圍，可使用課程群組、分類標籤、開課單位、課程屬性、catalog entries、TAICA 課程、相近課程認抵或學生 curriculum affiliation/role。TAICA 的「不屬主修、輔系或其他學程必修／必選」不是單純開課系所判斷，consumer 不得改用 opening unit 近似。

最低畢業學分是獨立資料集，以 `entry_academic_year`、`degree_level`、`department_code` 組成查詢鍵，目前涵蓋 112–115 並隨官方年度選單擴充。`minimum_graduation_credits` 只接受大於 0 的整數；`required_course_ratio` 可為 null。每筆保留官方結果 URL、binary hash、取得時間、HTTP status 及畢業門檻 parser version。系所名稱只供顯示，不應取代穩定的校方系所代碼；`latest` 只供當年度新生，不得替舊生自動選用。

## 畢業規則 API

`graduation-rules/common/112.json` 是 112 學年度入學者的校級共同層；`graduation-rules/common/113-plus.json` 是 113 學年度起入學者的共同層；`graduation-rules/{entry_year}/bachelor/{department_code}.json` 是系所層，目前涵蓋 112–115。系所檔以相對 `common_rule_ref` 連回對應年度的共同層，consumer 必須合併評估，系所規則不會複製共同規則。112 與 113+ 是獨立版本，不得以其中一版代替另一版。

官方年度選單是可用版本的唯一發現來源；排程每週從 112 起同步到官方最新年度。單一來源 hash 不變時沿用原規則與審核；來源有一般增刪時重建，若課表由有變無、課程列驟減超過一半、最低畢業學分消失，或全年度課表／最低學分覆蓋低於 75%，建置必須 fail closed，維持上一個 Pages 版本。

系所課程以穩定 `course_id` 連接 `course_groups`、`prerequisites` 與 `non_duplicated_counting_groups`。`course_groups.rule_kind` 支援 `choose_n_from_m`、最低學分、跨分類及 all-of；替代課放在每門課的 `alternatives`。每門課都保存中英文正式名稱、已知別名、學分、`curriculumRequirement`、建議年級學期、來源文件及備註。官方文件沒有英文名稱或學分時必須使用 `null`，不得自行翻譯或由當學期課表補值。

`manual_review_rules` 必須說明原始規則、無法自動執行的原因及人工確認方式；含任何未解人工規則的檔案使用 `review_status: manual_review_required`。`additional_credit_rules` 只保存官方明列的特殊入學身分加修學分。JSON Schema 驗證型別與必填欄位，建置器另驗證所有 source/course/group reference、ID 唯一性及門數上下限。

系所畢業規則的 `review_status: ai_approved` 表示完整的官方來源 hash 與解析後 ruleset 已由 `graduation-rules-complete-v1` 稽核鎖定，且課程列、學分、課群與人工條款分類全部通過。它不代表所有條件都能自動計算：`ai_review.manual_evaluation_rule_ids` 仍列出需依學生身分、授課屬性或系所核准人工判斷的已驗證規則。來源 hash、課程、學分、課群或候選集合只要改變，build 必須 fail closed，重新回到審核流程。

`ai_review.blocking_reason_details` 提供未通過系所的機器碼與人類可讀原因。缺少正式課表、最低畢業學分、單科學分，或課群宣告與表列數量不一致時，不得以當學期課程目錄補值，也不得只把狀態字串改成 `ai_approved`。
