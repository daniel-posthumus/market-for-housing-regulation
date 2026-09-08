"use strict";
let SCHEMA = [], SECTIONS = [], FIELDS = [];
let items = [], curId = null, curBlock = "", curCase = "", curItem = null;
// Queue mode: the sidebar lists review_queue ROWS (one field of one item) instead of items.
let queueRows = [], queueMode = false, curQueueRow = null, evidenceSpans = {};

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
  // the focus list is whatever the notes actually reference, not a hard-coded set
  const focusFields = await api("/api/focus_fields");
  const fSel = $("#fFocus");
  focusFields.forEach(f => fSel.appendChild(new Option(f, f)));
  const stats = await api("/api/stats");
  const yrSel = $("#fYear");
  Object.keys(stats.by_year).sort().forEach(y => {
    const o = document.createElement("option"); o.value = y; o.textContent = y; yrSel.appendChild(o);
  });
  wireEvents();
  // The v2 review queue is the work in front of us; open straight into it when it has
  // anything in it, and fall back to the ordinary item queue when it is clear.
  const q0 = await api("/api/stats");
  if (q0.queue_open) { await setQueueMode(true); await refreshProgress();
                       if (queueRows.length) loadItem(queueRows[0].item_id, queueRows[0]);
                       return; }
  await refreshList();
  await refreshProgress();
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
  if (fld.derived) wrap.classList.add("derived");
  if (fld.validation_only) wrap.classList.add("validation-only");
  if (fld.unmeasurable) wrap.classList.add("unmeasurable");
  const lab = document.createElement("label");
  const tag = fld.derived ? "derived" : fld.validation_only ? "validation only"
            : fld.unmeasurable ? "rare" : fld.type;
  lab.innerHTML = `${fld.name}<br><span class="t">${tag}</span>`;
  lab.title = fld.help || "";
  wrap.appendChild(lab);

  // Derived counts are DISPLAYED, never typed. If the number looks wrong the fix is the
  // speaker rows above it — a count that can disagree with the list it summarises is a
  // second source of truth, which is what schema v2 removed.
  if (fld.derived) {
    const out = document.createElement("output");
    out.id = "f_" + fld.name; out.dataset.name = fld.name; out.className = "derived-val";
    out.textContent = "0";
    wrap.appendChild(out);
    return wrap;
  }
  if (fld.type === "list_of_objects") {
    wrap.appendChild(speakerEditor(fld));
    return wrap;
  }

  let ctrl;
  if (fld.type === "enum") {
    ctrl = document.createElement("select");
    ctrl.appendChild(new Option("", ""));
    fld.choices.forEach(c => ctrl.appendChild(new Option(c, c)));
  } else if (fld.type === "text") {
    ctrl = document.createElement("textarea");
    ctrl.addEventListener("input", () => autosize(ctrl));
  } else {
    ctrl = document.createElement("input");
    ctrl.type = (fld.type === "int") ? "number" : "text";
    if (fld.type === "list") ctrl.placeholder = "comma, separated, names";
  }
  ctrl.id = "f_" + fld.name; ctrl.dataset.name = fld.name;
  ctrl.addEventListener("input", () => wrap.classList.add("changed"));
  wrap.appendChild(ctrl);

  // A motion and a resolution are different instruments and the gold set was wrong about it
  // on three items, so the choice gets its own emphasis rather than looking like any other
  // dropdown.
  if (fld.name === "action_instrument") wrap.classList.add("instrument");

  // `project_descr` is now a MECHANICAL target — the verbatim Request-for clause through the
  // first sentence — so filling it is one click, from the same rule the migration used.
  if (fld.name === "project_descr") {
    const b = document.createElement("button");
    b.type = "button"; b.className = "mini"; b.id = "btnDescr";
    b.textContent = "insert description from block";
    b.onclick = () => {
      const p = (curItem && curItem.descr_proposal) || {};
      if (!p.text) { toast("no recognised opener in this block — write it"); return; }
      ctrl.value = p.text; wrap.classList.add("changed");
      toast(p.rule === "request_for" ? "inserted the description, verbatim"
                                     : "inserted the description — no Request-for, check it");
    };
    wrap.appendChild(b);
  }

  // `project_address` is validation-only (§2.4): the parcel join keys on block+lot. The chip
  // is a warning, not a gate — it says the locational gloss is still attached.
  if (fld.name === "project_address") {
    const chip = document.createElement("button");
    chip.type = "button"; chip.className = "chip warn"; chip.id = "addrChip"; chip.hidden = true;
    chip.title = "the minutes' cross-street gloss is still attached — click to truncate";
    chip.onclick = () => {
      ctrl.value = (curItem && curItem.address_core) || ctrl.value;
      wrap.classList.add("changed"); checkAddress();
    };
    ctrl.addEventListener("input", checkAddress);
    wrap.appendChild(chip);
  }
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

