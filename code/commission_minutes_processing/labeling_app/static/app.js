"use strict";
let SCHEMA = [], SECTIONS = [], FIELDS = [];
let items = [], curId = null, curBlock = "";

const $ = (s) => document.querySelector(s);
const api = (u, o) => fetch(u, o).then(r => r.json());

function toast(msg) {
  let t = $("#toast"); if (!t) { t = document.createElement("div"); t.id = "toast"; document.body.appendChild(t); }
  t.textContent = msg; t.classList.add("show"); clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove("show"), 1400);
}

async function boot() {
  const s = await api("/api/schema");
  SCHEMA = s.schema; SECTIONS = s.sections; FIELDS = s.fields;
  buildForm();
  const stats = await api("/api/stats");
  const yrSel = $("#fYear");
  Object.keys(stats.by_year).sort().forEach(y => {
    const o = document.createElement("option"); o.value = y; o.textContent = y; yrSel.appendChild(o);
  });
  await refreshList();
  await refreshProgress();
  wireEvents();
  // auto-open first todo
  const first = items.find(i => i.status === "todo") || items[0];
  if (first) loadItem(first.id);
}

function buildForm() {
  const f = $("#labelForm"); f.innerHTML = "";
  for (const sec of SECTIONS) {
    const h = document.createElement("div"); h.className = "section-h"; h.textContent = sec; f.appendChild(h);
    for (const fld of SCHEMA.filter(x => x.section === sec)) f.appendChild(fieldRow(fld));
  }
}

function fieldRow(fld) {
  const wrap = document.createElement("div"); wrap.className = "field"; wrap.dataset.name = fld.name;
  const lab = document.createElement("label");
  lab.innerHTML = `${fld.name}<br><span class="t">${fld.type}</span>`;
  wrap.appendChild(lab);
  let ctrl;
  if (fld.type === "enum") {
    ctrl = document.createElement("select");
    ctrl.appendChild(new Option("", ""));
    fld.choices.forEach(c => ctrl.appendChild(new Option(c, c)));
  } else if (fld.type === "text") {
    ctrl = document.createElement("textarea");
  } else {
    ctrl = document.createElement("input");
    ctrl.type = (fld.type === "int") ? "number" : "text";
    if (fld.type === "list") ctrl.placeholder = "comma, separated, names";
  }
  ctrl.id = "f_" + fld.name; ctrl.dataset.name = fld.name;
  ctrl.addEventListener("input", () => wrap.classList.add("changed"));
  wrap.appendChild(ctrl);
  // Any enum offering "other" gets a companion free-text box, shown when "other"
  // is selected, so you can type a custom value instead of the canned choices.
  if (fld.type === "enum" && fld.choices.includes("other")) {
    const ti = document.createElement("input");
    ti.type = "text"; ti.id = "f_" + fld.name + "__other";
    ti.className = "other-text"; ti.placeholder = "type a custom value…";
    ti.hidden = true; ti.style.marginTop = "4px";
    ti.addEventListener("input", () => wrap.classList.add("changed"));
    ctrl.addEventListener("change", () => { ti.hidden = ctrl.value !== "other"; });
    wrap.appendChild(ti);
  }
  return wrap;
}

async function refreshList() {
  const focus = $("#fFocus").value;
  const qs = new URLSearchParams({ status: $("#fStatus").value, year: $("#fYear").value,
    order: $("#fOrder").value, focus, q: $("#fSearch").value });
  items = await api("/api/items?" + qs);
  const ul = $("#itemList"); ul.innerHTML = "";
  for (const it of items) {
    const li = document.createElement("li"); li.dataset.id = it.id;
    if (it.id === curId) li.classList.add("active");
    const st = it.flagged ? "flagged" : it.status;
    const rare = (it.score > 0 && it.rare_class)
      ? `<span class="badge rare" title="fills scarce class: ${it.rare_class}">rare ${it.score|0}</span>` : "";
    // QA-flag badge: green "X only" when `focus` is the sole flag (quick fix),
    // else list the fields QA touched so you know what to check.
    let qa = "";
    const fields = it.qa_fields || [];
    if (focus && it.qa_only) {
      qa = `<span class="badge qa-only" title="${focus} is the only QA flag — just fix it and Save">${focus} only</span>`;
    } else if (focus) {
      const others = fields.filter(f => f !== focus).length;
      qa = `<span class="badge qa" title="QA flags: ${fields.join(", ")}">${focus} +${others}</span>`;
    } else if (fields.length) {
      qa = `<span class="badge qa" title="QA flags: ${fields.join(", ")}">QA: ${fields.length}</span>`;
    }
    li.innerHTML = `<span class="cn">${it.case_number || "(no case#)"}</span>
      <span class="badge s-${st}">${st}</span>${rare}${qa}<br><span class="yr">${it.year} · ${it.meeting_date || "?"}</span>`;
    li.onclick = () => loadItem(it.id);
    ul.appendChild(li);
  }
}

async function refreshProgress() {
  const s = await api("/api/stats");
  const d = s.by_status || {};
  const done = d.done || 0;
  $("#progress").textContent =
    `${done}/${s.total} done · todo ${d.todo || 0} · prelabeled ${d.prelabeled || 0} · flagged ${d.flagged || 0}`;
}

