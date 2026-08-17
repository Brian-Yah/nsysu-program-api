const state = { queue: null, currentId: null, current: null, tab: "rules" };
const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"})[c]);
const n = (value) => value == null ? "—" : Number(value).toLocaleString("zh-TW");
const labels = {
  approved: "基本正確", cautious_use: "小心使用", needs_fix: "需要修正", skipped: "略過",
  course_count_constraints: "課程門數", entry_selection_constraints: "選課組合",
  named_group_selection_constraints: "群組選擇", program_course_selection_constraints: "學程選擇",
  no_double_count_constraints: "不得重複採計", non_standard_credit_constraint: "特殊學分規則",
  manual_requirements: "人工條件", source_conflicts: "來源衝突", parser_warning: "解析警告",
  special_rule_text_in_course_note: "課程備註規則", not_active: "已停開", missing_total_minimum: "總學分缺失",
  unclassified_course: "未分類課程", maximum_core_credits: "核心上限", minimum_core_courses: "核心門數",
  minimum_elective_courses: "選修門數"
};

function toast(message, error=false) {
  const el = $("#toast"); el.textContent = message; el.className = error ? "show error" : "show";
  clearTimeout(toast.timer); toast.timer = setTimeout(() => el.className = "", 2300);
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
  const items = state.queue.programs.filter(item => {
    const haystack = `${item.name_zh} ${item.program_id}`.toLowerCase();
    if (search && !haystack.includes(search)) return false;
    const status = decisionState(item);
    if (filter === "all") return true;
    if (filter === "pending") return status === "unreviewed" || status === "stale";
    if (filter === "conflicted") return item.model_status === "conflicted";
    return status === filter;
  });
  $("#queue").innerHTML = items.length ? items.map(item => `
    <button class="queue-item ${item.program_id === state.currentId ? "active" : ""}" data-id="${item.program_id}">
      <span class="state-dot ${decisionState(item)}"></span>
      <span><span class="queue-name">${esc(item.name_zh)}</span><span class="queue-reason">${item.reasons.map(x => esc(labels[x] || x)).join(" · ")}</span></span>
      ${item.conflict_count ? `<span class="count-pill">${item.conflict_count} conflict</span>` : ""}
      ${!item.conflict_count && item.resolved_conflict_count ? `<span class="count-pill">已解決 ${item.resolved_conflict_count}</span>` : ""}
    </button>`).join("") : '<div class="empty">沒有符合條件的學程</div>';
  document.querySelectorAll(".queue-item").forEach(el => el.addEventListener("click", () => selectProgram(el.dataset.id)));
  updateProgress();
}

function sourceText(rule) {
  if (!rule.source_text) return "";
  const location = rule.source_url ? `<a href="${esc(rule.source_url)}" target="_blank" rel="noreferrer">校級規定</a>` : `p.${esc(rule.source_page)}`;
  return `<div class="evidence">${location}｜${esc(rule.source_text)}</div>`;
}
function scopeText(scope={}) {
  if (scope.kind === "program") return "全學程";
  if (scope.kind === "catalog_filter") return (scope.requirement_groups || []).join(" / ") || "課程目錄";
  if (scope.kind === "entry_ids") return `${(scope.catalog_entry_ids || []).length} 個課程項目`;
  if (scope.kind === "course_eligibility") return `排除：${(scope.excluded_affiliations || []).join("、")}`;
  return scope.kind || "未指定";
}

function renderConflict(conflict) {
  const chosen = state.current.decision?.conflict_choices?.[conflict.conflict_id];
  const resolved = conflict.resolution_status === "resolved";
  const selected = chosen || conflict.selected_candidate_id;
  return `<div class="conflict-card"><strong>${esc(conflict.semantic_key)}</strong>${resolved ? ' <span class="chip">已解決</span>' : ""}
    ${(conflict.candidates || []).map(candidate => `<label class="candidate"><input type="radio" name="${conflict.conflict_id}" value="${candidate.candidate_id}" ${selected === candidate.candidate_id ? "checked" : ""} ${resolved ? "disabled" : ""}>候選值：<strong>${esc(JSON.stringify(candidate.value))}</strong>${(candidate.source_evidence || []).map(sourceText).join("")}</label>`).join("")}
  </div>`;
}