// ── speakers: a row per speaker, name + stance ──────────────────────────────
// v1 stored a flat name list beside three counts, and 106 of the 232 gold records had
// counts that could not be reconciled with the names. One row per speaker makes the stance
// a property of the person, which is the only shape in which the two cannot disagree.
// The labels carry the marker the minutes actually print, because that — not the speaker's
// remarks — is what decides the stance.
const STANCES = [["", "— none marked"], ["support", "+ support"], ["oppose", "− oppose"],
                 ["neutral", "= neutral"]];
// Was the stance READ off a printed marker, or reasoned from what the speaker said? The two
// are different evidence, and keeping them apart is what lets "markers only" be a filter.
const BASES = [["", "—"], ["marker", "marker"], ["inferred", "inferred"]];

function speakerRow(sp) {
  const row = document.createElement("div"); row.className = "sp-row";
  const name = document.createElement("input");
  name.type = "text"; name.className = "sp-name"; name.value = (sp && sp.name) || "";
  name.placeholder = "(anonymous)";
  const sel = document.createElement("select"); sel.className = "sp-stance";
  STANCES.forEach(([v, l]) => sel.appendChild(new Option(l, v)));
  sel.value = (sp && sp.stance) || "";
  sel.classList.add("st-" + (sel.value || "none"));
  const bas = document.createElement("select"); bas.className = "sp-basis";
  BASES.forEach(([v, l]) => bas.appendChild(new Option(l, v)));
  bas.value = (sp && sp.stance_basis) || "";
  bas.title = "marker = read off (+)/(-)/(=) or a heading; inferred = reasoned from what they said";
  const del = document.createElement("button");
  del.type = "button"; del.className = "sp-del"; del.textContent = "×"; del.title = "remove";
  const touch = () => {
    sel.className = "sp-stance st-" + (sel.value || "none");
    document.querySelector('.field[data-name="speakers"]').classList.add("changed");
    paintCounts();
  };
  name.addEventListener("input", touch);
  sel.addEventListener("change", touch);
  bas.addEventListener("change", touch);
  del.onclick = () => { row.remove(); touch(); };
  row.append(name, sel, bas, del);
  return row;
}

function speakerEditor(fld) {
  const box = document.createElement("div"); box.className = "sp-box"; box.id = "f_" + fld.name;
  const rows = document.createElement("div"); rows.className = "sp-rows";
  box.appendChild(rows);
  const bar = document.createElement("div"); bar.className = "sp-bar";
  const add = document.createElement("button");
  add.type = "button"; add.className = "mini"; add.textContent = "+ speaker";
  add.onclick = () => { rows.appendChild(speakerRow(null)); paintCounts();
                        document.querySelector('.field[data-name="speakers"]').classList.add("changed"); };
  // The count-only case: "3 speakers in support" names nobody. The schema's rule is that
  // many entries with an empty name and the stated stance — so it is one action, not three.
  const anon = document.createElement("button");
  anon.type = "button"; anon.className = "mini"; anon.textContent = "anonymous ×N";
  anon.title = "the block gives a count and no names";
  const hint = document.createElement("span");
  hint.className = "sp-hint";
  hint.textContent = "(+)/+ support · (-)/- oppose · (=)/(+/-)/= neutral · keep (M)/(F) · "
    + "stance is relative to THE REQUEST (on a DR the requestor is support)";
  anon.onclick = () => {
    const n = parseInt(prompt("how many anonymous speakers?", "3") || "0", 10);
    if (!n || n < 1) return;
    const st = (prompt("stance — support / oppose / neutral (blank for unspecified)", "support")
                || "").trim().toLowerCase();
    for (let i = 0; i < n; i++)
      rows.appendChild(speakerRow({ name: "", stance: st, stance_basis: st ? "marker" : "" }));
    paintCounts();
    document.querySelector('.field[data-name="speakers"]').classList.add("changed");
  };
  bar.append(add, anon, hint);
  box.appendChild(bar);
  return box;
}

function readSpeakers() {
  return [...document.querySelectorAll("#f_speakers .sp-row")].map(r => ({
    name: r.querySelector(".sp-name").value.trim(),
    stance: r.querySelector(".sp-stance").value,
    stance_basis: r.querySelector(".sp-basis").value,
  })).filter(s => s.name || s.stance);
}

// The derived counts, recomputed from the rows on every edit. Same rule as
// extraction_common.derive_speaker_counts, so the display and the stored value agree.
function paintCounts() {
  const sp = readSpeakers();
  for (const st of ["support", "oppose", "neutral"]) {
    const el = $("#f_" + st + "_count");
    if (el) el.textContent = sp.filter(s => s.stance === st).length;
  }
}

