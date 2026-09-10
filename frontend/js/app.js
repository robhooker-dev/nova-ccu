"use strict";

/* ---------------------------------------------------------------------
 * State
 * ------------------------------------------------------------------- */
const state = {
  user: null,        // { principal, display_name }
  taxonomy: null,
  view: "dashboard",
  activeCaseId: null,
  detailOrigin: "cases",  // where a case/hub detail view should "back" to
};

const root = document.getElementById("app-root");
const modalRoot = document.getElementById("modal-root");

/* ---------------------------------------------------------------------
 * Identity (dev-fallback: headers set from what's stored locally).
 * This stands in for real Entra sign-in until AZURE_AD_* is configured.
 * ------------------------------------------------------------------- */
function loadUser() {
  const raw = localStorage.getItem("nova_ccu_user");
  return raw ? JSON.parse(raw) : null;
}

function saveUser(user) {
  localStorage.setItem("nova_ccu_user", JSON.stringify(user));
  state.user = user;
  renderIdentityBox();
}

function renderIdentityBox() {
  const label = document.getElementById("identity-label");
  label.textContent = state.user ? `${state.user.display_name}` : "Not signed in";
}

function promptForUser() {
  return new Promise((resolve) => {
    modalRoot.innerHTML = `
      <div class="modal-overlay">
        <div class="modal-box" style="max-width:420px;">
          <div class="modal-head"><h2>Switch user</h2></div>
          <p style="font-size:13px;color:var(--muted);margin-top:0;">
            Dev-fallback identity &mdash; stands in for Entra sign-in until it's configured.
            Enter any email to test as an ad-hoc user, or sign in as one of the named demo
            accounts in README.md ("Demo sign-ins") with its password.
          </p>
          <div class="field">
            <label>Email / principal</label>
            <input id="su-principal" type="text" placeholder="dc.marsh@example.police.uk" />
          </div>
          <div class="field">
            <label>Display name</label>
            <input id="su-name" type="text" placeholder="DC J. Marsh" />
          </div>
          <div class="field">
            <label>Password (only needed for named demo accounts)</label>
            <input id="su-password" type="password" />
          </div>
          <div id="su-error"></div>
          <div class="modal-actions">
            <button class="btn" id="su-cancel">Cancel</button>
            <button class="btn btn-primary" id="su-ok">Continue</button>
          </div>
        </div>
      </div>`;
    document.getElementById("su-cancel").onclick = () => { modalRoot.innerHTML = ""; resolve(null); };
    document.getElementById("su-ok").onclick = async () => {
      const principal = document.getElementById("su-principal").value.trim();
      const name = document.getElementById("su-name").value.trim() || principal;
      const password = document.getElementById("su-password").value;
      const errBox = document.getElementById("su-error");
      const okBtn = document.getElementById("su-ok");
      errBox.innerHTML = "";
      if (!principal) return;
      okBtn.disabled = true;
      try {
        await api("/auth/dev-login", { method: "POST", body: JSON.stringify({ principal, password }) });
        modalRoot.innerHTML = "";
        resolve({ principal, display_name: name });
      } catch (e) {
        errBox.innerHTML = `<div class="error-banner">${escapeHtml(e.message)}</div>`;
        okBtn.disabled = false;
      }
    };
  });
}

/* ---------------------------------------------------------------------
 * API wrapper
 * ------------------------------------------------------------------- */
async function api(path, options = {}) {
  const headers = Object.assign({}, options.headers, {
    "Content-Type": "application/json",
  });
  if (state.user) {
    headers["X-User-Principal"] = state.user.principal;
    headers["X-User-Name"] = state.user.display_name;
  }
  const res = await fetch("/api" + path, Object.assign({}, options, { headers }));
  let data = null;
  try { data = await res.json(); } catch (e) { /* empty body */ }
  if (!res.ok) {
    const err = new Error((data && data.detail) || `Request failed (${res.status})`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

/* ---------------------------------------------------------------------
 * Small helpers
 * ------------------------------------------------------------------- */
function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function riskBadgeClass(band) {
  if (band === "High") return "badge-high";
  if (band === "Medium") return "badge-medium";
  if (band === "Low") return "badge-low";
  return "badge-neutral";
}

function riskBand(impact, likelihood) {
  const score = impact * likelihood;
  if (score >= 13) return "High";
  if (score >= 6) return "Medium";
  return "Low";
}

/* ---------------------------------------------------------------------
 * List sort/filter toolbar -- shared by every list view except
 * "Record intelligence" (that's a single form, not a list). Filtering and
 * sorting happen client-side over data already fetched for the view, so a
 * keystroke or dropdown change redraws the list without hitting the API.
 * ------------------------------------------------------------------- */
const listControls = {};

function listControl(key, defaultSort) {
  if (!listControls[key]) listControls[key] = { q: "", sort: defaultSort, status: "" };
  return listControls[key];
}

function renderListToolbar(key, opts) {
  const ctrl = listControl(key, opts.sorts[0].value);
  return `
    <div class="list-toolbar">
      <input type="text" id="lt-q-${key}" placeholder="${escapeHtml(opts.placeholder || "Filter…")}" value="${escapeHtml(ctrl.q)}" />
      ${opts.statusOptions ? `
        <select id="lt-status-${key}">
          <option value="">${escapeHtml(opts.statusLabel || "All")}</option>
          ${opts.statusOptions.map((s) => `<option value="${escapeHtml(s)}" ${s === ctrl.status ? "selected" : ""}>${escapeHtml(s)}</option>`).join("")}
        </select>` : ""}
      <select id="lt-sort-${key}">
        ${opts.sorts.map((s) => `<option value="${escapeHtml(s.value)}" ${s.value === ctrl.sort ? "selected" : ""}>${escapeHtml(s.label)}</option>`).join("")}
      </select>
    </div>
  `;
}

function wireListToolbar(key, opts, onChange) {
  const ctrl = listControl(key, opts.sorts[0].value);
  const qEl = document.getElementById(`lt-q-${key}`);
  const sortEl = document.getElementById(`lt-sort-${key}`);
  const statusEl = document.getElementById(`lt-status-${key}`);
  if (qEl) qEl.oninput = () => { ctrl.q = qEl.value; onChange(); };
  if (sortEl) sortEl.onchange = () => { ctrl.sort = sortEl.value; onChange(); };
  if (statusEl) statusEl.onchange = () => { ctrl.status = statusEl.value; onChange(); };
}

function matchesQuery(q, fields) {
  const needle = (q || "").trim().toLowerCase();
  if (!needle) return true;
  return fields.some((f) => String(f || "").toLowerCase().includes(needle));
}

// Loose display dates ("20 Jul 2026") parsed for sorting only -- never
// stored or validated as real dates, see storage.py.
function looseDateValue(s) {
  const t = Date.parse(s);
  return Number.isNaN(t) ? null : t;
}

function compareBy(getValue, dir) {
  return (a, b) => {
    const va = getValue(a), vb = getValue(b);
    if (va == null && vb == null) return 0;
    if (va == null) return 1;  // unparseable/unknown sorts last regardless of direction
    if (vb == null) return -1;
    if (va < vb) return dir === "asc" ? -1 : 1;
    if (va > vb) return dir === "asc" ? 1 : -1;
    return 0;
  };
}

// Refs look like "CCU/142/26" -- sort on the numeric middle segment, not
// lexically (which would put "CCU/10" before "CCU/2").
function refNum(ref) {
  const parts = String(ref || "").split("/");
  return parts.length > 1 ? Number(parts[1]) || 0 : 0;
}

function setNavActive(view) {
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === view);
  });
}

function navKeyFor(view) {
  if (view === "case-detail") return null;
  if (view === "hub-detail") return null;
  if (view === "subject") return null;
  return view;
}

function setView(view, opts = {}) {
  state.view = view;
  state.activeCaseId = opts.caseId ?? null;
  if (opts.origin) state.detailOrigin = opts.origin;
  setNavActive(navKeyFor(view));
  render();
}

/* ---------------------------------------------------------------------
 * Mode badge + startup
 * ------------------------------------------------------------------- */
async function refreshModeBadge() {
  try {
    const health = await api("/health");
    const m = health.modes;
    document.getElementById("mode-badge").textContent =
      `AI: ${m.llm} \u00b7 Auth: ${m.auth} \u00b7 Audit chain: ${health.audit_chain_ok ? "ok" : "BROKEN"}`;
  } catch (e) {
    document.getElementById("mode-badge").textContent = "Backend unreachable";
  }
}

// Role isn't part of the stored dev-fallback identity (just principal +
// display name) -- fetch it fresh each time so a role change on the server
// (or switching user) is picked up rather than trusting stale local state.
async function refreshUserRole() {
  if (!state.user) return;
  try {
    const who = await api("/whoami");
    if (who.signed_in) state.user.role = who.role;
  } catch (e) { /* dashboard just won't show role-gated sections */ }
}

async function init() {
  state.user = loadUser();
  renderIdentityBox();
  await refreshModeBadge();

  document.getElementById("switch-user-btn").onclick = async () => {
    const u = await promptForUser();
    if (u) { saveUser(u); await refreshUserRole(); render(); }
  };
  document.getElementById("brand-home").onclick = () => setView("dashboard");

  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.onclick = () => setView(btn.dataset.view);
  });

  if (!state.user) {
    const u = await promptForUser();
    if (u) saveUser(u);
  }

  try {
    state.taxonomy = await api("/taxonomy");
  } catch (e) {
    root.innerHTML = `<div class="error-banner">Could not load taxonomy from the backend: ${escapeHtml(e.message)}</div>`;
    return;
  }

  await refreshUserRole();
  render();
}

/* ---------------------------------------------------------------------
 * Router
 * ------------------------------------------------------------------- */
