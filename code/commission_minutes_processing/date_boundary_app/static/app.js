/* Meeting-date boundary marker.
   You mark WHERE each meeting starts and WHICH date it is; everything after a mark
   belongs to that meeting until the next one. Nothing is pre-marked and the pipeline's
   own answer is never shown here — that comparison lives in "Score vs pipeline". */

const $ = (s) => document.querySelector(s);
let DOCS = [], CUR = null, LINES = [], MARKS = [], DATE_MENU = [], cursor = 0, dateIdx = [];
let anchor = null;   // shift-click range start

// ── document queue ────────────────────────────────────────────────────────────
async function loadDocs(keepSrc) {
  const q = new URLSearchParams();
  if ($('#fSample').checked) q.set('sample', '1');
  if ($('#fYear').value) q.set('year', $('#fYear').value);
  if ($('#fStatus').value) q.set('status', $('#fStatus').value);
  const r = await (await fetch('/api/docs?' + q)).json();
  DOCS = r.docs;

  if ($('#fYear').options.length <= 1) {
    for (const y of r.years) $('#fYear').add(new Option(y, y));
  }
  const done = r.stats.done || 0, todo = r.stats.todo || 0;
  $('#progress').textContent = `${done} / ${done + todo} sample documents marked`;

  const ul = $('#docList');
  ul.innerHTML = '';
  for (const d of DOCS) {
    const li = document.createElement('li');
    li.dataset.src = d.source_file;
    const name = d.source_file.split('/').pop();
    li.innerHTML = `<span class="nm">${name}</span>` +
      `<span class="badge s-${d.status}">${d.status === 'done' ? d.n_marks + ' mk' : 'todo'}</span>` +
      (d.kind === 'pdf' ? '<span class="badge pdf">pdf</span>' : '') +
      `<br><span class="mo">${d.month}</span>`;
    li.onclick = () => openDoc(d.source_file);
    ul.appendChild(li);
  }
  if (keepSrc) highlight(keepSrc);
  else if (DOCS.length && !CUR) openDoc((DOCS.find(d => d.status === 'todo') || DOCS[0]).source_file);
}

function highlight(src) {
  document.querySelectorAll('#docList li').forEach(li =>
    li.classList.toggle('active', li.dataset.src === src));
}

// ── one document ──────────────────────────────────────────────────────────────
async function openDoc(src) {
  const r = await (await fetch('/api/doc?src=' + encodeURIComponent(src))).json();
  if (r.error) { alert(r.error); return; }
  CUR = src; LINES = r.lines; MARKS = r.boundaries || []; DATE_MENU = r.date_menu || [];
  cursor = 0;
  $('#docName').textContent = `${src}  ·  ${r.n_lines} lines  ·  ${r.kind}`;
  highlight(src);
  renderDoc();
  renderMarks();
  $('#doc').scrollTop = 0;
}

// A span counts rendered (non-blank) rows, so a mark starting at line_no covers the next
// span-1 rendered rows — not the next span-1 raw line numbers.
function spanLines(m) { return (m.span || 1); }

function rowsCovered(m) {
  const start = LINES.findIndex(l => l.n === m.line_no);
  if (start < 0) return [];
  return LINES.slice(start, start + spanLines(m)).map(l => l.n);
}

function renderDoc() {
  const box = $('#doc');
  box.innerHTML = '';
  dateIdx = [];
  LINES.forEach((ln, i) => {
    const div = document.createElement('div');
    div.className = 'line' + (ln.dates.length ? ' hasdate' : '');
    if (ln.dates.length) dateIdx.push(i);
    // a mark covers span lines, not just the one clicked
    const covering = MARKS.find(m => rowsCovered(m).includes(ln.n));
    if (covering) div.className += ' marked';
    if ((ln.span || 1) > 1) div.className += ' wrapped';
    div.dataset.i = i;
    div.innerHTML = `<span class="no">${ln.n}</span><span class="tx"></span>`;
    div.querySelector('.tx').textContent = ln.text;
    div.onclick = (e) => {
      if (e.shiftKey && anchor !== null) openPicker(Math.min(anchor, i), Math.abs(i - anchor) + 1);
      else { anchor = i; openPicker(i); }
    };
    box.appendChild(div);
  });
}

function renderMarks() {
  const box = $('#marks');
  box.innerHTML = '';
  MARKS.sort((a, b) => a.line_no - b.line_no);
  for (const m of MARKS) {
    const el = document.createElement('span');
    el.className = 'mark';
    el.innerHTML = `<b>${m.meeting_date}</b><span class="ln">line ${m.line_no}` +
      ((m.span || 1) > 1 ? ` +${(m.span || 1) - 1}` : '') + `</span>`;
    const go = document.createElement('button'); go.textContent = 'go';
    go.onclick = () => jumpToLine(m.line_no);
    const rm = document.createElement('button'); rm.textContent = '×';
    rm.onclick = () => { MARKS = MARKS.filter(x => x !== m); renderDoc(); renderMarks(); };
    el.append(go, rm);
    box.appendChild(el);
  }
  $('#markCount').textContent = MARKS.length
    ? `${MARKS.length} meeting${MARKS.length > 1 ? 's' : ''} marked`
    : 'no meetings marked yet';
}