// Grow a textarea to fit its content, up to the CSS cap. A 1,500-character description in
// a two-line box cannot be checked against the block, which is the whole job here.
function autosize(el) {
  if (!el) return;
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight + 2, window.innerHeight * 0.34) + "px";
}

function autosizeAll() {
  document.querySelectorAll("#labelForm textarea").forEach(autosize);
}

// Scroll a field into view WITHOUT moving anything else. `el.scrollIntoView()` walks up and
// scrolls every scrollable ancestor it finds — including #main, which is overflow:hidden but
// still scrollable from script — and that pushed the review-queue panel off the top of the
// window. The panel was rendering correctly and simply could not be seen.
function revealField(el) {
  if (!el) return;
  const pane = $("#formPane");
  if (!pane || !pane.contains(el)) return;
  const top = el.getBoundingClientRect().top - pane.getBoundingClientRect().top;
  pane.scrollTop += top - pane.clientHeight / 3;
}

function checkAddress() {
  const el = $("#f_project_address"), chip = $("#addrChip");
  if (!el || !chip) return;
  const core = (curItem && curItem.address_core) || "";
  const stale = core && el.value.trim() !== core && el.value.trim().length > core.length;
  chip.hidden = !stale;
  chip.textContent = stale ? "gloss attached → " + core.slice(0, 40) : "";
}

// Render ONE queue row. Split out of refreshList so that saving an item can repaint
// its own badge in place instead of re-fetching and re-sorting the whole queue —
// the rare-class scores shift with every label, so a refetch reshuffles the list and
// the row you just left is occupied by something else by the time you look back.
function paintRow(li, it, focus) {
    li.dataset.id = it.id;
    li.classList.toggle("active", it.id === curId);
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
    // only the unusual meeting types earn a badge; "regular" is the null hypothesis
    const mt = (it.meeting_type && it.meeting_type !== "regular")
      ? `<span class="badge mt" title="meeting type">${it.meeting_type.replace("_", " ")}</span>` : "";
    // The document format the item came from: the two eras lay an item out differently,
    // so knowing which one you are reading is part of reading it.
    const era = it.era === "pdf_2015_2026"
      ? `<span class="badge era pdf" title="2015-2026 PDF minutes">PDF</span>` : "";
    li.innerHTML = `<span class="cn">${it.case_number || "(no case#)"}</span>
      <span class="badge s-${st}">${st}</span>${rare}${qa}${mt}${era}<br><span class="yr">${weekdayOf(it.meeting_date || "")} ${it.meeting_date || "?"}</span>`;
    li.onclick = () => loadItem(it.id);
    return li;
}

// Refetch and rebuild the queue. Called on a filter change and at boot — NOT after a
// save, so the order you are working through stays put for the whole pass.
async function refreshList() {
  if (queueMode) return refreshQueue();
  const focus = $("#fFocus").value;
  const qs = new URLSearchParams({ status: $("#fStatus").value, year: $("#fYear").value,
    era: $("#fEra").value, order: $("#fOrder").value, focus, q: $("#fSearch").value });
  items = await api("/api/items?" + qs);
  const ul = $("#itemList"); ul.innerHTML = "";
  for (const it of items) ul.appendChild(paintRow(document.createElement("li"), it, focus));
}

async function refreshProgress() {
  const s = await api("/api/stats");
  const d = s.by_status || {};
  const done = d.done || 0;
  $("#progress").textContent =
    `${done}/${s.total} done · todo ${d.todo || 0} · prelabeled ${d.prelabeled || 0} · flagged ${d.flagged || 0}`;
  // The QA-review backlog gets its own counter: it is the finite pile with an end in
  // sight, unlike `todo`, so it is the number worth watching while working through it.
  const rev = d.review || 0;
  const b = $("#reviewLeft");
  b.textContent = rev ? `review (QA): ${rev} left` : "review (QA): clear ✓";
  b.classList.toggle("clear", rev === 0);
  // The v2 review queue is the finite pile that has to be worked before the schema change
  // is settled, so it gets the counter that matters most right now.
  const qb = $("#queueLeft");
  if (qb) {
    const q = s.queue_open || 0;
    qb.textContent = q ? `review queue: ${q} in ${s.queue_items}` : "review queue: clear ✓";
    qb.classList.toggle("clear", q === 0);
  }
}