function renderRules() {
  const p = state.current.program, r = p.structured_requirements || {}, s = r.completion_summary || {};
  const queueItem = state.queue.programs.find(x => x.program_id === p.program_id) || {};
  const reasons = queueItem.reasons || [], reasonDetails = queueItem.reason_details || [];
  const credit = r.credit_constraints || [], selections = [
    ...(r.entry_selection_constraints || []), ...(r.course_count_constraints || []),
    ...(r.named_group_selection_constraints || []), ...(r.program_course_selection_constraints || [])
  ];
  const noDouble = r.no_double_count_constraints || [];
  const manual = r.manual_requirements || [];
    const conflicts = r.source_conflicts || [];
    const unresolvedConflicts = conflicts.filter(x => x.resolution_status !== "resolved");
    const resolvedConflicts = conflicts.filter(x => x.resolution_status === "resolved");
  $("#content").innerHTML = `
    <div class="summary-grid">
      <div class="summary-card"><span>總學分下限</span><strong>${n(s.minimum_total_credits)}</strong></div>
      <div class="summary-card"><span>核心下限</span><strong>${n(s.minimum_core_credits)}</strong></div>
      <div class="summary-card"><span>選修下限</span><strong>${n(s.minimum_elective_credits)}</strong></div>
      <div class="summary-card"><span>課程數</span><strong>${p.course_catalog?.length || 0}</strong></div>
    </div>
    <div class="chips">${reasons.map(x => `<span class="chip">${esc(labels[x] || x)}</span>`).join("")}</div>
    ${reasonDetails.length ? `<section class="rule-section"><h3>為什麼需要人工確認</h3>${reasonDetails.map(reason => `<div class="rule"><strong>${esc(reason.title)}</strong>${reason.details?.length ? `<ul>${reason.details.map(detail => `<li>${esc(detail)}</li>`).join("")}</ul>` : ""}${reason.additional_detail_count ? `<div class="evidence">另有 ${n(reason.additional_detail_count)} 項同類證據，請在下方規則區逐筆查看。</div>` : ""}</div>`).join("")}</section>` : ""}
    ${unresolvedConflicts.length ? `<section class="rule-section"><h3>⚠ 官方來源衝突（${unresolvedConflicts.length}）</h3>${unresolvedConflicts.map(renderConflict).join("")}</section>` : ""}
    ${resolvedConflicts.length ? `<section class="rule-section"><h3>已解決的來源差異（${resolvedConflicts.length}）</h3>${resolvedConflicts.map(renderConflict).join("")}</section>` : ""}
    <section class="rule-section"><h3>學分規則（${credit.length}）</h3>${credit.length ? credit.map(x => `<div class="rule"><code>${esc(x.kind)} · ${esc(scopeText(x.scope))}</code><br>最低 ${n(x.minimum_credits)}／最高採計 ${n(x.maximum_counted_credits)}${sourceText(x)}</div>`).join("") : '<div class="rule">無</div>'}</section>
    <section class="rule-section"><h3>選擇／互斥規則（${selections.length + noDouble.length}）</h3>${[...selections, ...noDouble].length ? [...selections, ...noDouble].map(x => `<div class="rule"><code>${esc(x.kind)} · ${esc(x.requirement_label || x.requirement_group || "")}</code><br>${esc((x.course_names || []).join("、") || (x.catalog_entry_ids || []).length + " 個項目")}<br>門數：${n(x.min_entries ?? x.minimum_count)} ～ ${n(x.max_entries ?? x.maximum_count)}${x.max_entries_counted_for_requirement != null ? `；門檻最多採計 ${n(x.max_entries_counted_for_requirement)}` : ""}${x.excess_credit_destination ? `；超額流向 ${esc(x.excess_credit_destination)}` : ""}${sourceText(x)}</div>`).join("") : '<div class="rule">無</div>'}</section>
    <section class="rule-section"><h3>人工判斷條件（${manual.length}）</h3>${manual.length ? manual.map(x => `<div class="rule"><code>${esc(x.kind)} · ${esc(x.requirement_context || "program_completion")}</code><br>${esc(x.description || x.source_text || "")}${sourceText(x)}</div>`).join("") : '<div class="rule">無</div>'}</section>`;
}

function renderCourses() {
  const rows = state.current.program.course_catalog || [];
  $("#content").innerHTML = `<table class="courses"><thead><tr><th>#</th><th>開課單位</th><th>課程</th><th>學分</th><th>群組／標籤</th><th>備註</th></tr></thead><tbody>${rows.map((x,i) => {
    const units = x.opening_units?.length ? x.opening_units.join("、") : x.opening_unit_snapshot;
    const name = x.course_name_snapshot || x.program_course_name_snapshot;
    return `<tr><td>${i+1}</td><td>${esc(units)}</td><td><strong>${esc(name)}</strong><br>${esc(x.course_code || "課號未提供")}</td><td>${n(x.credits_snapshot)}</td><td>${esc(x.requirement_group || "")}<br>${esc(x.requirement_label || "")}</td><td>${esc(x.notes || "")}</td></tr>`;
  }).join("")}</tbody></table>`;
}