function render() {
  if (state.view === "dashboard") return renderDashboardView();
  if (state.view === "cases") return renderCasesView();
  if (state.view === "new-case") return renderNewCaseView();
  if (state.view === "case-detail") return renderCaseDetailView(state.activeCaseId);
  if (state.view === "hub") return renderHubTaskingsView();
  if (state.view === "hub-detail") return renderHubTaskingDetailView(state.activeCaseId);
  if (state.view === "nia") return renderNiaView();
  if (state.view === "bi") return renderBiView();
  if (state.view === "pirm") return renderPirmView();
  if (state.view === "subject") return renderSubjectView(state.activeCaseId);
}

/* ---------------------------------------------------------------------
 * Dashboard -- the logged-in user's own cases and Hub Taskings.
 * Everyone can see everything on CCU Cases / Hub Taskings; this is a
 * personal filter for convenience, not an access-control boundary.
 * ------------------------------------------------------------------- */
async function renderDashboardView() {
  root.innerHTML = `<div class="loading">Loading your dashboard&hellip;</div>`;
  let cases, taskings;
  try {
    [cases, taskings] = await Promise.all([api("/cases"), api("/hub-taskings")]);
  } catch (e) {
    root.innerHTML = `<div class="error-banner">${escapeHtml(e.message)}</div>`;
    return;
  }

  const me = state.user ? state.user.principal : null;
  const myCases = cases.filter((c) => c.officer_principal === me);
  const myTaskings = taskings.filter((t) => t.allocated_officer_principal === me);
  const t = state.taxonomy;

  // Reports submitted for supervisor sign-off have no dedicated inbox --
  // this is that inbox. Visible to supervisor/admin only; all cases are
  // already visible to everyone, so this is a relevance filter, not an
  // access boundary (same convention as the rest of this dashboard).
  const isSupervisor = !!(state.user && state.user.role && state.user.role !== "officer");
  const reviewQueue = isSupervisor ? cases.filter((c) => t.stages[c.stage_index] === "With DS for review") : [];

  const reviewKey = "dash-review";
  const reviewToolbar = {
    placeholder: "Filter the review queue…",
    sorts: [
      { value: "risk_desc", label: "Risk (high to low)" },
      { value: "risk_asc", label: "Risk (low to high)" },
      { value: "ref", label: "Reference" },
    ],
  };

  const casesKey = "dash-cases";
  const casesToolbar = {
    placeholder: "Filter my cases\u2026",
    sorts: [
      { value: "risk_desc", label: "Risk (high to low)" },
      { value: "risk_asc", label: "Risk (low to high)" },
      { value: "ref", label: "Reference" },
    ],
  };
  const hubKey = "dash-hub";
  const hubToolbar = {
    placeholder: "Filter my Hub Taskings\u2026",
    sorts: [
      { value: "due", label: "Due date (soonest first)" },
      { value: "priority", label: "Priority (high to low)" },
      { value: "ref", label: "Reference" },
    ],
  };
  const priorityWeight = { High: 3, Medium: 2, Low: 1 };

  root.innerHTML = `
    <div class="banner">
      <div class="eyebrow">Counter Corruption Unit</div>
      <h1>Good ${new Date().getHours() < 12 ? "morning" : "afternoon"}, ${escapeHtml(state.user ? state.user.display_name : "")}</h1>
      <p>You have ${myCases.length} case(s) and ${myTaskings.length} Hub Tasking(s) allocated to you${isSupervisor ? `, and ${reviewQueue.length} report(s) awaiting your review` : ""}.</p>
    </div>

    ${isSupervisor ? `
      <div class="card-title" style="margin-bottom:10px;">Review queue (${reviewQueue.length})</div>
      ${reviewQueue.length > 0 ? renderListToolbar(reviewKey, reviewToolbar) : ""}
      <div id="dash-review-body"></div>
    ` : ""}

    <div class="card-title" style="margin: ${isSupervisor ? "20px" : "0"} 0 10px;">My cases (${myCases.length})</div>
    ${myCases.length > 0 ? renderListToolbar(casesKey, casesToolbar) : ""}
    <div id="dash-cases-body"></div>

    <div class="card-title" style="margin: 20px 0 10px;">My Hub Taskings (${myTaskings.length})</div>
    ${myTaskings.length > 0 ? renderListToolbar(hubKey, hubToolbar) : ""}
    <div id="dash-hub-body"></div>
  `;

  const redrawDashReview = () => {
    const body = document.getElementById("dash-review-body");
    if (!body) return;
    if (reviewQueue.length === 0) { body.innerHTML = `<div class="empty-state" style="margin-bottom:20px;">Nothing waiting for your review.</div>`; return; }
    const ctrl = listControl(reviewKey, reviewToolbar.sorts[0].value);
    let filtered = reviewQueue.filter((c) => matchesQuery(ctrl.q, [c.ref, c.subject_code, c.officer_name, c.category]));
    const sorters = {
      risk_desc: compareBy((c) => c.risk_impact * c.risk_likelihood, "desc"),
      risk_asc: compareBy((c) => c.risk_impact * c.risk_likelihood, "asc"),
      ref: compareBy((c) => refNum(c.ref), "asc"),
    };
    filtered = filtered.slice().sort(sorters[ctrl.sort] || sorters.risk_desc);
    body.innerHTML = filtered.length === 0 ? `<div class="empty-state" style="margin-bottom:20px;">No reports match this filter.</div>` : filtered.map((c) => `
      <div class="card">
        <div class="case-row">
          <div>
            <div class="case-id">
              ${escapeHtml(c.ref)}
              <span class="badge ${riskBadgeClass(riskBand(c.risk_impact, c.risk_likelihood))}">${riskBand(c.risk_impact, c.risk_likelihood)} risk</span>
            </div>
            <div class="case-meta">Subject ${escapeHtml(c.subject_code)} &middot; submitted by ${escapeHtml(c.officer_name || "OIC TBC")}</div>
          </div>
          <button class="btn btn-primary btn-sm" data-dash-review-case="${c.id}">Review</button>
        </div>
      </div>
    `).join("");
    body.querySelectorAll("[data-dash-review-case]").forEach((btn) => {
      btn.onclick = () => {
        caseDetailTab = "investigation";  // the report/approve UI lives here, not the default Intelligence tab
        setView("case-detail", { caseId: Number(btn.dataset.dashReviewCase), origin: "dashboard" });
      };
    });
  };

  const redrawDashCases = () => {
    const body = document.getElementById("dash-cases-body");
    if (myCases.length === 0) { body.innerHTML = `<div class="empty-state" style="margin-bottom:20px;">No cases currently allocated to you.</div>`; return; }
    const ctrl = listControl(casesKey, casesToolbar.sorts[0].value);
    let filtered = myCases.filter((c) => matchesQuery(ctrl.q, [c.ref, c.subject_code, c.category]));
    const sorters = {
      risk_desc: compareBy((c) => c.risk_impact * c.risk_likelihood, "desc"),
      risk_asc: compareBy((c) => c.risk_impact * c.risk_likelihood, "asc"),
      ref: compareBy((c) => refNum(c.ref), "asc"),
    };
    filtered = filtered.slice().sort(sorters[ctrl.sort] || sorters.risk_desc);
    body.innerHTML = filtered.length === 0 ? `<div class="empty-state" style="margin-bottom:20px;">No cases match this filter.</div>` : filtered.map((c) => `
      <div class="card">
        <div class="case-row">
          <div>
            <div class="case-id">
              ${escapeHtml(c.ref)}
              <span class="badge ${riskBadgeClass(riskBand(c.risk_impact, c.risk_likelihood))}">${riskBand(c.risk_impact, c.risk_likelihood)} risk</span>
            </div>
            <div class="case-meta">Subject ${escapeHtml(c.subject_code)} &middot; ${escapeHtml(t.stages[c.stage_index])}</div>
          </div>
          <button class="btn btn-primary btn-sm" data-dash-open-case="${c.id}">Open</button>
        </div>
      </div>
    `).join("");
    body.querySelectorAll("[data-dash-open-case]").forEach((btn) => {
      btn.onclick = () => setView("case-detail", { caseId: Number(btn.dataset.dashOpenCase), origin: "dashboard" });
    });
  };

  const redrawDashHub = () => {
    const body = document.getElementById("dash-hub-body");
    if (myTaskings.length === 0) { body.innerHTML = `<div class="empty-state">No Hub Taskings currently allocated to you.</div>`; return; }
    const ctrl = listControl(hubKey, hubToolbar.sorts[0].value);
    let filtered = myTaskings.filter((h) => matchesQuery(ctrl.q, [h.ref, h.task_description, h.subject_code]));
    const sorters = {
      due: compareBy((h) => looseDateValue(h.due_date), "asc"),
      priority: compareBy((h) => priorityWeight[h.priority] || 0, "desc"),
      ref: compareBy((h) => refNum(h.ref), "asc"),
    };
    filtered = filtered.slice().sort(sorters[ctrl.sort] || sorters.due);
    body.innerHTML = filtered.length === 0 ? `<div class="empty-state">No Hub Taskings match this filter.</div>` : filtered.map((h) => `
      <div class="card">
        <div class="case-row">
          <div>
            <div class="case-id">${escapeHtml(h.ref)} <span class="badge ${riskBadgeClass(h.priority === "High" ? "High" : h.priority === "Medium" ? "Medium" : "Low")}">${escapeHtml(h.priority)} priority</span></div>
            <div class="case-meta">${escapeHtml(h.task_description)}</div>
            <div class="case-meta">${h.subject_code ? `Subject ${escapeHtml(h.subject_code)} \u00b7 ` : ""}${escapeHtml(t.hub_stages[h.stage_index])} &middot; due ${escapeHtml(h.due_date)}</div>
          </div>
          <button class="btn btn-primary btn-sm" data-dash-open-hub="${h.id}">Open</button>
        </div>
      </div>
    `).join("");
    body.querySelectorAll("[data-dash-open-hub]").forEach((btn) => {
      btn.onclick = () => setView("hub-detail", { caseId: Number(btn.dataset.dashOpenHub), origin: "dashboard" });
    });
  };

  if (reviewQueue.length > 0) wireListToolbar(reviewKey, reviewToolbar, redrawDashReview);
  if (myCases.length > 0) wireListToolbar(casesKey, casesToolbar, redrawDashCases);
  if (myTaskings.length > 0) wireListToolbar(hubKey, hubToolbar, redrawDashHub);
  redrawDashReview();
  redrawDashCases();
  redrawDashHub();
}