// ── the meeting bar ─────────────────────────────────────────────────────────
// Everything here is read, not typed: `meeting_date` is assigned by the date stage and
// the rest by the meeting-level extraction. Showing it means you can tell at a glance
// which hearing you are in — above all whether the Commission was sitting jointly with
// another body, which changes who voted and how the item reads.
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function weekdayOf(iso) {
  // noon local, so a date-only string never slips a day across a timezone offset
  const d = new Date(iso + "T12:00:00");
  return isNaN(d) ? "" : WEEKDAYS[d.getDay()];
}

function nameCount(v) {
  return (v || "").split(";").map(x => x.trim()).filter(Boolean);
}

function renderMeetingBar(it) {
  const m = it.meeting, bar = $("#meetingBar");
  const iso = it.meeting_date || "";
  const wd = iso ? weekdayOf(iso) : "";
  $("#mbDate").textContent = iso ? `${wd} ${iso}` : "(no date)";
  bar.classList.toggle("no-record", !m);

  const type = (m && m.meeting_type) || "";
  const badge = $("#mbType");
  badge.textContent = m ? (type || "type unknown") : "no meeting record";
  badge.className = "mb-type t-" + (m ? (type || "unknown") : "unknown");
  badge.title = m
    ? `from ${m.source_file}${m.hand_verified ? " · hand-verified" : ""}`
    : "no raw document for this meeting in the corpus — 2018 mostly; date still assigned";

  const bits = [];
  if (m && m.joint_body) bits.push("with " + m.joint_body);
  if (m && m.meeting_time) bits.push(m.meeting_time);
  if (m && m.location) bits.push(m.location);
  if (m && m.presiding) bits.push(m.presiding + " presiding");
  $("#mbDetail").textContent = bits.join(" · ");

  const eraChip = $("#mbEra");
  if (eraChip) {
    const pdf = it.era === "pdf_2015_2026";
    eraChip.textContent = pdf ? "PDF era" : "HTML era";
    eraChip.className = "mb-era " + (pdf ? "pdf" : "html");
    eraChip.title = pdf ? "2015-2026 PDF minutes — a different item layout"
                        : "1998-2014 HTML minutes";
  }

  const roll = $("#mbRoll");
  if (m) {
    const pres = nameCount(m.present), abs = nameCount(m.absent);
    roll.textContent = `present ${pres.length}` + (abs.length ? ` · absent ${abs.length}` : "");
    roll.title = `present: ${pres.join(", ") || "—"}\nabsent: ${abs.join(", ") || "—"}` +
                 `\nstaff: ${nameCount(m.staff).join(", ") || "—"}`;
  } else {
    roll.textContent = ""; roll.title = "";
  }
}

// ── raw-text display: compact vs verbatim ───────────────────────────────────
// The 1998-2014 archive pages break a line at every HTML tag boundary and pad with
// blank lines: 54% of all stored block text is whitespace, and the worst single item
// runs to 2,051 lines. `compact` reflows that for READING ONLY. It never touches
// items.block_text — that string is the gold input the trainer sees and the string
// assign_meeting_dates.py matches back into its source page, so rewriting it would
// desync both. Toggle to verbatim (button, or `t`) to see exactly what is stored.
//
// Reflow joins the lines of a paragraph, but always breaks before a labelled field.
// Without that guard the modern PDF era — which has no blank lines to begin with —
// collapses to one wall of text with ACTION and AYES buried inside it.
const STRUCTURAL = new RegExp(
  "^(?:SPEAKERS?\\b|SPEAKER\\s*\\(s\\)|ACTION\\b|AYES\\b|NOES\\b|NAYES\\b|" +
  "ABSENT\\b|EXCUSED\\b|RECUSED\\b|MOTION\\b|RESOLUTION\\b|" +
  "Preliminary\\s+Recommendation\\b|\\(Proposed\\b|\\(Continued\\b|" +
  "\\d+[a-z]?\\.\\s)", "i");

let rawMode = localStorage.getItem("rawMode") || "compact";

function compactText(t) {
  const out = [];
  let cur = [];
  const flush = () => { if (cur.length) { out.push(cur.join(" ")); cur = []; } };
  for (let line of (t || "").replace(/\u00a0/g, " ").split("\n")) {
    line = line.trim();
    if (!line) { flush(); continue; }        // a blank line ends a paragraph
    if (STRUCTURAL.test(line)) flush();      // ...and so does a labelled field
    cur.push(line);
  }
  flush();
  // A label and its value land in different paragraphs in the HTML era ("AYES :" then
  // the names, with a blank between). Rejoin them so a vote reads the same way it does
  // in the modern era — but never onto a line that is itself a label, or "SPEAKER(S):"
  // would swallow the ACTION line that follows it.
  const merged = [];
  for (const para of out) {
    const prev = merged[merged.length - 1];
    if (prev && /:\s*$/.test(prev) && !STRUCTURAL.test(para)) merged[merged.length - 1] = prev + " " + para;
    else merged.push(para);
  }
  return merged.join("\n");
}

