# Data sources

學程資料的主要權威來源是國立中山大學教學發展與資源中心的[公開學程頁面](https://ctdr.nsysu.edu.tw/class2.php)及頁面連結的公開課程規劃 PDF。最低畢業學分則來自選課系統公開的[必修科目表查詢](https://selcrs.nsysu.edu.tw/stu_query/crs_mst_qry/crs_mst_query_top.asp)，依入學年度及校方系所代碼逐筆擷取。只存取公開資源，不登入學生申請系統、管理後台或非公開 API。

每筆保存 catalog/PDF URL、UTC 取得時間、HTTP metadata、PDF binary SHA-256、normalized text SHA-256 及 parser version。URL、binary hash 與文字 hash 分開，因為同 URL 可能被覆蓋，PDF metadata 變動也不一定代表規則變動。

畢業學分資料保存結果頁 URL、binary SHA-256、UTC 取得時間與獨立 parser version。系所清單可能包含停辦或改名代碼；指定年度查無有效必修表，或最低畢業學分為 0 時，不會發布為可用門檻，而會列入 `unavailable_departments`。

完整畢業規則只採用標示適用入學年度的正式課程結構、必修科目表或校方規章。113+ 共同層主要依據[通識教育課程架構](https://rpb133.nsysu.edu.tw/static/file/29/1029/img/4427/824669194.pdf)與[學士班學生英文能力培育要點](https://rpb133.nsysu.edu.tw/static/file/29/1029/img/4427/840378989.pdf)；首批系所規則依據[應用數學系 113 必修科目表](https://rpb28.nsysu.edu.tw/static/file/183/1183/img/1373/113B-0.pdf)及[國際經營管理全英語學士學位學程 113 必修科目表](https://rpb78.nsysu.edu.tw/static/file/239/1239/img/IBBAcoursestructure_applyforAcademicYear2024students.pdf)。每份 JSON 保存來源 URL、檢視日期、頁碼與 PDF SHA-256。

當學期開課目錄只能協助使用者配對實際課程，不能證明入學年度的畢業規則。正式文件未提供或須查核學生紀錄的內容，以 `null`、`manual_review_required` 及具體人工處理說明保存，不從課表或最低總學分反推。

[NSYSUCourseAPI](https://github.com/nsysu-opendev/NSYSUCourseAPI) 可在未來作課號、課名、學分與開課資料的輔助驗證；它不是學程規則的唯一權威來源，本版未將其結果自動納入認定。

原始 PDF 不提交 Git，也不宣告為 MIT 授權。衍生 JSON 應保留來源鏈結；再利用前請自行確認著作權、資料庫權利與校方使用條款。