/* ---------------------------------------------------------------------
 * Cases list
 * ------------------------------------------------------------------- */
async function renderCasesView() {
  root.innerHTML = `<div class="loading">Loading cases&hellip;</div>`;
  let cases;
  try {
    cases = await api("/cases");
  } catch (e) {
    root.innerHTML = `<div class="error-banner">${escapeHtml(e.message)}</div>`;
    return;
  }

  const stages = state.taxonomy.stages;
  const key = "cases";
  const toolbarOpts = {
    placeholder: "Filter by reference, subject, officer or category…",
    statusOptions: stages,
    statusLabel: "All stages",
    sorts: [
      { value: "created_desc", label: "Newest first" },
      { value: "created_asc", label: "Oldest first" },
      { value: "risk_desc", label: "Risk (high to low)" },
      { value: "risk_asc", label: "Risk (low to high)" },
      { value: "ref", label: "Reference" },
    ],
  };

  root.innerHTML = `
    <div class="banner">
      <div class="eyebrow">Counter Corruption Unit</div>
      <h1>CCU Cases</h1>
      <p>All corruption cases visible to you, per your role and allocation.</p>
    </div>
    ${renderListToolbar(key, toolbarOpts)}
    <div id="list-body"></div>
  `;

  const redraw = () => {
    const ctrl = listControl(key, toolbarOpts.sorts[0].value);
    let filtered = cases.filter((c) => matchesQuery(ctrl.q, [c.ref, c.subject_code, c.officer_name, c.category]));
    if (ctrl.status) filtered = filtered.filter((c) => stages[c.stage_index] === ctrl.status);
    const sorters = {
      created_desc: compareBy((c) => looseDateValue(c.created_at), "desc"),
      created_asc: compareBy((c) => looseDateValue(c.created_at), "asc"),
      risk_desc: compareBy((c) => c.risk_impact * c.risk_likelihood, "desc"),
      risk_asc: compareBy((c) => c.risk_impact * c.risk_likelihood, "asc"),
      ref: compareBy((c) => refNum(c.ref), "asc"),
    };
    filtered = filtered.slice().sort(sorters[ctrl.sort] || sorters.created_desc);

    const body = document.getElementById("list-body");
    body.innerHTML = `
      <div class="card-title" style="margin-bottom:10px;">Cases (${filtered.length} of ${cases.length})</div>
      ${filtered.length === 0 ? `<div class="empty-state">No cases match this filter.</div>` : filtered.map((c) => `
        <div class="card">
          <div class="case-row">
            <div>
              <div class="case-id">
                ${escapeHtml(c.ref)}
                <span class="badge ${riskBadgeClass(riskBand(c.risk_impact, c.risk_likelihood))}">
                  ${riskBand(c.risk_impact, c.risk_likelihood)} risk
                </span>
              </div>
              <div class="case-meta">
                Subject ${escapeHtml(c.subject_code)} &middot;
                ${escapeHtml(c.officer_name || "OIC TBC")} &middot;
                ${escapeHtml(stages[c.stage_index])}
              </div>
            </div>
            <button class="btn btn-primary btn-sm" data-open-case="${c.id}">Open</button>
          </div>
        </div>
      `).join("")}
    `;
    body.querySelectorAll("[data-open-case]").forEach((btn) => {
      btn.onclick = () => setView("case-detail", { caseId: Number(btn.dataset.openCase), origin: "cases" });
    });
  };

  wireListToolbar(key, toolbarOpts, redraw);
  redraw();
}

/* ---------------------------------------------------------------------
 * New case / record intelligence
 * ------------------------------------------------------------------- */
function renderNewCaseView() {
  const t = state.taxonomy;
  const opt = (arr, sel) => arr.map((v) => `<option value="${escapeHtml(v)}" ${v === sel ? "selected" : ""}>${escapeHtml(v)}</option>`).join("");

  root.innerHTML = `
    <div class="card">
      <div class="card-title">Record intelligence</div>
      <div id="new-case-error"></div>

      <div class="card-title" style="margin-top:0;">Draft from a document (optional)</div>
      <p style="font-size:12px;color:var(--muted);margin-top:-6px;">
        Upload an email, Word document or report (.txt, .eml, .docx, .pdf). The AI will
        propose values for the fields below from its content — review and correct
        every field before creating the record; nothing is saved automatically.
      </p>
      <div id="intel-dropzone" class="dropzone">
        <div id="intel-dropzone-idle">
          <div style="font-weight:600;font-size:13px;">Drop a file here, or click to browse</div>
          <div style="font-size:12px;color:var(--muted);margin-top:4px;">.txt &middot; .eml &middot; .docx &middot; .pdf (text layer only) &middot; up to 8MB</div>
        </div>
        <div id="intel-dropzone-busy" style="display:none;font-size:13px;color:var(--muted);">Reading and drafting…</div>
      </div>
      <input type="file" id="intel-file-input" accept=".txt,.eml,.docx,.pdf" style="display:none;" />
      <div id="intel-draft-note"></div>

      <div class="field" style="margin-top:18px;"><label>Subject</label><input id="f-subject" placeholder="e.g. PC 1234 (existing or new)" /></div>
      <div class="field"><label>Source type</label><select id="f-source">${opt(t.source_types)}</select></div>
      <div class="field"><label>Category</label><select id="f-category">${opt(t.intel_categories)}</select></div>
      <div class="field"><label>Linked crime/incident reference (optional)</label><input id="f-crime-ref" /></div>
      <div class="field"><label>Information content (what, when, where, why, who, how)</label>
        <textarea id="f-summary" rows="4" placeholder="What has been received..."></textarea></div>

      <div class="card-title" style="margin-top:18px;">Grading (3x5x2)</div>
      <div class="field"><label>Source evaluation</label><select id="f-source-eval">${opt(t.source_evaluation)}</select></div>
      <div class="field"><label>Intelligence evaluation</label><select id="f-intel-eval">${opt(t.intelligence_evaluation)}</select></div>
      <div class="field"><label>Handling code</label><select id="f-handling-code">${opt(t.handling_codes)}</select></div>
      <div class="field" id="f-handling-conditions-wrap" style="display:none;">
        <label>Handling conditions</label><select id="f-handling-conditions">${opt(t.handling_conditions)}</select></div>
      <div class="field"><label>Government security classification</label><select id="f-gsc">${opt(t.gsc_levels, "OFFICIAL-SENSITIVE")}</select></div>
      <div class="field"><label>Intelligence source reference (ISR, optional -- never a name)</label><input id="f-isr" /></div>

      <div class="card-title" style="margin-top:18px;">Optional</div>
      <div class="field"><label>Sanitised version for wider dissemination</label><textarea id="f-sanitised" rows="3"></textarea></div>
      <div class="field"><label>Retention review date</label><input id="f-review-date" placeholder="e.g. 07 September 2027" /></div>

      <div class="modal-actions">
        <button class="btn" id="new-case-cancel">Cancel</button>
        <button class="btn btn-primary" id="new-case-submit">Create intelligence record</button>
      </div>
    </div>
  `;

  document.getElementById("f-handling-code").onchange = (e) => {
    document.getElementById("f-handling-conditions-wrap").style.display = e.target.value === "C" ? "block" : "none";
  };

  const dropzone = document.getElementById("intel-dropzone");
  const fileInput = document.getElementById("intel-file-input");

  const setDropzoneBusy = (busy) => {
    document.getElementById("intel-dropzone-idle").style.display = busy ? "none" : "block";
    document.getElementById("intel-dropzone-busy").style.display = busy ? "block" : "none";
    dropzone.classList.toggle("busy", busy);
  };

  const applyDraft = (draft) => {
    if (draft.subject_code) document.getElementById("f-subject").value = draft.subject_code;
    if (draft.source) document.getElementById("f-source").value = draft.source;
    if (draft.category) document.getElementById("f-category").value = draft.category;
    if (draft.crime_ref) document.getElementById("f-crime-ref").value = draft.crime_ref;
    if (draft.summary) document.getElementById("f-summary").value = draft.summary;
    if (draft.source_evaluation) document.getElementById("f-source-eval").value = draft.source_evaluation;
    if (draft.intelligence_evaluation) document.getElementById("f-intel-eval").value = draft.intelligence_evaluation;
    if (draft.handling_code) {
      document.getElementById("f-handling-code").value = draft.handling_code;
      document.getElementById("f-handling-conditions-wrap").style.display = draft.handling_code === "C" ? "block" : "none";
    }
    if (draft.handling_conditions) document.getElementById("f-handling-conditions").value = draft.handling_conditions;
    if (draft.sanitised) document.getElementById("f-sanitised").value = draft.sanitised;

    const noteBox = document.getElementById("intel-draft-note");
    const warning = draft.warning ? `<div class="error-banner">${escapeHtml(draft.warning)}</div>` : "";
    const mode = draft.llm_mode === "azure"
      ? `<p style="font-size:11px;color:#B9C0C9;margin:6px 0 0;">AI-drafted from the uploaded file — every field above is editable; check each one before creating the record.</p>`
      : `<p style="font-size:11px;color:var(--amber-text);margin:6px 0 0;">Drafted in mock mode (no AI configured) — the summary field holds the raw extracted text; every other field needs filling in by hand.</p>`;
    noteBox.innerHTML = warning + mode;
  };

  const handleFile = async (file) => {
    if (!file) return;
    const errBox = document.getElementById("new-case-error");
    errBox.innerHTML = "";
    document.getElementById("intel-draft-note").innerHTML = "";
    setDropzoneBusy(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const headers = {};
      if (state.user) {
        headers["X-User-Principal"] = state.user.principal;
        headers["X-User-Name"] = state.user.display_name;
      }
      const res = await fetch("/api/intel/extract", { method: "POST", body: form, headers });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error((data && data.detail) || `Request failed (${res.status})`);
      applyDraft(data);
    } catch (e) {
      errBox.innerHTML = `<div class="error-banner">${escapeHtml(e.message)}</div>`;
    } finally {
      setDropzoneBusy(false);
      fileInput.value = "";
    }
  };

  dropzone.onclick = () => fileInput.click();
  fileInput.onchange = () => handleFile(fileInput.files[0]);
  dropzone.ondragover = (e) => { e.preventDefault(); dropzone.classList.add("dragover"); };
  dropzone.ondragleave = () => dropzone.classList.remove("dragover");
  dropzone.ondrop = (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
    if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
  };

  document.getElementById("new-case-cancel").onclick = () => setView("cases");

  document.getElementById("new-case-submit").onclick = async () => {
    const errBox = document.getElementById("new-case-error");
    errBox.innerHTML = "";
    const body = {
      subject_code: document.getElementById("f-subject").value.trim(),
      source: document.getElementById("f-source").value,
      category: document.getElementById("f-category").value,
      crime_ref: document.getElementById("f-crime-ref").value.trim(),
      summary: document.getElementById("f-summary").value.trim(),
      source_evaluation: document.getElementById("f-source-eval").value,
      intelligence_evaluation: document.getElementById("f-intel-eval").value,
      handling_code: document.getElementById("f-handling-code").value,
      handling_conditions: document.getElementById("f-handling-code").value === "C"
        ? document.getElementById("f-handling-conditions").value : "None",
      gsc: document.getElementById("f-gsc").value,
      source_reference: document.getElementById("f-isr").value.trim(),
      sanitised: document.getElementById("f-sanitised").value.trim(),
      review_date: document.getElementById("f-review-date").value.trim() || "TBC",
    };
    if (!body.subject_code || !body.summary) {
      errBox.innerHTML = `<div class="error-banner">Subject and information content are required.</div>`;
      return;
    }
    try {
      const result = await api("/cases", { method: "POST", body: JSON.stringify(body) });
      setView("case-detail", { caseId: result.id });
    } catch (e) {
      errBox.innerHTML = `<div class="error-banner">${escapeHtml(e.message)}</div>`;
    }
  };
}