function renderRaw() {
  const text = rawMode === "verbatim" ? curBlock : compactText(curBlock);
  $("#rawText").innerHTML = highlight(text, curCase);
  const b = $("#btnRaw");
  if (b) {
    b.textContent = rawMode === "verbatim" ? "verbatim (t)" : "compact (t)";
    b.title = rawMode === "verbatim"
      ? "showing the stored text exactly — click for the reflowed view"
      : "whitespace reflowed for reading; the stored text is unchanged — click to see it verbatim";
  }
}

function toggleRaw() {
  rawMode = rawMode === "compact" ? "verbatim" : "compact";
  localStorage.setItem("rawMode", rawMode);
  renderRaw();
}

// ── the unified review queue ───────────────────────────────────────────────
// One queue, four typed reasons (§7.2). The sidebar lists ROWS, not items, so the same
// field lands consecutively and 232 `project_descr` values are one pass rather than 232
// context switches.
const REASON_LABEL = {
  field_redefined: "redefined",
  migration_ambiguous: "migrate",
  adjudication: "gold vs model",
  new_item: "new",
};

function showVal(v) {
  if (v == null || v === "") return "(blank)";
  if (Array.isArray(v)) {
    if (!v.length) return "(none)";
    return v.map(x => (x && typeof x === "object")
      ? `${x.name || "(anon)"}${x.stance ? " [" + x.stance
          + (x.stance_basis ? "/" + x.stance_basis : "") + "]" : ""}` : String(x)).join(", ");
  }
  // a {field: value} map — one decision that sets more than one field
  if (typeof v === "object")
    return Object.entries(v).map(([k, x]) => `${k}=${x === "" ? "(blank)" : x}`).join(" · ");
  return String(v);
}

// Collapse is a per-item convenience, not a persistent setting: a panel that stays hidden as
// you move through the queue is indistinguishable from a panel that is broken.
function toggleQueuePanel() {
  const p = $("#queuePanel");
  const on = p.classList.toggle("collapsed");
  $("#btnQueueCollapse").textContent = on ? "expand" : "collapse";
}


async function refreshQueue() {
  const qs = new URLSearchParams({ status: "open" });
  const reason = $("#fReason").value, field = $("#fQField").value;
  if (reason) qs.set("reason", reason);
  if (field) qs.set("field", field);
  queueRows = await api("/api/queue?" + qs);
  const ul = $("#itemList"); ul.innerHTML = "";
  for (const r of queueRows) {
    const li = document.createElement("li");
    li.dataset.qid = r.id;
    li.classList.toggle("active", curQueueRow && curQueueRow.id === r.id);
    li.innerHTML = `<span class="cn">${r.case_number || "(no case#)"}</span>
      <span class="badge q-${r.reason}">${REASON_LABEL[r.reason] || r.reason}</span>
      ${r.field ? `<span class="badge fld">${r.field}</span>` : ""}
      <br><span class="yr">${r.year} · ${r.era === "pdf_2015_2026" ? "PDF" : "HTML"}</span>`;
    li.onclick = () => loadItem(r.item_id, r);
    ul.appendChild(li);
  }
  // populate the field filter from what is actually queued
  const sel = $("#fQField");
  if (!sel.dataset.filled) {
    const all = await api("/api/queue?status=open&limit=3000");
    const fields = [...new Set(all.map(r => r.field).filter(Boolean))];
    fields.forEach(f => sel.appendChild(new Option(f, f)));
    sel.dataset.filled = "1";
  }
}

function queueAdvance() {
  if (!queueMode || !curQueueRow) return false;
  const i = queueRows.findIndex(r => r.id === curQueueRow.id);
  queueRows = queueRows.filter(r => r.id !== curQueueRow.id);
  const ul = $("#itemList");
  const li = ul.querySelector(`li[data-qid="${curQueueRow.id}"]`);
  if (li) li.remove();
  const next = queueRows[Math.min(i, queueRows.length - 1)];
  if (next) loadItem(next.item_id, next);
  else toast("queue empty for this filter");
  return true;
}

async function resolveQueue(row, action, extra) {
  const r = await api("/api/queue/" + row.id, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(Object.assign({ action }, extra || {})),
  });
  if (r.error) { toast(r.error); return; }
  toast(action === "skip" ? "skipped" : "resolved");
  await refreshProgress();
  if (curQueueRow && curQueueRow.id === row.id) queueAdvance();
  else loadItem(curId, curQueueRow);   // re-read: the label changed under the open form
}

