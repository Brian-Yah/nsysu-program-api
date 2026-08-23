const ENTRY_YEAR = "113";
const state = { queue: null, currentId: null, current: null, tab: "issues" };
const $ = selector => document.querySelector(selector);
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"})[c]);
const n = value => value == null ? "—" : Number(value).toLocaleString("zh-TW");
const labels = {
  approved: "證據支持", cautious_use: "小心使用", needs_fix: "需要修正", skipped: "暫緩",
  course_credit_unknown: "課程學分不明", course_group_requires_review: "課群／學生軌道待確認",
  course_row_requires_review: "特殊課程列待確認", parser_warnings: "表格解析不一致",
  non_generated_source_requires_review: "獨立 PDF 人工建模",
  course_table_unavailable: "官方課表不可用", empty_course_table: "課程表為空",
  missing_minimum_graduation_credits: "最低畢業學分缺失"
};

function toast(message, error=false) {
  const el = $("#toast"); el.textContent = message; el.className = error ? "show error" : "show";
  clearTimeout(toast.timer); toast.timer = setTimeout(() => el.className = "", 2600);
}

async function api(url, options) {
  const response = await fetch(url, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
}

function decisionState(item) { return item.decision_stale ? "stale" : (item.decision || "unreviewed"); }

function updateProgress() {
  const q = state.queue; if (!q) return;
  const reviewed = q.total - q.counts.unreviewed - q.counts.stale;
  const percent = q.total ? Math.round(reviewed / q.total * 100) : 0;
  $("#progressText").textContent = `${reviewed} / ${q.total} 已審`;
  $("#progressPercent").textContent = `${percent}%`;
  $("#progressBar").style.width = `${percent}%`;
}

function renderQueue() {
  const search = $("#search").value.trim().toLowerCase();
  const filter = $("#statusFilter").value;
  const items = state.queue.departments.filter(item => {
    const haystack = `${item.department_name_zh} ${item.department_code}`.toLowerCase();
    if (search && !haystack.includes(search)) return false;
    const status = decisionState(item);
    if (filter === "all") return true;
    if (filter === "pending") return status === "unreviewed" || status === "stale";
    if (filter === "needs_evidence") return item.needs_official_evidence;
    return status === filter;
  });
  $("#queue").innerHTML = items.length ? items.map(item => `
    <button class="queue-item ${item.department_code === state.currentId ? "active" : ""}" data-id="${item.department_code}">
      <span class="state-dot ${decisionState(item)}"></span>
      <span><span class="queue-name">${esc(item.department_name_zh)}</span><span class="queue-reason">${item.reasons.map(x => esc(labels[x] || x)).join(" · ")}</span></span>
      ${item.needs_official_evidence ? '<span class="count-pill">缺證據</span>' : ""}
    </button>`).join("") : '<div class="empty">沒有符合條件的系所</div>';
  document.querySelectorAll(".queue-item").forEach(el => el.addEventListener("click", () => selectDepartment(el.dataset.id)));
  updateProgress();
}

function renderIssues() {
  const rule = state.current.rule;
  const item = state.queue.departments.find(x => x.department_code === rule.department_code) || {};
  const reviewCourses = (rule.courses || []).filter(x => x.manual_review_required);
  const reviewGroups = (rule.course_groups || []).filter(x => x.manual_review_required);
  const manualRules = rule.manual_review_rules || [];
  $("#content").innerHTML = `
    <div class="summary-grid">
      <div class="summary-card"><span>最低畢業學分</span><strong>${n(rule.credit_requirements?.minimum_graduation_credits)}</strong></div>
      <div class="summary-card"><span>待確認課程</span><strong>${reviewCourses.length}</strong></div>
      <div class="summary-card"><span>待確認課群</span><strong>${reviewGroups.length}</strong></div>
      <div class="summary-card"><span>人工規則</span><strong>${manualRules.length}</strong></div>
    </div>
    <div class="chips">${item.reasons.map(x => `<span class="chip">${esc(labels[x] || x)}</span>`).join("")}</div>
    <section class="rule-section"><h3>為什麼不能自動核准</h3>
      ${(item.reason_details || []).map(x => `<div class="rule"><strong>${esc(labels[x.code] || x.code)}</strong><br>${esc(x.message)}</div>`).join("") || '<div class="rule">未提供詳細原因</div>'}
    </section>
    ${reviewCourses.length ? `<section class="rule-section"><h3>需要你確認的課程（${reviewCourses.length}）</h3>${reviewCourses.map(course => `<div class="rule"><strong>${esc(course.canonical_name_zh)}</strong> · ${n(course.credits)} 學分<br>${(course.notes || []).map(esc).join("<br>")}</div>`).join("")}</section>` : ""}
    ${reviewGroups.length ? `<section class="rule-section"><h3>需要你確認的課群（${reviewGroups.length}）</h3>${reviewGroups.map(group => `<div class="rule"><strong>${esc(group.name_zh)}</strong><br>至少 ${n(group.minimum_courses)} 門／最低 ${n(group.minimum_credits)} 學分<br>${(group.notes || []).map(esc).join("<br>")}</div>`).join("")}</section>` : ""}
    <section class="rule-section"><h3>人工規則（${manualRules.length}）</h3>${manualRules.map(x => `<div class="rule"><code>${esc(x.rule_id)}</code><br><strong>${esc(x.description)}</strong><div class="evidence">待確認原因：${esc(x.reason)}<br>建議處理：${esc(x.resolution)}</div></div>`).join("") || '<div class="rule">無</div>'}</section>`;
}

function renderCourses() {
  const rows = state.current.rule.courses || [];
  $("#content").innerHTML = `<table class="courses"><thead><tr><th>#</th><th>課程</th><th>學分</th><th>類別</th><th>建議年級／學期</th><th>備註</th></tr></thead><tbody>${rows.map((x, i) => `<tr class="${x.manual_review_required ? "review-row" : ""}"><td>${i + 1}</td><td><strong>${esc(x.canonical_name_zh)}</strong><br>${esc(x.canonical_name_en || "英文名未提供")}</td><td>${n(x.credits)}</td><td>${esc(x.curriculumRequirement)}</td><td>${n(x.recommendedYear)}／${esc(x.recommendedSemester)}</td><td>${x.manual_review_required ? '<span class="count-pill">待確認</span><br>' : ""}${(x.notes || []).map(esc).join("<br>")}</td></tr>`).join("")}</tbody></table>`;
}

function renderRules() {
  const rule = state.current.rule;
  const credits = rule.credit_requirements || {};
  $("#content").innerHTML = `
    <section class="rule-section"><h3>學分門檻</h3><div class="rule"><pre>${esc(JSON.stringify(credits, null, 2))}</pre></div></section>
    <section class="rule-section"><h3>課群（${(rule.course_groups || []).length}）</h3>${(rule.course_groups || []).map(x => `<div class="rule"><strong>${esc(x.name_zh)}</strong>${x.manual_review_required ? ' <span class="count-pill">待確認</span>' : ""}<br><code>${esc(x.rule_kind)} · ${esc(x.counts_toward)}</code><br>課程 ${x.course_ids?.length || 0} 門；最低 ${n(x.minimum_courses)} 門／${n(x.minimum_credits)} 學分<br>${(x.notes || []).map(esc).join("<br>")}</div>`).join("") || '<div class="rule">無</div>'}</section>
    <section class="rule-section"><h3>先修規則（${(rule.prerequisites || []).length}）</h3>${(rule.prerequisites || []).map(x => `<div class="rule"><pre>${esc(JSON.stringify(x, null, 2))}</pre></div>`).join("") || '<div class="rule">無</div>'}</section>
    <section class="rule-section"><h3>不得重複採計（${(rule.non_duplicated_counting_groups || []).length}）</h3>${(rule.non_duplicated_counting_groups || []).map(x => `<div class="rule"><pre>${esc(JSON.stringify(x, null, 2))}</pre></div>`).join("") || '<div class="rule">無</div>'}</section>`;
}

function renderContent() {
  if (!state.current) return;
  if (state.tab === "issues") renderIssues();
  else if (state.tab === "courses") renderCourses();
  else if (state.tab === "rules") renderRules();
  else $("#content").innerHTML = `<pre>${esc(JSON.stringify(state.current.rule, null, 2))}</pre>`;
}

async function selectDepartment(code) {
  state.currentId = code; renderQueue(); $("#saveState").textContent = "載入中…";
  try {
    state.current = await api(`/api/graduation/department/${code}?entry_year=${ENTRY_YEAR}`);
    const rule = state.current.rule;
    const item = state.queue.departments.find(x => x.department_code === code);
    $("#departmentName").textContent = rule.department_name_zh;
    $("#departmentMeta").textContent = `${rule.department_code} · ${rule.entry_year} 入學 · ${rule.degree_level}`;
    $("#modelBadge").textContent = rule.review_status;
    $("#modelBadge").className = "badge partial";
    $("#staleNotice").classList.toggle("hidden", !state.current.decision_stale);
    $("#evidenceNotice").classList.toggle("hidden", !item.needs_official_evidence);
    $("#notes").value = state.current.decision?.notes || "";
    $("#evidenceUrl").value = state.current.decision?.evidence_url || "";
    $("#saveState").textContent = state.current.decision ? (state.current.decision_stale ? "審核已過期" : `已儲存：${labels[state.current.decision.decision]}`) : "尚未儲存";
    $("#sourceLabel").textContent = rule.sources?.[0]?.title || rule.department_name_zh;
    if (state.current.source_url) {
      $("#pdfFrame").src = state.current.source_url;
      $("#pdfFrame").style.display = "block"; $("#pdfEmpty").style.display = "none";
      $("#sourceLink").href = state.current.source_url; $("#sourceLink").style.visibility = "visible";
    } else {
      $("#pdfFrame").removeAttribute("src"); $("#pdfFrame").style.display = "none";
      $("#pdfEmpty").textContent = "沒有來源網址，請提供中山大學官方文件"; $("#pdfEmpty").style.display = "grid";
      $("#sourceLink").style.visibility = "hidden";
    }
    renderContent();
  } catch (error) { toast(error.message, true); }
}

async function save(decision) {
  if (!state.currentId) return toast("請先選擇系所", true);
  const reviewer = $("#reviewer").value.trim();
  const notes = $("#notes").value.trim();
  const evidenceUrl = $("#evidenceUrl").value.trim();
  const item = state.queue.departments.find(x => x.department_code === state.currentId);
  if (!reviewer) { $("#reviewer").focus(); return toast("請先輸入審核人姓名", true); }
  if (["approved", "cautious_use", "needs_fix"].includes(decision) && !notes) { $("#notes").focus(); return toast("請寫下確認結果或需要修正的內容", true); }
  if (decision === "approved" && item.needs_official_evidence && !evidenceUrl) { $("#evidenceUrl").focus(); return toast("缺課表的系所必須提供官方證據網址", true); }
  try {
    document.querySelectorAll(".decision").forEach(x => x.disabled = true);
    const record = await api(`/api/graduation/decision/${state.currentId}?entry_year=${ENTRY_YEAR}`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({decision, reviewer, notes, evidence_url: evidenceUrl})});
    state.current.decision = record; state.current.decision_stale = false;
    state.queue = await api(`/api/graduation/queue?entry_year=${ENTRY_YEAR}`);
    $("#saveState").textContent = `已儲存：${labels[decision]}`; renderQueue(); toast(`已標記「${labels[decision]}」`);
    const pending = state.queue.departments.find(x => !x.decision || x.decision_stale);
    if (pending) setTimeout(() => selectDepartment(pending.department_code), 350);
  } catch (error) { toast(error.message, true); }
  finally { document.querySelectorAll(".decision").forEach(x => x.disabled = false); }
}