/* ---------------------------------------------------------------------
 * Case detail
 * ------------------------------------------------------------------- */
let caseDetailTab = "intel";

async function renderCaseDetailView(caseId) {
  root.innerHTML = `<div class="loading">Loading case&hellip;</div>`;
  let data, users;
  try {
    [data, users] = await Promise.all([api(`/cases/${caseId}`), api("/users")]);
  } catch (e) {
    root.innerHTML = `<div class="error-banner">${escapeHtml(e.message)}</div>`;
    return;
  }

  const c = data.case;
  const t = state.taxonomy;
  const band = riskBand(c.risk_impact, c.risk_likelihood);

  const origin = state.detailOrigin;
  const backLabel = origin === "dashboard" ? "Dashboard" : "All cases";

  root.innerHTML = `
    <div class="breadcrumb"><button id="back-to-cases">&larr; ${escapeHtml(backLabel)}</button> / ${escapeHtml(c.ref)}</div>

    <div class="card">
      <div class="case-row">
        <div class="case-id">${escapeHtml(c.ref)} <span class="badge ${riskBadgeClass(band)}">${band} risk</span></div>
        <div>
          <label style="font-size:12px;color:var(--muted);margin-right:6px;">Officer in charge</label>
          <select id="officer-select">
            <option value="">OIC TBC</option>
            ${users.map((u) => `<option value="${escapeHtml(u.principal)}" ${u.principal === c.officer_principal ? "selected" : ""}>${escapeHtml(u.display_name)}</option>`).join("")}
          </select>
        </div>
      </div>
    </div>

    <div class="tabs">
      <button class="tab-btn" data-tab="intel">Intelligence</button>
      <button class="tab-btn" data-tab="risk">Risk assessment</button>
      <button class="tab-btn" data-tab="adc">ADC</button>
      <button class="tab-btn" data-tab="investigation">Investigation</button>
    </div>
    <div id="tab-content"></div>
  `;

  document.getElementById("back-to-cases").onclick = () => setView(origin === "dashboard" ? "dashboard" : "cases");

  document.getElementById("officer-select").onchange = async (e) => {
    try {
      await api(`/cases/${caseId}/officer`, {
        method: "POST",
        body: JSON.stringify({ officer_principal: e.target.value || null }),
      });
      renderCaseDetailView(caseId);
    } catch (err) {
      alert("Could not reassign officer: " + err.message);
    }
  };

  root.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === caseDetailTab);
    btn.onclick = () => { caseDetailTab = btn.dataset.tab; renderCaseDetailView(caseId); };
  });

  const tabContent = document.getElementById("tab-content");
  if (caseDetailTab === "intel") renderIntelTab(tabContent, data);
  if (caseDetailTab === "risk") renderRiskTab(tabContent, caseId, c);
  if (caseDetailTab === "adc") renderAdcTab(tabContent, caseId, data, users);
  if (caseDetailTab === "investigation") renderInvestigationTab(tabContent, caseId, data, users);
}

function fieldRow(label, value) {
  return `<div class="field-row"><span class="label">${escapeHtml(label)}</span><span class="value">${escapeHtml(value)}</span></div>`;
}

function renderIntelTab(el, data) {
  const i = data.intel;
  if (!i) { el.innerHTML = `<div class="empty-state">No intelligence record found for this case.</div>`; return; }
  el.innerHTML = `
    <div style="text-align:center;font-size:12px;font-weight:600;padding:6px;border-radius:6px;background:#F3E7E7;color:#9C2B2B;margin-bottom:14px;">
      ${escapeHtml(i.gsc)}
    </div>
    <div class="card">
      <div class="card-title">Intelligence report</div>
      ${fieldRow("URN", i.urn)}
      ${fieldRow("Date/time of report", i.received_at)}
      ${fieldRow("Source type", i.source_type)}
      ${fieldRow("Intelligence source reference (ISR)", i.source_reference || "Not applicable")}
      ${fieldRow("Linked crime/incident reference", i.crime_ref || "None")}
    </div>
    <div class="card">
      <div class="card-title">Grading (3x5x2)
        <span class="badge badge-neutral">${escapeHtml(i.source_evaluation)}/${escapeHtml(i.intelligence_evaluation)}/${escapeHtml(i.handling_code)}</span>
      </div>
      ${fieldRow("Source evaluation", i.source_evaluation)}
      ${fieldRow("Intelligence evaluation", i.intelligence_evaluation)}
      ${fieldRow("Handling code", i.handling_code)}
      ${i.handling_code === "C" ? fieldRow("Handling conditions", i.handling_conditions) : ""}
      ${fieldRow("Retention review date", i.review_date)}
    </div>
    <div class="card">
      <div class="card-title">Information content</div>
      <p style="font-size:13px;line-height:1.6;">${escapeHtml(i.summary)}</p>
    </div>
    ${i.sanitised ? `<div class="card"><div class="card-title">Sanitised version</div><p style="font-size:13px;">${escapeHtml(i.sanitised)}</p></div>` : ""}
  `;
}

function renderRiskTab(el, caseId, c) {
  const t = state.taxonomy;
  el.innerHTML = `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px;">
      <div class="card">
        <div class="card-title">Impact</div>
        <select id="risk-impact">
          ${t.impact_labels.map((l, idx) => `<option value="${idx + 1}" ${idx + 1 === c.risk_impact ? "selected" : ""}>${escapeHtml(l)}</option>`).join("")}
        </select>
      </div>
      <div class="card">
        <div class="card-title">Likelihood</div>
        <select id="risk-likelihood">
          ${t.likelihood_labels.map((l, idx) => `<option value="${idx + 1}" ${idx + 1 === c.risk_likelihood ? "selected" : ""}>${escapeHtml(l)}</option>`).join("")}
        </select>
      </div>
    </div>
    <div class="card">
      <div class="card-title">Risk score <span id="risk-score-badge" class="badge ${riskBadgeClass(riskBand(c.risk_impact, c.risk_likelihood))}">${c.risk_impact * c.risk_likelihood} \u2014 ${riskBand(c.risk_impact, c.risk_likelihood)}</span></div>
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <label style="font-size:12px;color:var(--muted);">Rationale</label>
        <button class="btn btn-sm" id="risk-generate-rationale">Generate with AI</button>
      </div>
      <textarea id="risk-rationale" rows="6" style="width:100%;padding:8px;border:1px solid var(--border);border-radius:6px;font-family:inherit;font-size:13px;">${escapeHtml(c.risk_rationale)}</textarea>
      <div id="risk-rationale-note"></div>
      <div class="modal-actions"><button class="btn btn-primary" id="risk-save">Save risk assessment</button></div>
      <div id="risk-error"></div>
    </div>
  `;

  const updateBadge = () => {
    const impact = Number(document.getElementById("risk-impact").value);
    const likelihood = Number(document.getElementById("risk-likelihood").value);
    const score = impact * likelihood;
    const band = riskBand(impact, likelihood);
    const badge = document.getElementById("risk-score-badge");
    badge.textContent = `${score} \u2014 ${band}`;
    badge.className = `badge ${riskBadgeClass(band)}`;
  };
  document.getElementById("risk-impact").onchange = updateBadge;
  document.getElementById("risk-likelihood").onchange = updateBadge;

  document.getElementById("risk-generate-rationale").onclick = async (e) => {
    const btn = e.target;
    const noteBox = document.getElementById("risk-rationale-note");
    btn.disabled = true;
    btn.textContent = "Generating\u2026";
    noteBox.innerHTML = "";
    try {
      const result = await api(`/cases/${caseId}/risk/generate-rationale`, {
        method: "POST",
        body: JSON.stringify({
          impact: Number(document.getElementById("risk-impact").value),
          likelihood: Number(document.getElementById("risk-likelihood").value),
        }),
      });
      document.getElementById("risk-rationale").value = result.rationale;
      if (result.llm_mode !== "azure") {
        noteBox.innerHTML = `<p style="font-size:11px;color:var(--amber-text);margin:4px 0 0;">Drafted in mock mode (no AI configured) \u2014 this is a placeholder, not a real drafted rationale.</p>`;
      } else {
        noteBox.innerHTML = `<p style="font-size:11px;color:#B9C0C9;margin:4px 0 0;">AI-drafted \u2014 review and edit before saving.</p>`;
      }
    } catch (err) {
      noteBox.innerHTML = `<div class="error-banner">${escapeHtml(err.message)}</div>`;
    } finally {
      btn.disabled = false;
      btn.textContent = "Generate with AI";
    }
  };

  document.getElementById("risk-save").onclick = async () => {
    try {
      await api(`/cases/${caseId}/risk`, {
        method: "POST",
        body: JSON.stringify({
          impact: Number(document.getElementById("risk-impact").value),
          likelihood: Number(document.getElementById("risk-likelihood").value),
          rationale: document.getElementById("risk-rationale").value,
        }),
      });
      renderCaseDetailView(caseId);
    } catch (e) {
      document.getElementById("risk-error").innerHTML = `<div class="error-banner">${escapeHtml(e.message)}</div>`;
    }
  };
}