// Every open queue row for the item on screen, so a field is never resolved blind — the
// block is right there and the other pending questions on the same item are visible.
async function renderQueuePanel(id) {
  const panel = $("#queuePanel"), body = $("#queueBody");
  const rows = await api("/api/queue?item_id=" + id + "&status=open");
  if (!rows.length) { panel.hidden = true; return; }
  panel.hidden = false; body.innerHTML = "";
  panel.classList.remove("collapsed");
  const cb = $("#btnQueueCollapse");
  if (cb) cb.textContent = "collapse";
  for (const r of rows) {
    const card = document.createElement("div");
    card.className = "q-card q-" + r.reason + (curQueueRow && curQueueRow.id === r.id ? " focus" : "");
    const head = document.createElement("div"); head.className = "q-head";
    head.innerHTML = `<span class="badge q-${r.reason}">${REASON_LABEL[r.reason] || r.reason}</span>
      <b>${r.field || "whole item"}</b> <span class="q-detail">${r.detail || ""}</span>`;
    card.appendChild(head);

    if (r.reason === "adjudication") {
      const cmp = document.createElement("div"); cmp.className = "q-cmp";
      cmp.innerHTML = `<div class="g"><b>gold</b> ${showVal(r.old_value)}</div>
                       <div class="p"><b>model</b> ${showVal(r.proposed)}</div>`;
      card.appendChild(cmp);
      const btns = document.createElement("div"); btns.className = "q-btns";
      for (const [v, label] of [["gold", "gold ✓"], ["model", "model ✓ (adopt)"],
                                ["both", "both wrong"]]) {
        const b = document.createElement("button"); b.type = "button"; b.textContent = label;
        b.onclick = () => {
          if (v === "both") {
            // Neither value is right, so the corrected one has to be typed — it is taken
            // from the form field, which is where the labeller fixes it. If the field still
            // holds the gold value, nothing was corrected: saying "both wrong" and then
            // storing the gold value back would record a verdict that contradicts the value,
            // so say so instead of doing it silently.
            const el = $("#f_" + r.field);
            const val = el ? (r.field === "speakers" ? readSpeakers() : el.value) : "";
            if (JSON.stringify(val) === JSON.stringify(r.old_value) ||
                showVal(val) === showVal(r.old_value)) {
              toast("edit the field to what it SHOULD be first — \u2018both wrong\u2019 saves the form value");
              if (el) revealField(el);
              return;
            }
            resolveQueue(r, "accept", { verdict: "both", value: val });
          } else {
            resolveQueue(r, "accept", { verdict: v });
          }
        };
        btns.appendChild(b);
      }
      card.appendChild(btns);
    } else if (r.reason === "new_item") {
      const btns = document.createElement("div"); btns.className = "q-btns";
      const b = document.createElement("button");
      b.type = "button"; b.textContent = "mark labelled";
      b.title = "save the form first (⌘/Ctrl+Enter), then clear this from the queue";
      b.onclick = () => resolveQueue(r, "accept", { value: null });
      btns.appendChild(b);
      card.appendChild(btns);
    } else {
      const cmp = document.createElement("div"); cmp.className = "q-cmp";
      cmp.innerHTML = `<div class="g"><b>v1</b> ${showVal(r.old_value)}</div>
                       <div class="p"><b>proposed</b> ${showVal(r.proposed)}</div>`;
      card.appendChild(cmp);
      const btns = document.createElement("div"); btns.className = "q-btns";
      const acc = document.createElement("button");
      acc.type = "button"; acc.className = "primary"; acc.textContent = "accept (a)";
      acc.onclick = () => resolveQueue(r, "accept");
      const use = document.createElement("button");
      use.type = "button"; use.textContent = "use the form value";
      use.title = "resolve with whatever is in the form field right now";
      use.onclick = () => {
        const el = $("#f_" + r.field);
        const val = el ? (r.field === "speakers" ? readSpeakers() : el.value) : "";
        resolveQueue(r, "accept", { value: val });
      };
      const put = document.createElement("button");
      put.type = "button"; put.textContent = "→ form";
      put.title = "drop the proposal into the form field to edit it";
      put.onclick = () => {
        const el = $("#f_" + r.field);
        if (!el) return;
        if (r.field === "speakers") {
          const rows = el.querySelector(".sp-rows"); rows.innerHTML = "";
          (r.proposed || []).forEach(sp => rows.appendChild(speakerRow(sp)));
          paintCounts();
        } else if (r.proposed && typeof r.proposed === "object") {
          for (const [k, x] of Object.entries(r.proposed)) {
            const e2 = $("#f_" + k);
            if (e2) { e2.value = x; e2.closest(".field").classList.add("changed"); }
          }
        } else { el.value = (r.proposed == null) ? "" : r.proposed; }
        el.closest(".field").classList.add("changed");
        revealField(el);
      };
      const skip = document.createElement("button");
      skip.type = "button"; skip.textContent = "skip";
      skip.onclick = () => resolveQueue(r, "skip");
      btns.append(acc, use, put, skip);
      card.appendChild(btns);
    }
    body.appendChild(card);
    // focus the field this row is about, so the eye lands in the right place
    if (curQueueRow && curQueueRow.id === r.id && r.field) {
      const f = document.querySelector(`.field[data-name="${r.field}"]`);
      if (f) { f.classList.add("q-focus"); setTimeout(() => revealField(f), 0); }
    }
  }
  document.querySelectorAll(".field.q-focus").forEach(f => {
    if (!curQueueRow || f.dataset.name !== curQueueRow.field) f.classList.remove("q-focus");
  });
}