// ── marking ───────────────────────────────────────────────────────────────────
async function openPicker(i, rows) {
  document.querySelectorAll('.picker').forEach(p => p.remove());
  setCursor(i);
  const ln = LINES[i];
  // how many rendered rows this mark covers: an explicit shift-click selection, else the
  // line's own wrapped-date span ("Thursday, March" + "10, 2011")
  const span = rows || ln.span || 1;
  const host = document.querySelector(`.line[data-i="${i + span - 1}"]`)
            || document.querySelector(`.line[data-i="${i}"]`);
  const p = document.createElement('div');
  p.className = 'picker';
  p.onclick = (e) => e.stopPropagation();

  document.querySelectorAll('.line.selected').forEach(e => e.classList.remove('selected'));
  for (let k = i; k < i + span; k++)
    document.querySelector(`.line[data-i="${k}"]`)?.classList.add('selected');

  // dates found in the selected run first — that is the header, wrapped or not
  let own = ln.dates;
  if (span > 1) {
    const texts = LINES.slice(i, i + span).map(l => l.text);
    try {
      const r = await (await fetch('/api/dates', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ texts })
      })).json();
      own = r.dates.length ? r.dates : own;
    } catch { /* fall back to the line's own dates */ }
  }
  const near = [...own];
  for (const d of DATE_MENU) if (!near.includes(d)) near.push(d);
  for (const d of near.slice(0, 10)) {
    const b = document.createElement('button');
    b.textContent = d;
    if (own.includes(d)) b.style.fontWeight = '700';
    b.onclick = () => addMark(ln.n, d, span);
    p.appendChild(b);
  }
  if (span > 1) {
    const note = document.createElement('span');
    note.className = 'spannote';
    note.textContent = `${span} lines selected`;
    p.appendChild(note);
  }
  const free = document.createElement('input');
  free.type = 'date'; free.title = 'other date';
  free.onchange = () => free.value && addMark(ln.n, free.value, span);
  const cancel = document.createElement('button');
  cancel.textContent = 'cancel'; cancel.onclick = () => p.remove();
  p.append(free, cancel);
  host.after(p);
  p.scrollIntoView({ block: 'nearest' });
}

function addMark(line_no, meeting_date, span = 1) {
  MARKS = MARKS.filter(m => m.line_no !== line_no);
  MARKS.push({ line_no, meeting_date, span });
  document.querySelectorAll('.picker').forEach(p => p.remove());
  renderDoc(); renderMarks();
}

function setCursor(i) {
  cursor = i;
  document.querySelectorAll('.line.cursor').forEach(e => e.classList.remove('cursor'));
  const el = document.querySelector(`.line[data-i="${i}"]`);
  if (el) { el.classList.add('cursor'); el.scrollIntoView({ block: 'center' }); }
}

function jumpToLine(line_no) {
  const i = LINES.findIndex(l => l.n === line_no);
  if (i >= 0) setCursor(i);
}

function stepDate(dir) {
  if (!dateIdx.length) return;
  const next = dir > 0
    ? dateIdx.find(i => i > cursor) ?? dateIdx[0]
    : [...dateIdx].reverse().find(i => i < cursor) ?? dateIdx[dateIdx.length - 1];
  setCursor(next);
}

async function save(advance) {
  if (!CUR) return;
  if (!MARKS.length && !confirm('Save with no meeting marked?')) return;
  await fetch('/api/doc/save', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ src: CUR, boundaries: MARKS, status: 'done' })
  });
  const i = DOCS.findIndex(d => d.source_file === CUR);
  const nxt = advance ? DOCS.slice(i + 1).find(d => d.status === 'todo') : null;
  await loadDocs(CUR);
  if (nxt) openDoc(nxt.source_file);
}

// ── wiring ────────────────────────────────────────────────────────────────────
$('#btnSave').onclick = () => save(true);
$('#btnNext').onclick = () => stepDate(1);
$('#btnPrev').onclick = () => stepDate(-1);
$('#fSample').onchange = () => loadDocs();
$('#fYear').onchange = () => loadDocs();
$('#fStatus').onchange = () => loadDocs();
$('#btnCloseScore').onclick = () => $('#scoreModal').classList.add('hidden');
$('#btnScore').onclick = async () => {
  $('#scoreModal').classList.remove('hidden');
  $('#scoreBody').textContent = 'scoring…';
  const r = await (await fetch('/api/score')).json();
  $('#scoreBody').textContent = JSON.stringify(r, null, 2);
};

document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT') return;
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); save(true); }
  else if (e.key === 'n') { e.preventDefault(); stepDate(1); }
  else if (e.key === 'N') { e.preventDefault(); stepDate(-1); }
  else if (e.key === 'm') { e.preventDefault(); anchor = cursor; openPicker(cursor); }
  else if (e.key === 'M') {   // extend: mark from the anchor through the cursor
    e.preventDefault();
    if (anchor !== null) openPicker(Math.min(anchor, cursor), Math.abs(cursor - anchor) + 1);
  }
});

loadDocs();