function renderAdcTab(el, caseId, data, users) {
  const t = state.taxonomy;
  const adc = data.adc;
  let selectedDecision = adc ? adc.decision : null;

  el.innerHTML = `
    ${adc ? `<div class="card">${fieldRow("Date considered", adc.considered_at)}</div>` : ""}
    <div class="card">
      <div class="card-title">Decision</div>
      <p style="font-size:12px;color:var(--muted);">
        ${adc ? "Select the ADC decision" : "This case has not yet been referred to the Assessment Development Cell \u2014 record a decision below."}
      </p>
      <div class="chip-group" id="adc-chips">
        ${t.adc_decisions.map((d) => `<span class="chip ${d === selectedDecision ? "selected" : ""}" data-decision="${escapeHtml(d)}">${escapeHtml(d)}</span>`).join("")}
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <label style="font-size:12px;color:var(--muted);">Rationale</label>
        <button class="btn btn-sm" id="adc-generate-rationale">Generate with AI</button>
      </div>
      <textarea id="adc-rationale" rows="6" style="width:100%;padding:8px;border:1px solid var(--border);border-radius:6px;font-family:inherit;font-size:13px;">${escapeHtml(adc ? adc.rationale : "")}</textarea>
      <div id="adc-rationale-note"></div>
      <label style="font-size:12px;color:var(--muted);margin-top:8px;display:block;">Action owner</label>
      <select id="adc-owner">
        ${users.map((u) => `<option value="${escapeHtml(u.principal)}" ${adc && adc.action_owner_id && u.display_name === adc.action_owner_name ? "selected" : ""}>${escapeHtml(u.display_name)}</option>`).join("")}
      </select>
      <div class="modal-actions">
        <button class="btn btn-primary" id="adc-save">${adc ? "Save decision" : "Record ADC decision"}</button>
      </div>
      <div id="adc-error"></div>
    </div>
  `;

  el.querySelectorAll("[data-decision]").forEach((chip) => {
    chip.onclick = () => {
      selectedDecision = chip.dataset.decision;
      el.querySelectorAll("[data-decision]").forEach((c) => c.classList.toggle("selected", c.dataset.decision === selectedDecision));
    };
  });

  document.getElementById("adc-generate-rationale").onclick = async (e) => {
    const btn = e.target;
    const noteBox = document.getElementById("adc-rationale-note");
    noteBox.innerHTML = "";
    if (!selectedDecision) {
      noteBox.innerHTML = `<div class="error-banner">Select a decision above first \u2014 the rationale is drafted to justify the specific decision chosen.</div>`;
      return;
    }
    btn.disabled = true;
    btn.textContent = "Generating\u2026";
    try {
      const result = await api(`/cases/${caseId}/adc/generate-rationale`, {
        method: "POST",
        body: JSON.stringify({ decision: selectedDecision }),
      });
      document.getElementById("adc-rationale").value = result.rationale;
      if (result.llm_mode !== "azure") {
        noteBox.innerHTML = `<p style="font-size:11px;color:var(--amber-text);margin:4px 0 0;">Drafted in mock mode (no AI configured) \u2014 this is a placeholder, not a real drafted rationale.</p>`;
      } else {
        noteBox.innerHTML = `<p style="font-size:11px;color:#B9C0C9;margin:4px 0 0;">AI-drafted \u2014 review and edit before saving.</p>`;
      }
    } catch (err) {
      noteBox.innerHTML = `<div class="error-banner">${escapeHtml(err.message)}${err.status === 403 ? " (requires a supervisor role.)" : ""}</div>`;
    } finally {
      btn.disabled = false;
      btn.textContent = "Generate with AI";
    }
  };

  document.getElementById("adc-save").onclick = async () => {
    const errBox = document.getElementById("adc-error");
    if (!selectedDecision) { errBox.innerHTML = `<div class="error-banner">Select a decision first.</div>`; return; }
    try {
      await api(`/cases/${caseId}/adc`, {
        method: "POST",
        body: JSON.stringify({
          decision: selectedDecision,
          rationale: document.getElementById("adc-rationale").value,
          action_owner_principal: document.getElementById("adc-owner").value,
        }),
      });
      renderCaseDetailView(caseId);
    } catch (e) {
      errBox.innerHTML = `<div class="error-banner">${escapeHtml(e.message)}${e.status === 403 ? " (ADC decisions require a supervisor role.)" : ""}</div>`;
    }
  };
}

function renderInvestigationTab(el, caseId, data, users) {
  const inv = data.investigation;
  const actions = data.actions;
  const updates = data.updates;
  const t = state.taxonomy;

  el.innerHTML = `
    <div class="modal-actions" style="justify-content:flex-end;margin-bottom:14px;">
      <button class="btn btn-primary" id="generate-report-btn">Generate report</button>
    </div>

    ${inv ? `
      <div class="card"><div class="card-title">Investigation plan</div><p style="font-size:13px;">${escapeHtml(inv.plan)}</p></div>
      <div class="card">${fieldRow("Next review date", inv.review_date)}${fieldRow("Current outcome", inv.outcome)}</div>
    ` : `<div class="card"><div class="empty-state" style="padding:20px;">This case has not progressed to a formal investigation.</div></div>`}

    <div class="card">
      <div class="card-title">Actions (${actions.length})</div>
      ${actions.length === 0 ? `<div class="empty-state" style="padding:20px;">No actions have been generated for this case yet.</div>` :
        actions.map((a) => `
          <div class="action-item">
            <div>
              <div style="font-weight:500;font-size:13px;">${escapeHtml(a.title)}</div>
              <div style="font-size:12px;color:var(--muted);">${escapeHtml(a.owner_name || "Unassigned")} &middot; due ${escapeHtml(a.due_date)} &middot; ${escapeHtml(a.priority)} priority</div>
            </div>
            <select data-action-status="${a.id}">
              ${t.action_statuses.map((s) => `<option value="${escapeHtml(s)}" ${s === a.status ? "selected" : ""}>${escapeHtml(s)}</option>`).join("")}
            </select>
          </div>
        `).join("")}
      <div style="border-top:1px solid var(--border);padding-top:12px;margin-top:12px;">
        <div class="field"><label>Action</label><input id="new-action-title" placeholder="e.g. Obtain phone billing records" /></div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
          <div class="field"><label>Owner</label><select id="new-action-owner">${users.map((u) => `<option value="${escapeHtml(u.principal)}">${escapeHtml(u.display_name)}</option>`).join("")}</select></div>
          <div class="field"><label>Priority</label><select id="new-action-priority">${t.action_priorities.map((p) => `<option>${escapeHtml(p)}</option>`).join("")}</select></div>
        </div>
        <div class="field"><label>Due date</label><input id="new-action-due" placeholder="e.g. 15 Sep 2026" /></div>
        <div class="modal-actions"><button class="btn btn-primary" id="add-action-btn">Add action</button></div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">Case updates (${updates.length})</div>
      ${updates.length === 0 ? `<div class="empty-state" style="padding:16px;">No updates recorded yet.</div>` :
        [...updates].reverse().map((u) => `
          <div class="update-item">
            <div class="update-head"><span class="role">${escapeHtml(u.entered_by_role_label)} (${escapeHtml(u.entered_by_name)})</span><span class="ts">${escapeHtml(u.created_at)}</span></div>
            <div style="font-size:13px;">${escapeHtml(u.text)}</div>
          </div>
        `).join("")}
      <div style="border-top:1px solid var(--border);padding-top:12px;margin-top:12px;">
        <label style="font-size:12px;color:var(--muted);">Entered as</label>
        <select id="update-role">${t.update_entry_roles.map((r) => `<option>${escapeHtml(r)}</option>`).join("")}</select>
        <label style="font-size:12px;color:var(--muted);margin-top:8px;display:block;">Update</label>
        <textarea id="update-text" rows="3" style="width:100%;padding:8px;border:1px solid var(--border);border-radius:6px;font-family:inherit;font-size:13px;" placeholder="Add a timestamped update&hellip;"></textarea>
        <p style="font-size:11px;color:#B9C0C9;margin:4px 0 0;">Updates are timestamped automatically and cannot be edited once saved.</p>
        <div class="modal-actions"><button class="btn btn-primary" id="add-update-btn">Add update</button></div>
      </div>
    </div>
  `;

  el.querySelectorAll("[data-action-status]").forEach((sel) => {
    sel.onchange = async () => {
      try {
        await api(`/actions/${sel.dataset.actionStatus}/status`, { method: "POST", body: JSON.stringify({ status: sel.value }) });
      } catch (e) { alert("Could not update status: " + e.message); renderCaseDetailView(caseId); }
    };
  });

  document.getElementById("add-action-btn").onclick = async () => {
    const title = document.getElementById("new-action-title").value.trim();
    const due = document.getElementById("new-action-due").value.trim();
    if (!title || !due) { alert("Action and due date are required."); return; }
    try {
      await api(`/cases/${caseId}/actions`, {
        method: "POST",
        body: JSON.stringify({
          title, due_date: due,
          owner_principal: document.getElementById("new-action-owner").value,
          priority: document.getElementById("new-action-priority").value,
        }),
      });
      renderCaseDetailView(caseId);
    } catch (e) { alert("Could not add action: " + e.message); }
  };

  document.getElementById("add-update-btn").onclick = async () => {
    const text = document.getElementById("update-text").value.trim();
    if (!text) return;
    try {
      await api(`/cases/${caseId}/updates`, {
        method: "POST",
        body: JSON.stringify({ text, role_label: document.getElementById("update-role").value }),
      });
      renderCaseDetailView(caseId);
    } catch (e) { alert("Could not add update: " + e.message); }
  };

  document.getElementById("generate-report-btn").onclick = () => openReportModal(caseId, data.reports);
}