function highlight(text, caseNum) {
  const esc = text.replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  if (!caseNum) return esc;
  return esc.replace(new RegExp(caseNum.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g"), "<mark>$&</mark>");
}

async function loadItem(id) {
  const it = await api("/api/item/" + id);
  curId = id; curBlock = it.block_text;
  document.querySelectorAll("#itemList li").forEach(li =>
    li.classList.toggle("active", +li.dataset.id === id));
  $("#rawMeta").textContent = `${it.case_number || "(no case#)"} · ${it.year} · ${it.meeting_date || "?"} · ${it.source_file}`;
  $("#rawText").innerHTML = highlight(it.block_text, it.case_number);
  $("#formMeta").textContent = `item #${id}`;
  fillForm(it.label.data);
  $("#status").value = it.label.status === "prelabeled" ? "todo" : it.label.status;
  $("#flagged").checked = it.label.flagged;
  $("#notes").value = it.label.notes || "";
  // Surface a QA/review note as a prominent banner (strip the leading [review] tag).
  const note = it.label.notes || "";
  const isReview = it.label.status === "review" || /^\s*\[review\]/i.test(note);
  const banner = $("#reviewNote");
  if (isReview && note.trim()) {
    $("#reviewNoteText").textContent = note.replace(/^\s*\[review\]\s*/i, "");
    banner.hidden = false;
  } else {
    banner.hidden = true;
  }
}

function fillForm(data) {
  for (const fld of SCHEMA) {
    const el = $("#f_" + fld.name); if (!el) continue;
    let v = data[fld.name];
    if (Array.isArray(v)) v = v.join(", ");
    if (v === 0 && fld.type === "int") v = "";   // show empty for zero counts
    // enum-with-other: a value that isn't a canned choice is a typed custom string —
    // set the select to "other" and surface it in the companion text box.
    if (fld.type === "enum" && fld.choices.includes("other")) {
      const ti = $("#f_" + fld.name + "__other");
      const val = (v == null) ? "" : String(v);
      const known = val === "" || fld.choices.includes(val);
      el.value = known ? val : "other";
      if (ti) { ti.value = known ? "" : val; ti.hidden = el.value !== "other"; }
      el.closest(".field").classList.remove("changed");
      continue;
    }
    el.value = (v == null) ? "" : v;
    el.closest(".field").classList.remove("changed");
  }
}

function collectForm() {
  const data = {};
  for (const fld of SCHEMA) {
    const el = $("#f_" + fld.name); if (!el) continue;
    // enum-with-other + "other" selected → send the typed custom string (or "other")
    if (fld.type === "enum" && fld.choices.includes("other") && el.value === "other") {
      const ti = $("#f_" + fld.name + "__other");
      const custom = ti && ti.value.trim();
      data[fld.name] = custom ? ti.value.trim() : "other";
    } else {
      data[fld.name] = el.value;   // backend coerce_record() handles list/int/enum
    }
  }
  return data;
}

async function save(advance) {
  if (curId == null) return;
  const flagged = $("#flagged").checked;
  const status = flagged ? "flagged" : $("#status").value;
  await api("/api/item/" + curId, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ data: collectForm(), status, flagged, notes: $("#notes").value }),
  });
  toast("saved");
  await refreshProgress();
  const idx = items.findIndex(i => i.id === curId);
  if (idx >= 0) { items[idx].status = status; items[idx].flagged = flagged; }
  await refreshList();
  if (advance) {
    const next = items.find(i => i.id !== curId && i.status === "todo") ||
                 (idx + 1 < items.length ? items[idx + 1] : null);
    if (next) loadItem(next.id);
  }
}

async function prefill() {
  if (curId == null) return;
  const backend = $("#prefillBackend").value;
  toast("prefilling…");
  const r = await api(`/api/prefill/${curId}?backend=${backend}`, { method: "POST" });
  if (r.error) { toast(r.error); return; }
  fillForm(r.data);
  document.querySelectorAll(".field").forEach(f => f.classList.add("changed"));
  toast("prefilled (review!)");
}

function wireEvents() {
  $("#fStatus").onchange = refreshList;
  $("#fYear").onchange = refreshList;
  $("#fOrder").onchange = refreshList;
  $("#fFocus").onchange = refreshList;
  let t; $("#fSearch").oninput = () => { clearTimeout(t); t = setTimeout(refreshList, 250); };
  $("#btnSave").onclick = (e) => { e.preventDefault(); save(true); };
  $("#btnPrefill").onclick = prefill;
  $("#btnExport").onclick = async () => {
    if (!confirm("Write status=done labels to {year}_labeled.json (backs up existing)?")) return;
    const r = await api("/api/export", { method: "POST" });
    toast(`exported ${r.total} records across ${Object.keys(r.written).length} years`);
  };
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); save(true); }
    else if (e.key === "p" && e.target.tagName === "BODY") { e.preventDefault(); prefill(); }
    else if (e.altKey && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      e.preventDefault();
      const idx = items.findIndex(i => i.id === curId);
      const ni = e.key === "ArrowDown" ? idx + 1 : idx - 1;
      if (ni >= 0 && ni < items.length) loadItem(items[ni].id);
    }
  });
}

boot();
