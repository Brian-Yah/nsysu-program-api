# Data sources

學程資料的主要權威來源是國立中山大學教學發展與資源中心的[公開學程頁面](https://ctdr.nsysu.edu.tw/class2.php)及頁面連結的公開課程規劃 PDF。最低畢業學分則來自選課系統公開的[必修科目表查詢](https://selcrs.nsysu.edu.tw/stu_query/crs_mst_qry/crs_mst_query_top.asp)，依入學年度及校方系所代碼逐筆擷取。只存取公開資源，不登入學生申請系統、管理後台或非公開 API。

每筆保存 catalog/PDF URL、UTC 取得時間、HTTP metadata、PDF binary SHA-256、normalized text SHA-256 及 parser version。URL、binary hash 與文字 hash 分開，因為同 URL 可能被覆蓋，PDF metadata 變動也不一定代表規則變動。

畢業學分資料保存結果頁 URL、binary SHA-256、UTC 取得時間與獨立 parser version。已由官方沿革確認為尚未成立、停招或改名的年度／代碼不會進入該年度資料；仍在有效期間但指定年度查無有效必修表，或最低畢業學分為 0 時，才會列入 `unavailable_departments`。

官方查詢首頁的系所選單是跨年度全域清單，會同時保留已停辦代碼與尚未成立的新系所；空白結果頁仍可能回傳 HTTP 200。產生器因此另以官方組織沿革鎖定系所生命週期，不把不適用年度誤列為資料不足。目前依據包括：[材料與光電工程學系於97學年度整併更名](https://sec.nsysu.edu.tw/var/file/9/1009/img/332/af9602.pdf)、[海洋科學學士學位學程於102學年度整併為海洋科學系](https://sec.nsysu.edu.tw/var/file/9/1009/img/332/af10104-3.pdf)、[生物醫學科技學系全英語學士班於113學年度增設](https://apply-1-account.nsysu.edu.tw/webppr/P11319GENINFO_C.PDF)、[國際跨域學士學位學程原住民族專班於114學年度成立](https://siwan.nsysu.edu.tw/p/404-1029-348843.php?Lang=zh-tw)、[護理學系於114學年度成立](https://cmed.nsysu.edu.tw/p/412-1327-23611.php?Lang=zh-tw)，以及[人文暨科技跨領域學士學位學程自115學年度改制為學系](https://siwan.nsysu.edu.tw/p/404-1029-363411.php?Lang=zh-tw)。生命週期過濾只排除有正式沿革證據的年度／代碼；一般內容缺漏仍維持 fail-closed 人工確認。

完整畢業規則只採用標示適用入學年度的正式課程結構、必修科目表或校方規章。113+ 共同層主要依據[通識教育課程架構](https://rpb133.nsysu.edu.tw/static/file/29/1029/img/4427/824669194.pdf)與[學士班學生英文能力培育要點](https://rpb133.nsysu.edu.tw/static/file/29/1029/img/4427/840378989.pdf)；首批系所規則依據[應用數學系 113 必修科目表](https://rpb28.nsysu.edu.tw/static/file/183/1183/img/1373/113B-0.pdf)及[國際經營管理全英語學士學位學程 113 必修科目表](https://rpb78.nsysu.edu.tw/static/file/239/1239/img/IBBAcoursestructure_applyforAcademicYear2024students.pdf)。每份 JSON 保存來源 URL、檢視日期、頁碼與 PDF SHA-256。

當學期開課目錄只能協助使用者配對實際課程，不能證明入學年度的畢業規則。正式文件未提供或須查核學生紀錄的內容，以 `null`、`manual_review_required` 及具體人工處理說明保存，不從課表或最低總學分反推。

[NSYSUCourseAPI](https://github.com/nsysu-opendev/NSYSUCourseAPI) 可在未來作課號、課名、學分與開課資料的輔助驗證；它不是學程規則的唯一權威來源，本版未將其結果自動納入認定。

原始 PDF 不提交 Git，也不宣告為 MIT 授權。衍生 JSON 應保留來源鏈結；再利用前請自行確認著作權、資料庫權利與校方使用條款。