/* ---------------------------------------------------------------------
 * Report modal: generate -> edit -> submit -> (supervisor) approve/return
 * ------------------------------------------------------------------- */
async function openReportModal(caseId, existingReports) {
  modalRoot.innerHTML = `
    <div class="modal-overlay">
      <div class="modal-box">
        <div class="modal-head"><h2>Case report</h2><button class="btn btn-sm" id="report-close">Close</button></div>
        <div id="report-body-wrap"><div class="loading">Generating report&hellip;</div></div>
      </div>
    </div>`;
  document.getElementById("report-close").onclick = () => { modalRoot.innerHTML = ""; };

  let reportId, reportText, reportStatus = "draft";

  try {
    const latest = existingReports && existingReports[0];
    if (latest && latest.status !== "draft") {
      const full = await api(`/reports/${latest.id}`);
      reportId = full.id; reportText = full.body; reportStatus = full.status;
      renderReportBody(caseId, reportId, reportText, reportStatus, full.stale);
      return;
    }
    const result = await api(`/cases/${caseId}/report/generate`, { method: "POST" });
    reportId = result.report_id; reportText = result.body;
    renderReportBody(caseId, reportId, reportText, "draft", false);
  } catch (e) {
    document.getElementById("report-body-wrap").innerHTML = `<div class="error-banner">${escapeHtml(e.message)}</div>`;
  }
}

function renderReportBody(caseId, reportId, text, status, stale) {
  const wrap = document.getElementById("report-body-wrap");
  const locked = status !== "draft" && status !== "returned";

  wrap.innerHTML = `
    ${stale ? `<div class="stale-banner">This report is stale \u2014 the case has changed since it was built. Regenerate before sending.</div>` : ""}
    <div style="font-size:11px;color:var(--muted);margin-bottom:6px;">Status: ${escapeHtml(status)}</div>
    ${locked
      ? `<div class="report-body">${escapeHtml(text)}</div>`
      : `<textarea class="report-edit" id="report-edit">${escapeHtml(text)}</textarea>`}
    <div class="modal-actions">
      ${!locked ? `<button class="btn" id="report-regenerate">Regenerate</button>` : ""}
      ${!locked ? `<button class="btn btn-primary" id="report-send">Send to supervisor</button>` : ""}
      ${status === "submitted" ? `
        <button class="btn" id="report-return">Return with message</button>
        <button class="btn btn-primary" id="report-approve">Approve</button>
      ` : ""}
    </div>
    <div id="report-error"></div>
  `;

  if (!locked) {
    document.getElementById("report-regenerate").onclick = async () => {
      wrap.innerHTML = `<div class="loading">Generating report&hellip;</div>`;
      try {
        const result = await api(`/cases/${caseId}/report/generate`, { method: "POST" });
        renderReportBody(caseId, result.report_id, result.body, "draft", false);
      } catch (e) {
        wrap.innerHTML = `<div class="error-banner">${escapeHtml(e.message)}</div>`;
      }
    };

    document.getElementById("report-send").onclick = async () => {
      const edited = document.getElementById("report-edit").value;
      try {
        await api(`/reports/${reportId}`, { method: "PATCH", body: JSON.stringify({ body: edited }) });
        openAttestationModal(caseId, reportId);
      } catch (e) {
        document.getElementById("report-error").innerHTML = `<div class="error-banner">${escapeHtml(e.message)}</div>`;
      }
    };
  }

  if (status === "submitted") {
    document.getElementById("report-approve").onclick = async () => {
      try {
        await api(`/reports/${reportId}/approve`, { method: "POST" });
        modalRoot.innerHTML = "";
        renderCaseDetailView(caseId);
      } catch (e) {
        document.getElementById("report-error").innerHTML = `<div class="error-banner">${escapeHtml(e.message)}</div>`;
      }
    };
    document.getElementById("report-return").onclick = async () => {
      const message = prompt("Message to the officer explaining what needs changing:");
      if (message === null) return;
      try {
        await api(`/reports/${reportId}/return`, { method: "POST", body: JSON.stringify({ message }) });
        modalRoot.innerHTML = "";
        renderCaseDetailView(caseId);
      } catch (e) {
        document.getElementById("report-error").innerHTML = `<div class="error-banner">${escapeHtml(e.message)}</div>`;
      }
    };
  }
}

async function openAttestationModal(caseId, reportId) {
  let statements, checksResult;
  try {
    [statements, checksResult] = await Promise.all([
      api("/attestation-statements"),
      api(`/cases/${caseId}/checks`),
    ]);
  } catch (e) {
    alert("Could not load attestation statements: " + e.message);
    return;
  }

  const failedChecks = checksResult.deterministic.filter((c) => !c.passed);

  modalRoot.innerHTML = `
    <div class="modal-overlay">
      <div class="modal-box" style="max-width:560px;">
        <div class="modal-head"><h2>Confirm and send to supervisor</h2></div>
        <div class="error-banner" style="background:var(--amber-bg);color:var(--amber-text);">
          The sections above marked as AI-drafted were written by an AI model from the case
          facts. You are responsible for checking them before this goes to a supervisor.
        </div>

        <div class="card-title">Second reader (informational \u2014 does not block submission)</div>
        ${checksResult.deterministic.map((c) => `
          <div class="field-row">
            <span class="label">${c.passed ? "\u2713" : "\u26a0"} ${escapeHtml(c.check)}</span>
            <span class="value" style="color:${c.passed ? "var(--green-text)" : "var(--amber-text)"};">${c.passed ? "OK" : escapeHtml(c.message)}</span>
          </div>
        `).join("")}
        ${checksResult.ai_note ? `<div class="field-row"><span class="label">AI note</span><span class="value">${escapeHtml(checksResult.ai_note)}</span></div>` : ""}
        ${failedChecks.length > 0 ? `<p style="font-size:12px;color:var(--amber-text);margin-top:8px;">${failedChecks.length} check(s) did not pass. This will not stop you sending the report, but the findings are stored with your attestation.</p>` : ""}

        <div class="card-title" style="margin-top:16px;">Attestation</div>
        ${statements.map((s, i) => `
          <div class="attestation-item">
            <input type="checkbox" id="att-${i}" />
            <label for="att-${i}">${escapeHtml(s)}</label>
          </div>
        `).join("")}
        <div class="field" style="margin-top:12px;">
          <label>Type your full name to confirm (must match your signed-in identity)</label>
          <input id="att-typed-name" placeholder="${escapeHtml(state.user.display_name)}" />
        </div>
        <div id="att-error"></div>
        <div class="modal-actions">
          <button class="btn" id="att-cancel">Cancel</button>
          <button class="btn btn-primary" id="att-submit">Send to supervisor</button>
        </div>
      </div>
    </div>`;

  document.getElementById("att-cancel").onclick = () => { modalRoot.innerHTML = ""; renderCaseDetailView(caseId); };

  document.getElementById("att-submit").onclick = async () => {
    const ticked = statements.map((_, i) => document.getElementById(`att-${i}`).checked);
    const typedName = document.getElementById("att-typed-name").value.trim();
    try {
      await api(`/reports/${reportId}/submit`, {
        method: "POST",
        body: JSON.stringify({ typed_name: typedName, statements_ticked: ticked }),
      });
      modalRoot.innerHTML = "";
      renderCaseDetailView(caseId);
    } catch (e) {
      document.getElementById("att-error").innerHTML = `<div class="error-banner">${escapeHtml(e.message)}</div>`;
    }
  };
}

/* ---------------------------------------------------------------------
 * Hub Taskings
 * ------------------------------------------------------------------- */