async function init() {
  $("#reviewer").value = localStorage.getItem("nsysu-reviewer") || "";
  $("#reviewer").addEventListener("change", e => localStorage.setItem("nsysu-reviewer", e.target.value.trim()));
  $("#search").addEventListener("input", renderQueue); $("#statusFilter").addEventListener("change", renderQueue);
  document.querySelectorAll(".tab").forEach(tab => tab.addEventListener("click", () => { state.tab = tab.dataset.tab; document.querySelectorAll(".tab").forEach(x => x.classList.toggle("active", x === tab)); renderContent(); }));
  document.querySelectorAll(".decision").forEach(button => button.addEventListener("click", () => save(button.dataset.decision)));
  document.addEventListener("keydown", event => {
    if (event.target.matches("input, textarea, select") || event.ctrlKey || event.metaKey || event.altKey) return;
    const decision = {a:"approved", c:"cautious_use", f:"needs_fix", s:"skipped"}[event.key.toLowerCase()];
    if (decision) { event.preventDefault(); save(decision); }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      const items = state.queue.departments, current = items.findIndex(x => x.department_code === state.currentId);
      const next = Math.max(0, Math.min(items.length - 1, current + (event.key === "ArrowDown" ? 1 : -1)));
      if (items[next]) selectDepartment(items[next].department_code);
    }
  });
  try {
    state.queue = await api(`/api/graduation/queue?entry_year=${ENTRY_YEAR}`); renderQueue();
    const first = state.queue.departments.find(x => !x.decision || x.decision_stale) || state.queue.departments[0];
    if (first) selectDepartment(first.department_code);
  } catch (error) { toast(error.message, true); $("#queue").innerHTML = `<div class="empty">${esc(error.message)}</div>`; }
}
init();
