/* Meeting-level labelling: confirm the attributes of the hearing itself — type, time,
   room, roll call, staff — from the header window around each marked boundary. */

const $ = (s) => document.querySelector(s);
let MTGS = [], SCHEMA = [], CUR = null, DATA = {};

async function loadList(keep) {
  const r = await (await fetch('/api/meetings')).json();
  MTGS = r.meetings; SCHEMA = r.schema;
  const done = r.stats.done || 0, todo = r.stats.todo || 0;
  $('#progress').textContent = `${done} / ${done + todo} meetings confirmed`;

  const st = $('#fStatus').value, ty = $('#fType').value;
  const shown = MTGS.filter(m => (!st || m.status === st) && (!ty || m.meeting_type === ty));
  const ul = $('#docList');
  ul.innerHTML = '';
  for (const m of shown) {
    const li = document.createElement('li');
    li.dataset.key = key(m);
    li.innerHTML =
      `<span class="nm">${m.meeting_date}</span>` +
      `<span class="badge s-${m.status}">${m.status}</span>` +
      (m.origin === 'detected' ? '<span class="badge pdf">detected</span>' : '') +
      `<br><span class="mo">${m.meeting_type || '—'} · ${m.source_file.split('/').pop()}</span>`;
    li.onclick = () => open(m);
    ul.appendChild(li);
  }
  if (keep) highlight(keep);
  else if (shown.length && !CUR) open(shown.find(m => m.status === 'todo') || shown[0]);
}

const key = (m) => `${m.source_file}#${m.line_no}`;

function highlight(k) {
  document.querySelectorAll('#docList li').forEach(li =>
    li.classList.toggle('active', li.dataset.key === k));
}

async function open(m) {
  const q = new URLSearchParams({ src: m.source_file, line: m.line_no });
  const r = await (await fetch('/api/meeting?' + q)).json();
  if (r.error) { alert(r.error); return; }
  CUR = r; DATA = r.data || {};
  $('#mtgName').textContent = `${r.meeting_date} · ${r.source_file} · line ${r.line_no}`;
  $('#originBadge').textContent = r.origin === 'detected'
    ? 'found by the detector, not hand-marked' : 'hand-marked';
  highlight(key(r));
  renderWindow(r);
  renderForm();
}

function renderWindow(r) {
  const box = $('#win');
  box.innerHTML = '';
  for (const line of (r.window_text || '').split('\n')) {
    const div = document.createElement('div');
    div.className = 'wline' + (line.trim() && line.trim() === (r.date_line || '').trim()
      ? ' mark' : '');
    div.textContent = line;
    box.appendChild(div);
  }
}

function autoGrow(el) {
  el.style.height = 'auto';
  el.style.height = Math.max(el.scrollHeight, 26) + 'px';
}

function renderForm() {
  const box = $('#form');
  box.innerHTML = '';
  for (const f of SCHEMA) {
    const wrap = document.createElement('div');
    wrap.className = 'fld';
    const lab = document.createElement('label');
    lab.textContent = f.name;
    lab.title = f.help || '';
    let input;
    if (f.type === 'enum') {
      input = document.createElement('select');
      input.add(new Option('', ''));
      for (const c of f.choices) input.add(new Option(c, c));
      input.value = DATA[f.name] || '';
    } else if (f.type === 'text') {
      input = document.createElement('textarea');
      input.rows = 2;
      input.value = DATA[f.name] || '';
    } else if (f.type === 'list') {
      // One entry per line, in a box that grows to fit. A roll call or a staff list is
      // half a dozen names; on a single comma-separated line you can only read it by
      // scrolling sideways, which makes checking it against the window tedious.
      input = document.createElement('textarea');
      input.className = 'listbox';
      input.value = (DATA[f.name] || []).join('\n');
    } else {
      input = document.createElement('input');
      input.type = 'text';
      input.value = DATA[f.name] || '';
    }
    input.oninput = () => {
      // accept commas too, so pasting from the document still works
      DATA[f.name] = f.type === 'list'
        ? input.value.split(/[\n,]/).map(s => s.trim()).filter(Boolean)
        : input.value;
      if (f.type === 'list') autoGrow(input);
    };
    input.onchange = input.oninput;
    wrap.append(lab, input);
    if (f.help) {
      const h = document.createElement('span');
      h.className = 'help'; h.textContent = f.help;
      wrap.appendChild(h);
    }
    box.appendChild(wrap);
  }
  // sized after insertion: scrollHeight is 0 while the element is still detached
  box.querySelectorAll('textarea.listbox').forEach(autoGrow);
}

async function save(advance) {
  if (!CUR) return;
  await fetch('/api/meeting/save', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ src: CUR.source_file, line: CUR.line_no, data: DATA, status: 'done' })
  });
  const i = MTGS.findIndex(m => key(m) === key(CUR));
  const nxt = advance ? MTGS.slice(i + 1).find(m => m.status === 'todo') : null;
  await loadList(key(CUR));
  if (nxt) open(nxt);
}

$('#btnSave').onclick = () => save(true);
$('#fStatus').onchange = () => loadList();
$('#fType').onchange = () => loadList();
$('#btnExport').onclick = async () => {
  const r = await (await fetch('/api/meetings/export', { method: 'POST' })).json();
  alert(`Wrote ${r.path}\n${r.confirmed} confirmed meetings.`);
};

document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); save(true); }
});

loadList();