async function renderHubTaskingsView() {
  root.innerHTML = `<div class="loading">Loading Hub Taskings&hellip;</div>`;
  let taskings, users;
  try {
    [taskings, users] = await Promise.all([api("/hub-taskings"), api("/users")]);
  } catch (e) {
    root.innerHTML = `<div class="error-banner">${escapeHtml(e.message)}</div>`;
    return;
  }
  const t = state.taxonomy;
  const key = "hub";
  const toolbarOpts = {
    placeholder: "Filter by reference, description, subject or officer…",
    statusOptions: t.hub_stages,
    statusLabel: "All stages",
    sorts: [
      { value: "created_desc", label: "Newest first" },
      { value: "created_asc", label: "Oldest first" },
      { value: "priority", label: "Priority (high to low)" },
      { value: "due", label: "Due date (soonest first)" },
      { value: "ref", label: "Reference" },
    ],
  };
  const priorityWeight = { High: 3, Medium: 2, Low: 1 };

  root.innerHTML = `
    <div class="banner">
      <div class="eyebrow">Counter Corruption Unit</div>
      <h1>Hub Taskings</h1>
      <p>Taskings received from the Intelligence Hub and partner units.</p>
    </div>
    <div class="card">
      <div class="card-title">New tasking</div>
      <div class="field"><label>Source</label><input id="hub-source" value="Force Intelligence Hub" /></div>
      <div class="field"><label>Subject (optional)</label><input id="hub-subject" placeholder="e.g. PC 5521 -- leave blank if not subject-specific" /></div>
      <div class="field"><label>Task description</label><textarea id="hub-desc" rows="2"></textarea></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
        <div class="field"><label>Priority</label><select id="hub-priority">${t.action_priorities.map((p) => `<option>${escapeHtml(p)}</option>`).join("")}</select></div>
        <div class="field"><label>Due date</label><input id="hub-due" placeholder="e.g. 15 Sep 2026" /></div>
      </div>
      <div class="modal-actions"><button class="btn btn-primary" id="hub-create-btn">Add tasking</button></div>
      <div id="hub-create-error"></div>
    </div>
    ${renderListToolbar(key, toolbarOpts)}
    <div id="list-body"></div>
  `;

  const redraw = () => {
    const ctrl = listControl(key, toolbarOpts.sorts[0].value);
    let filtered = taskings.filter((h) => matchesQuery(ctrl.q, [h.ref, h.task_description, h.subject_code, h.allocated_officer_name]));
    if (ctrl.status) filtered = filtered.filter((h) => t.hub_stages[h.stage_index] === ctrl.status);
    const sorters = {
      created_desc: compareBy((h) => looseDateValue(h.created_at), "desc"),
      created_asc: compareBy((h) => looseDateValue(h.created_at), "asc"),
      priority: compareBy((h) => priorityWeight[h.priority] || 0, "desc"),
      due: compareBy((h) => looseDateValue(h.due_date), "asc"),
      ref: compareBy((h) => refNum(h.ref), "asc"),
    };
    filtered = filtered.slice().sort(sorters[ctrl.sort] || sorters.created_desc);

    const body = document.getElementById("list-body");
    body.innerHTML = `
      <div class="card-title" style="margin-bottom:10px;">Taskings (${filtered.length} of ${taskings.length})</div>
      ${filtered.length === 0 ? `<div class="empty-state">No Hub Taskings match this filter.</div>` : filtered.map((h) => `
        <div class="card">
          <div class="case-row">
            <div>
              <div class="case-id">${escapeHtml(h.ref)} <span class="badge ${riskBadgeClass(h.priority === "High" ? "High" : h.priority === "Medium" ? "Medium" : "Low")}">${escapeHtml(h.priority)} priority</span></div>
              <div class="case-meta">${escapeHtml(h.task_description)}</div>
              <div class="case-meta">
                ${h.subject_code ? `Subject ${escapeHtml(h.subject_code)} &middot; ` : ""}
                ${escapeHtml(h.allocated_officer_name || "OIC TBC")} &middot; ${escapeHtml(t.hub_stages[h.stage_index])} &middot; due ${escapeHtml(h.due_date)}
              </div>
            </div>
            <button class="btn btn-primary btn-sm" data-open-hub="${h.id}">Open</button>
          </div>
        </div>
      `).join("")}
    `;
    body.querySelectorAll("[data-open-hub]").forEach((btn) => {
      btn.onclick = () => setView("hub-detail", { caseId: Number(btn.dataset.openHub), origin: "hub" });
    });
  };

  document.getElementById("hub-create-btn").onclick = async () => {
    const desc = document.getElementById("hub-desc").value.trim();
    const due = document.getElementById("hub-due").value.trim();
    if (!desc || !due) { document.getElementById("hub-create-error").innerHTML = `<div class="error-banner">Description and due date are required.</div>`; return; }
    try {
      await api("/hub-taskings", {
        method: "POST",
        body: JSON.stringify({
          source: document.getElementById("hub-source").value.trim(),
          subject_code: document.getElementById("hub-subject").value.trim() || null,
          task_description: desc,
          priority: document.getElementById("hub-priority").value,
          due_date: due,
        }),
      });
      renderHubTaskingsView();
    } catch (e) {
      document.getElementById("hub-create-error").innerHTML = `<div class="error-banner">${escapeHtml(e.message)}</div>`;
    }
  };

  wireListToolbar(key, toolbarOpts, redraw);
  redraw();
}

async function renderHubTaskingDetailView(taskingId) {
  root.innerHTML = `<div class="loading">Loading&hellip;</div>`;
  let h, users;
  try {
    [h, users] = await Promise.all([api(`/hub-taskings/${taskingId}`), api("/users")]);
  } catch (e) {
    root.innerHTML = `<div class="error-banner">${escapeHtml(e.message)}</div>`;
    return;
  }
  const t = state.taxonomy;

  const origin = state.detailOrigin;
  const backLabel = origin === "dashboard" ? "Dashboard" : "All taskings";

  root.innerHTML = `
    <div class="breadcrumb"><button id="back-to-hub">&larr; ${escapeHtml(backLabel)}</button> / ${escapeHtml(h.ref)}</div>
    <div class="card">
      <div class="card-title">Hub tasking <span class="badge badge-neutral">${escapeHtml(t.hub_stages[h.stage_index])}</span></div>
      ${fieldRow("Reference", h.ref)}
      ${fieldRow("Source", h.source)}
      ${fieldRow("Subject", h.subject_code || "Not subject-specific")}
      ${fieldRow("Priority", h.priority)}
      ${fieldRow("Due date", h.due_date)}
      <div class="field-row">
        <span class="label">Allocated officer</span>
        <select id="hub-officer-select">
          <option value="">OIC TBC</option>
          ${users.map((u) => `<option value="${escapeHtml(u.principal)}" ${u.principal === h.allocated_officer_principal ? "selected" : ""}>${escapeHtml(u.display_name)}</option>`).join("")}
        </select>
      </div>
      <div style="margin-top:10px;">
        <label style="font-size:12px;color:var(--muted);">Stage</label>
        <select id="hub-stage-select">
          ${t.hub_stages.map((s, i) => `<option value="${escapeHtml(s)}" ${i === h.stage_index ? "selected" : ""}>${escapeHtml(s)}</option>`).join("")}
        </select>
      </div>
      <div id="hub-detail-error"></div>
    </div>
    <div class="card"><div class="card-title">Task description</div><p style="font-size:13px;">${escapeHtml(h.task_description)}</p></div>
  `;

  document.getElementById("back-to-hub").onclick = () => setView(origin === "dashboard" ? "dashboard" : "hub");

  document.getElementById("hub-officer-select").onchange = async (e) => {
    try {
      await api(`/hub-taskings/${taskingId}/officer`, { method: "POST", body: JSON.stringify({ officer_principal: e.target.value || null }) });
      renderHubTaskingDetailView(taskingId);
    } catch (err) {
      document.getElementById("hub-detail-error").innerHTML = `<div class="error-banner">${escapeHtml(err.message)}${err.status === 403 ? " (allocation requires a supervisor role.)" : ""}</div>`;
    }
  };

  document.getElementById("hub-stage-select").onchange = async (e) => {
    try {
      await api(`/hub-taskings/${taskingId}/stage`, { method: "POST", body: JSON.stringify({ stage: e.target.value }) });
      renderHubTaskingDetailView(taskingId);
    } catch (err) {
      document.getElementById("hub-detail-error").innerHTML = `<div class="error-banner">${escapeHtml(err.message)}</div>`;
      renderHubTaskingDetailView(taskingId);
    }
  };
}

/* ---------------------------------------------------------------------
 * NIA / BI (simple list + create -- no detail drill-in for this pass)
 * ------------------------------------------------------------------- */
function statusBadgeClass(status) {
  if (["Approved", "Cleared", "Managed"].includes(status)) return "badge-low";
  if (["Under review"].includes(status)) return "badge-medium";
  if (["Refused", "Restricted"].includes(status)) return "badge-high";
  return "badge-neutral";
}

async function renderNiaView() { renderModuleView("nia"); }
async function renderBiView() { renderModuleView("bi"); }