// The gold-vs-model adjudication view was removed from the interface on 2026-09-07: the
// question it answers (was the model wrong, or was the label?) matters for scoring the
// extractor, not for finishing the labels, and it was competing for attention with the work
// that has to happen first. The 206 rows are `deferred` in review_queue, the 70 verdicts
// already given are kept, and `build_adjudication.py --mirror` brings them back.

const rxEsc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

function highlight(text, caseNum) {
  let esc = text.replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  if (caseNum) esc = esc.replace(new RegExp(rxEsc(caseNum), "g"), "<mark>$&</mark>");
  // The model's evidence span for the field under review, marked in the block. This is what
  // makes an adjudication judgeable at a glance instead of by re-reading the whole item.
  const want = curQueueRow && curQueueRow.field;
  const span = (curQueueRow && curQueueRow.evidence) || (want ? evidenceSpans[want] : "");
  if (span && span.length > 3) {
    // whitespace in the block is ragged; match on a flexible-space pattern
    const pat = span.trim().split(/\s+/).map(rxEsc).join("\\s+");
    try {
      esc = esc.replace(new RegExp(pat, "i"), '<mark class="ev">$&</mark>');
    } catch (e) { /* an unparseable span is a missing highlight, not an error */ }
  }
  return esc;
}

async function loadItem(id, queueRow) {
  const it = await api("/api/item/" + id);
  curId = id; curBlock = it.block_text; curCase = it.case_number; curItem = it;
  curQueueRow = queueRow || null;
  evidenceSpans = it.evidence || {};
  document.querySelectorAll("#itemList li").forEach(li =>
    li.classList.toggle("active", +li.dataset.id === id));
  renderMeetingBar(it);
  renderQueuePanel(id);
  $("#rawMeta").textContent = `${it.case_number || "(no case#)"} · ${it.source_file}`;
  renderRaw();
  $("#formMeta").textContent = `item #${id}`;
  fillForm(it.label.data);
  autosizeAll();
  paintCounts();
  checkAddress();
  const db = $("#btnDescr");
  if (db) {
    const r = (it.descr_proposal || {}).rule;
    db.className = "mini rule-" + (r || "none");
    db.title = r === "request_for"
        ? "opens with \"Request for...\" — the description verbatim, to the closing block"
        : r === "opener"
        ? "no \"Request for...\"; runs from the opening phrase to the closing block — read it"
        : "no recognised opening phrase in this block — you will have to write it";
  }
  $("#status").value = it.label.status === "prelabeled" ? "todo" : it.label.status;
  $("#flagged").checked = it.label.flagged;
  $("#notes").value = it.label.notes || "";
  // Surface a QA/review note as a prominent banner (strip the leading [review] tag).
  const note = it.label.notes || "";
  const isReview = it.label.status === "review" || /^\s*\[review\]/i.test(note);
  const banner = $("#reviewNote");
  if (isReview && note.trim()) {
    // strip the machine-readable tags; the prose is what a human needs to read
    $("#reviewNoteText").textContent = note
      .replace(/^\s*\[review\]\s*/i, "").replace(/\[CHECK:[^\]]*\]/i, "").trim();
    banner.hidden = false;
  } else {
    banner.hidden = true;
  }
}