function renderContent() {
  if (!state.current) return;
  if (state.tab === "rules") renderRules();
  else if (state.tab === "courses") renderCourses();
  else $("#content").innerHTML = `<pre>${esc(JSON.stringify(state.current.program, null, 2))}</pre>`;
}

async function selectProgram(id) {
  state.currentId = id; renderQueue(); $("#saveState").textContent = "載入中…";
  try {
    state.current = await api(`/api/program/${id}?version=115-1`);
    const p = state.current.program, summary = p.structured_requirements?.completion_summary || {};
    $("#programName").textContent = p.name_zh;
    $("#programMeta").textContent = `${p.program_id} · ${p.type} · PDF ${p.selected_pdf_academic_version || "版本未知"}`;
    $("#modelBadge").textContent = summary.model_status || p.review_status;
    $("#modelBadge").className = `badge ${summary.model_status || ""}`;
    $("#staleNotice").classList.toggle("hidden", !state.current.decision_stale);
    const unresolvedConflicts = (p.structured_requirements?.source_conflicts || []).filter(x => x.resolution_status !== "resolved");
    $("#conflictNotice").classList.toggle("hidden", !unresolvedConflicts.length);
    $("#notes").value = state.current.decision?.notes || "";
    $("#saveState").textContent = state.current.decision ? (state.current.decision_stale ? "審核已過期" : `已儲存：${labels[state.current.decision.decision]}`) : "尚未儲存";
    $("#pdfLabel").textContent = p.name_zh;
    if (state.current.pdf_local_url) {
      $("#pdfFrame").src = `${state.current.pdf_local_url}#view=FitH`;
      $("#pdfFrame").style.display = "block"; $("#pdfEmpty").style.display = "none";
      $("#pdfLink").href = state.current.pdf_local_url; $("#pdfLink").style.visibility = "visible";
    } else {
      $("#pdfFrame").removeAttribute("src"); $("#pdfFrame").style.display = "none";
      $("#pdfEmpty").textContent = "本機沒有這份 PDF，可使用官方網址"; $("#pdfEmpty").style.display = "grid";
      $("#pdfLink").href = p.source?.pdf_url || p.source_pdf; $("#pdfLink").style.visibility = "visible";
    }
    renderContent();
  } catch (error) { toast(error.message, true); }
}

async function save(decision) {
  if (!state.currentId) return toast("請先選擇學程", true);
  const reviewer = $("#reviewer").value.trim(), notes = $("#notes").value.trim();
  if (!reviewer) { $("#reviewer").focus(); return toast("請先輸入審核人姓名", true); }
  if (["cautious_use", "needs_fix"].includes(decision) && !notes) { $("#notes").focus(); return toast("此狀態必須填寫備註", true); }
  const conflictChoices = {};
  document.querySelectorAll('.conflict-card input[type="radio"]:checked').forEach(input => conflictChoices[input.name] = input.value);
  try {
    document.querySelectorAll(".decision").forEach(x => x.disabled = true);
    const record = await api(`/api/decision/${state.currentId}?version=115-1`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({decision, reviewer, notes, conflict_choices: conflictChoices})});
    state.current.decision = record; state.current.decision_stale = false;
    const item = state.queue.programs.find(x => x.program_id === state.currentId);
    item.decision = decision; item.decision_stale = false; item.decision_note = notes; item.reviewer = reviewer;
    state.queue = await api("/api/queue?version=115-1");
    $("#saveState").textContent = `已儲存：${labels[decision]}`; renderQueue(); toast(`已標記「${labels[decision]}」`);
    const pending = state.queue.programs.find(x => !x.decision || x.decision_stale);
    if (pending) setTimeout(() => selectProgram(pending.program_id), 350);
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
      const items = state.queue.programs, current = items.findIndex(x => x.program_id === state.currentId);
      const next = Math.max(0, Math.min(items.length - 1, current + (event.key === "ArrowDown" ? 1 : -1)));
      if (items[next]) selectProgram(items[next].program_id);
    }
  });
  try {
    state.queue = await api("/api/queue?version=115-1"); renderQueue();
    const first = state.queue.programs.find(x => !x.decision || x.decision_stale) || state.queue.programs[0];
    if (first) selectProgram(first.program_id);
  } catch (error) { toast(error.message, true); $("#queue").innerHTML = `<div class="empty">${esc(error.message)}</div>`; }
}
init();