async function renderModuleView(kind) {
  const isNia = kind === "nia";
  const endpoint = isNia ? "/nia" : "/business-interests";
  const title = isNia ? "Notifiable Inappropriate Associations" : "Business Interests";
  const statuses = isNia ? state.taxonomy.risk_levels && ["Managed", "Under review", "Restricted"] : ["Approved", "Under review", "Refused"];

  root.innerHTML = `<div class="loading">Loading&hellip;</div>`;
  let records;
  try { records = await api(endpoint); } catch (e) {
    root.innerHTML = `<div class="error-banner">${escapeHtml(e.message)}</div>`; return;
  }

  const key = kind; // "nia" or "bi" -- separate filter/sort state per module
  const toolbarOpts = {
    placeholder: "Filter by reference, subject or summary…",
    statusOptions: statuses,
    statusLabel: "All statuses",
    sorts: [
      { value: "ref_desc", label: "Newest first" },
      { value: "ref_asc", label: "Oldest first" },
      { value: "subject", label: "Subject" },
      { value: "review", label: "Next review (soonest first)" },
    ],
  };

  root.innerHTML = `
    <div class="banner"><div class="eyebrow">Counter Corruption Unit</div><h1>${escapeHtml(title)}</h1></div>
    <div class="card">
      <div class="card-title">New record</div>
      <div class="field"><label>Subject</label><input id="mod-subject" placeholder="e.g. PC 5521" /></div>
      <div class="field"><label>Status</label><select id="mod-status">${statuses.map((s) => `<option>${escapeHtml(s)}</option>`).join("")}</select></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
        <div class="field"><label>Opened</label><input id="mod-opened" placeholder="e.g. 20 Jul 2026" /></div>
        <div class="field"><label>Next review</label><input id="mod-review" placeholder="e.g. 20 Jan 2027" /></div>
      </div>
      <div class="field"><label>Summary</label><textarea id="mod-summary" rows="2"></textarea></div>
      <div class="modal-actions"><button class="btn btn-primary" id="mod-create-btn">Add record</button></div>
      <div id="mod-create-error"></div>
    </div>
    ${renderListToolbar(key, toolbarOpts)}
    <div id="list-body"></div>
  `;

  const redraw = () => {
    const ctrl = listControl(key, toolbarOpts.sorts[0].value);
    let filtered = records.filter((r) => matchesQuery(ctrl.q, [r.ref, r.subject_code, r.summary]));
    if (ctrl.status) filtered = filtered.filter((r) => r.status === ctrl.status);
    const sorters = {
      ref_desc: compareBy((r) => refNum(r.ref), "desc"),
      ref_asc: compareBy((r) => refNum(r.ref), "asc"),
      subject: compareBy((r) => (r.subject_code || "").toLowerCase(), "asc"),
      review: compareBy((r) => looseDateValue(r.next_review), "asc"),
    };
    filtered = filtered.slice().sort(sorters[ctrl.sort] || sorters.ref_desc);

    const body = document.getElementById("list-body");
    body.innerHTML = `
      <div class="card-title" style="margin-bottom:10px;">Records (${filtered.length} of ${records.length})</div>
      ${filtered.length === 0 ? `<div class="empty-state">No records match this filter.</div>` : filtered.map((r) => `
        <div class="card">
          <div class="case-row">
            <div>
              <div class="case-id">${escapeHtml(r.ref)} <span class="badge ${statusBadgeClass(r.status)}">${escapeHtml(r.status)}</span></div>
              <div class="case-meta">Subject ${escapeHtml(r.subject_code)} &middot; opened ${escapeHtml(r.opened_at)} &middot; next review ${escapeHtml(r.next_review)}</div>
              <div class="case-meta">${escapeHtml(r.summary)}</div>
            </div>
          </div>
        </div>
      `).join("")}
    `;
  };

  document.getElementById("mod-create-btn").onclick = async () => {
    const subject = document.getElementById("mod-subject").value.trim();
    const summary = document.getElementById("mod-summary").value.trim();
    if (!subject || !summary) { document.getElementById("mod-create-error").innerHTML = `<div class="error-banner">Subject and summary are required.</div>`; return; }
    try {
      await api(endpoint, {
        method: "POST",
        body: JSON.stringify({
          subject_code: subject,
          status: document.getElementById("mod-status").value,
          opened_at: document.getElementById("mod-opened").value.trim() || "TBC",
          next_review: document.getElementById("mod-review").value.trim() || "TBC",
          summary,
        }),
      });
      renderModuleView(kind);
    } catch (e) {
      document.getElementById("mod-create-error").innerHTML = `<div class="error-banner">${escapeHtml(e.message)}</div>`;
    }
  };

  wireListToolbar(key, toolbarOpts, redraw);
  redraw();
}

/* ---------------------------------------------------------------------
 * PIRM
 * ------------------------------------------------------------------- */
async function renderPirmView() {
  root.innerHTML = `<div class="loading">Loading&hellip;</div>`;
  let scored;
  try { scored = await api("/pirm"); } catch (e) {
    root.innerHTML = `<div class="error-banner">${escapeHtml(e.message)}</div>`; return;
  }

  const key = "pirm";
  const toolbarOpts = {
    placeholder: "Filter by subject…",
    statusOptions: ["High", "Medium", "Low"],
    statusLabel: "All bands",
    sorts: [
      { value: "score_desc", label: "Score (high to low)" },
      { value: "score_asc", label: "Score (low to high)" },
      { value: "subject", label: "Subject" },
    ],
  };

  root.innerHTML = `
    <div class="banner">
      <div class="eyebrow">Counter Corruption Unit</div>
      <h1>People Intelligence Risk Matrix</h1>
      <p>Calculated from CCU cases, associations, business interests and Hub Taskings held on each subject.</p>
    </div>
    ${renderListToolbar(key, toolbarOpts)}
    <div id="list-body"></div>
  `;

  const redraw = () => {
    const ctrl = listControl(key, toolbarOpts.sorts[0].value);
    let filtered = scored.filter((p) => matchesQuery(ctrl.q, [p.subject_code]));
    if (ctrl.status) filtered = filtered.filter((p) => p.band === ctrl.status);
    const sorters = {
      score_desc: compareBy((p) => p.score, "desc"),
      score_asc: compareBy((p) => p.score, "asc"),
      subject: compareBy((p) => (p.subject_code || "").toLowerCase(), "asc"),
    };
    filtered = filtered.slice().sort(sorters[ctrl.sort] || sorters.score_desc);

    const body = document.getElementById("list-body");
    body.innerHTML = `
      <div class="card-title" style="margin-bottom:10px;">Subjects (${filtered.length} of ${scored.length})</div>
      ${filtered.length === 0 ? `<div class="empty-state">No subjects match this filter.</div>` : filtered.map((p) => `
        <div class="card">
          <div class="case-row">
            <div>
              <div class="case-id">${escapeHtml(p.subject_code)} <span class="badge ${riskBadgeClass(p.band)}">${escapeHtml(p.band)}</span></div>
              <div class="case-meta">PIRM score ${p.score} &middot; ${p.factors.length} contributing record(s)</div>
            </div>
            <button class="btn btn-primary btn-sm" data-open-subject="${p.subject_id}">Open</button>
          </div>
        </div>
      `).join("")}
    `;
    body.querySelectorAll("[data-open-subject]").forEach((btn) => {
      btn.onclick = () => setView("subject", { caseId: Number(btn.dataset.openSubject) });
    });
  };

  wireListToolbar(key, toolbarOpts, redraw);
  redraw();
}

async function renderSubjectView(subjectId) {
  root.innerHTML = `<div class="loading">Loading&hellip;</div>`;
  let s;
  try { s = await api(`/subjects/${subjectId}`); } catch (e) {
    root.innerHTML = `<div class="error-banner">${escapeHtml(e.message)}</div>`; return;
  }
  const t = state.taxonomy;

  root.innerHTML = `
    <div class="breadcrumb"><button id="back-to-pirm">&larr; PIRM</button> / ${escapeHtml(s.subject.code)}</div>
    <div class="card">
      <div class="card-title">PIRM <span class="badge ${riskBadgeClass(s.pirm.band)}">${escapeHtml(s.pirm.band)} \u2014 score ${s.pirm.score}</span></div>
      ${s.pirm.factors.map((f) => `
        <div class="field-row"><span class="label">${escapeHtml(f.label)}${f.detail ? ` (${escapeHtml(f.detail)})` : ""}</span><span class="value">+${f.points}</span></div>
      `).join("") || `<div class="empty-state" style="padding:16px;">No contributing records.</div>`}
    </div>
    <div class="card">
      <div class="card-title">CCU Cases</div>
      ${s.cases.length === 0 ? `<div class="empty-state" style="padding:16px;">None</div>` : s.cases.map((c) => `
        <div class="field-row"><span class="label"><a href="#" data-open-case-from-subject="${c.id}" style="color:var(--blue);">${escapeHtml(c.ref)}</a></span><span class="value">${escapeHtml(t.stages[c.stage_index])}</span></div>
      `).join("")}
    </div>
    <div class="card">
      <div class="card-title">Hub Taskings</div>
      ${s.hub_taskings.length === 0 ? `<div class="empty-state" style="padding:16px;">None</div>` : s.hub_taskings.map((h) => `
        <div class="field-row"><span class="label"><a href="#" data-open-hub-from-subject="${h.id}" style="color:var(--blue);">${escapeHtml(h.ref)}</a></span><span class="value">${escapeHtml(t.hub_stages[h.stage_index])}</span></div>
      `).join("")}
    </div>
    <div class="card">
      <div class="card-title">NIAs</div>
      ${s.nia.length === 0 ? `<div class="empty-state" style="padding:16px;">None</div>` : s.nia.map((n) => `
        <div class="field-row"><span class="label">${escapeHtml(n.ref)}</span><span class="value">${escapeHtml(n.status)}</span></div>
      `).join("")}
    </div>
    <div class="card">
      <div class="card-title">Business Interests</div>
      ${s.business_interests.length === 0 ? `<div class="empty-state" style="padding:16px;">None</div>` : s.business_interests.map((b) => `
        <div class="field-row"><span class="label">${escapeHtml(b.ref)}</span><span class="value">${escapeHtml(b.status)}</span></div>
      `).join("")}
    </div>
  `;
  document.getElementById("back-to-pirm").onclick = () => setView("pirm");

  root.querySelectorAll("[data-open-case-from-subject]").forEach((link) => {
    link.onclick = (e) => {
      e.preventDefault();
      setView("case-detail", { caseId: Number(link.dataset.openCaseFromSubject) });
    };
  });
  root.querySelectorAll("[data-open-hub-from-subject]").forEach((link) => {
    link.onclick = (e) => {
      e.preventDefault();
      setView("hub-detail", { caseId: Number(link.dataset.openHubFromSubject) });
    };
  });
}

/* ---------------------------------------------------------------------
 * Go
 * ------------------------------------------------------------------- */
init();