function fillForm(data) {
  for (const fld of SCHEMA) {
    const el = $("#f_" + fld.name); if (!el) continue;
    if (fld.type === "list_of_objects") {
      const rows = el.querySelector(".sp-rows"); rows.innerHTML = "";
      (data[fld.name] || []).forEach(sp => rows.appendChild(speakerRow(sp)));
      el.closest(".field").classList.remove("changed");
      continue;
    }
    if (fld.derived) continue;              // painted from the speaker rows
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
    if (fld.derived) continue;              // the server derives these from `speakers`
    const el = $("#f_" + fld.name); if (!el) continue;
    if (fld.type === "list_of_objects") { data[fld.name] = readSpeakers(); continue; }
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
  if (idx >= 0) {                      // repaint just this row, leaving the order alone
    items[idx].status = status; items[idx].flagged = flagged;
    const li = document.querySelector(`#itemList li[data-id="${curId}"]`);
    if (li) paintRow(li, items[idx], $("#fFocus").value);
  }
  if (advance) {
    if (queueMode) {
      const r = curQueueRow;
      // A `new_item` row is whole-item work: saving it as done IS the resolution, so it
      // clears itself rather than asking for a second click on 60 items.
      if (r && r.reason === "new_item" && status === "done")
        return resolveQueue(r, "accept", { value: null });
      // An adjudication is a VERDICT, not a value — saving the form does not answer it, so
      // the row stays open and the three buttons still want a click.
      if (r && r.reason === "adjudication") {
        toast("saved — the verdict buttons still need an answer");
        renderQueuePanel(curId); return;
      }
      // Otherwise Cmd-Enter means "this is my answer": resolve the focused row with what is
      // in the form and move on. Previously it only saved, leaving the row open and
      // inviting a `skip` that made finished work look abandoned.
      if (r && r.field) {
        const el = $("#f_" + r.field);
        const val = el ? (r.field === "speakers" ? readSpeakers() : el.value) : "";
        return resolveQueue(r, "accept", { value: val });
      }
      renderQueuePanel(curId); return;
    }
    // "next" means the next row down, always. Anything cleverer (skip to the next
    // `todo`) jumps around the queue once some of it is labelled.
    const next = idx >= 0 && idx + 1 < items.length ? items[idx + 1] : null;
    if (next) loadItem(next.id);
    else toast("end of the queue — change a filter for more");
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

async function setQueueMode(on) {
  queueMode = on;
  document.body.classList.toggle("queue-mode", on);
  $("#queueFilters").hidden = !on;
  $("#itemFilters").hidden = on;
  $("#btnQueueMode").classList.toggle("on", on);
  $("#btnQueueMode").textContent = on ? "queue mode ✓" : "queue mode";
  await refreshList();
}

function wireEvents() {
  $("#fStatus").onchange = refreshList;
  $("#fEra").onchange = refreshList;
  $("#fReason").onchange = refreshQueue;
  $("#fQField").onchange = refreshQueue;
  $("#btnQueueMode").onclick = () => setQueueMode(!queueMode);
  $("#btnQueueCollapse").onclick = toggleQueuePanel;
  $("#queueLeft").onclick = () => setQueueMode(true);
  $("#fYear").onchange = refreshList;
  $("#fOrder").onchange = refreshList;
  $("#fFocus").onchange = refreshList;
  let t; $("#fSearch").oninput = () => { clearTimeout(t); t = setTimeout(refreshList, 250); };
  $("#btnSave").onclick = (e) => { e.preventDefault(); save(true); };
  $("#btnPrefill").onclick = prefill;
  $("#btnRaw").onclick = toggleRaw;
  $("#reviewLeft").onclick = () => { $("#fStatus").value = "review"; refreshList(); };
  $("#btnExport").onclick = async () => {
    if (!confirm("Write status=done labels to {year}_labeled.json (backs up existing)?")) return;
    const r = await api("/api/export", { method: "POST" });
    toast(`exported ${r.total} records across ${Object.keys(r.written).length} years`);
  };
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); save(true); }
    else if (e.key === "p" && e.target.tagName === "BODY") { e.preventDefault(); prefill(); }
    else if (e.key === "t" && e.target.tagName === "BODY") { e.preventDefault(); toggleRaw(); }
    // `a` accepts the focused queue row's proposal — the keystroke that turns 232
    // project_descr rows into a pass rather than a project.
    else if (e.key === "a" && e.target.tagName === "BODY" && curQueueRow) {
      e.preventDefault();
      if (curQueueRow.reason !== "adjudication" && curQueueRow.reason !== "new_item")
        resolveQueue(curQueueRow, "accept");
    }
    else if (e.key === "c" && e.target.tagName === "BODY") { e.preventDefault(); toggleQueuePanel(); }
    else if (e.key === "s" && e.target.tagName === "BODY" && curQueueRow) {
      e.preventDefault(); resolveQueue(curQueueRow, "skip");
    }
    else if (e.altKey && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      e.preventDefault();
      const list = queueMode ? queueRows : items;
      const idx = queueMode ? list.findIndex(r => curQueueRow && r.id === curQueueRow.id)
                            : list.findIndex(i => i.id === curId);
      const ni = e.key === "ArrowDown" ? idx + 1 : idx - 1;
      if (ni >= 0 && ni < list.length)
        queueMode ? loadItem(list[ni].item_id, list[ni]) : loadItem(list[ni].id);
    }
  });
}

boot();
