# mini_crm.py — Simple Bootstrap CRM (SQLite storage) + Mass Delete + Sources tags
# Features preserved:
# - Add Company with Type, Owner, URL, LinkedIn, Email, contact checkboxes, Notes, Status (incl. New)
# - Remember last selected Type & Owner (prefs)
# - Company detail: timestamped notes, change status/contacted flags
# - Kanban board: columns for New, Contacted, Followup Sent, Replied, Discovery; drag & drop + live search
# - List view: search, sort by created/updated, delete
# - CSV import: header or headerless; duplicate skipping; maps columns
# - Duplicate detection: on-blur in Add form and on submit (name/URL) with link to existing
# - Mass action delete on List page
# - Sources field (tags, multiple) persisted in SQLite

import os
import json
import uuid
import csv
import sqlite3
from datetime import datetime
from flask import Flask, request, redirect, url_for, render_template_string, jsonify, flash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, 'data')
DB_PATH   = os.path.join(DATA_DIR, 'crm.db')

STATUSES = ["New", "Contacted", "Followup Sent", "Replied", "Discovery"]
TYPES = ["marketing", "development", "merchant"]
OWNERS = ["Oskars", "Shawn"]

# ------------------------ SQLite Helpers ------------------------

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def ensure_storage():
    os.makedirs(DATA_DIR, exist_ok=True)
    with db() as con:
        cur = con.cursor()
        # companies table
        cur.execute("""
        CREATE TABLE IF NOT EXISTS companies (
          id TEXT PRIMARY KEY,
          type TEXT,
          owner TEXT,
          name TEXT,
          url TEXT,
          linkedin TEXT,
          email TEXT,
          contacted_email INTEGER DEFAULT 0,
          contacted_url INTEGER DEFAULT 0,
          contacted_linkedin INTEGER DEFAULT 0,
          status TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )""")
        # notes table
        cur.execute("""
        CREATE TABLE IF NOT EXISTS notes (
          id TEXT PRIMARY KEY,
          company_id TEXT NOT NULL,
          time TEXT NOT NULL,
          text TEXT NOT NULL,
          FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
        )""")
        # prefs table
        cur.execute("""
        CREATE TABLE IF NOT EXISTS prefs (
          key TEXT PRIMARY KEY,
          value TEXT
        )""")
        cur.execute("INSERT OR IGNORE INTO prefs(key,value) VALUES('last_sources', '[]')")
        # sources table (tags)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS sources (
          company_id TEXT NOT NULL,
          source TEXT NOT NULL,
          PRIMARY KEY (company_id, source),
          FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
        )""")
        # defaults for prefs if missing
        cur.execute("INSERT OR IGNORE INTO prefs(key,value) VALUES('last_type', ?)", (TYPES[0],))
        cur.execute("INSERT OR IGNORE INTO prefs(key,value) VALUES('last_owner', ?)", (OWNERS[0],))
        con.commit()

def row_to_company(row, notes_by_company=None, sources_by_company=None):
    c = dict(row)
    c['contacted_via'] = {
        'email': bool(c.pop('contacted_email', 0)),
        'url': bool(c.pop('contacted_url', 0)),
        'linkedin': bool(c.pop('contacted_linkedin', 0)),
    }
    if notes_by_company is not None:
        c['notes'] = notes_by_company.get(c['id'], [])
    else:
        c['notes'] = []
    if sources_by_company is not None:
        c['sources'] = sources_by_company.get(c['id'], [])
    else:
        c['sources'] = []
    return c

def load_prefs():
    ensure_storage()
    with db() as con:
        cur = con.execute("SELECT key, value FROM prefs")
        prefs = {r['key']: r['value'] for r in cur.fetchall()}
    prefs.setdefault('last_type', TYPES[0])
    prefs.setdefault('last_owner', OWNERS[0])
    # ensure key exists even if older DB
    if 'last_sources' not in prefs:
      prefs['last_sources'] = '[]'
    return prefs

def save_prefs(prefs):
    ensure_storage()
    with db() as con:
        for k, v in prefs.items():
            con.execute(
                "INSERT INTO prefs(key,value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, v)
            )
        con.commit()

def get_latest_sources(limit=10):
    """Return up to `limit` most recently used distinct sources (tags)."""
    ensure_storage()
    with db() as con:
        rows = con.execute(
            """
            SELECT s.source, MAX(COALESCE(c.updated_at, c.created_at)) AS last_used
            FROM sources s
            JOIN companies c ON c.id = s.company_id
            GROUP BY s.source
            ORDER BY last_used DESC
            LIMIT ?
            """,
            (limit,)
        ).fetchall()
    return [r["source"] for r in rows]

def load_data():
    """Return {'companies': [company dicts with notes and sources]} for existing UI code."""
    ensure_storage()
    with db() as con:
        companies = con.execute("""
            SELECT id, type, owner, name, url, linkedin, email,
                   contacted_email, contacted_url, contacted_linkedin,
                   status, created_at, updated_at
            FROM companies
        """).fetchall()
        ids = [r['id'] for r in companies] or ['']
        notes_by_company, sources_by_company = {}, {}
        if ids and ids != ['']:
            q_marks = ",".join("?" for _ in ids)
            note_rows = con.execute(f"""
                SELECT company_id, id, time, text
                FROM notes
                WHERE company_id IN ({q_marks})
            """, ids).fetchall()
            for n in note_rows:
                notes_by_company.setdefault(n['company_id'], []).append(
                    {'time': n['time'], 'text': n['text']}
                )
            src_rows = con.execute(f"""
                SELECT company_id, source
                FROM sources
                WHERE company_id IN ({q_marks})
            """, ids).fetchall()
            for s in src_rows:
                sources_by_company.setdefault(s['company_id'], []).append(s['source'])
        out = [row_to_company(r, notes_by_company, sources_by_company) for r in companies]
    return {"companies": out}

def get_company(cid):
    ensure_storage()
    with db() as con:
        row = con.execute("""
            SELECT id, type, owner, name, url, linkedin, email,
                   contacted_email, contacted_url, contacted_linkedin,
                   status, created_at, updated_at
            FROM companies WHERE id=?
        """, (cid,)).fetchone()
        if not row:
            return None
        notes = con.execute(
            "SELECT id, time, text FROM notes WHERE company_id=? ORDER BY time ASC", (cid,)
        ).fetchall()
        sources = con.execute(
            "SELECT source FROM sources WHERE company_id=? ORDER BY source COLLATE NOCASE", (cid,)
        ).fetchall()
        c = row_to_company(row)
        c['notes'] = [{'time': n['time'], 'text': n['text']} for n in notes]
        c['sources'] = [s['source'] for s in sources]
        return c

def norm_text(s):
    return (s or '').strip().lower()

def norm_url(u):
    u = (u or '').strip().lower()
    if u.startswith('http://'):
        u = u[7:]
    elif u.startswith('https://'):
        u = u[8:]
    if u.endswith('/'):
        u = u[:-1]
    return u

# ------------------------ Templates ------------------------

BASE_HTML = """
<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
    <title>{{ title or 'Mini CRM' }}</title>
    <link href=\"https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css\" rel=\"stylesheet\">
    <script src=\"https://cdn.jsdelivr.net/npm/sortablejs@1.15.3/Sortable.min.js\"></script>
    <style>
      body { background:#f8f9fa; }
      .kanban { display:grid; grid-template-columns:repeat(5, 1fr); gap:1rem; }
      .kanban-column { background:#fff; border-radius:.75rem; box-shadow:0 2px 12px rgba(0,0,0,.05); padding:.75rem; }
      .kanban-header { font-weight:700; font-size:1rem; margin-bottom:.5rem; display:flex; justify-content:space-between; align-items:center; }
      .card { cursor:pointer; }
      .note { background:#fff; border-radius:.5rem; padding:.75rem; border:1px solid #e9ecef; }
      .badge-type { text-transform:capitalize; }
      .chip { display:inline-flex; align-items:center; gap:.35rem; padding:.15rem .5rem; border-radius:999px; background:#e9ecef; font-size:.85rem; }
      .chip .x { cursor:pointer; font-weight:700; opacity:.6; }
    </style>
  </head>
  <body>
    <nav class=\"navbar navbar-expand-lg bg-body-tertiary mb-3\">
      <div class=\"container\">
        <a class=\"navbar-brand\" href=\"{{ url_for('board') }}\">Mini CRM</a>
        <div class=\"d-flex gap-2\">
          <a class=\"btn btn-primary\" href=\"{{ url_for('add_company') }}\">Add Company</a>
          <a class=\"btn btn-outline-secondary\" href=\"{{ url_for('board') }}\">Board</a>
          <a class=\"btn btn-outline-secondary\" href=\"{{ url_for('list_view') }}\">List</a>
          <a class=\"btn btn-outline-secondary\" href=\"{{ url_for('import_csv') }}\">Import</a>
        </div>
      </div>
    </nav>
    <div class=\"container\">
      {% with messages = get_flashed_messages() %}
        {% if messages %}
          <div class=\"alert alert-info\">{{ messages[0] }}</div>
        {% endif %}
      {% endwith %}
      {{ body|safe }}
    </div>
    <script src=\"https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js\"></script>
  </body>
</html>
"""

ADD_HTML = """
<div class=\"row justify-content-center\">
  <div class=\"col-lg-9\">
    <div class=\"card shadow-sm\"><div class=\"card-body\">
      <h1 class=\"h4 mb-3\">Add Company / Lead</h1>
      <form method=\"post\" action=\"{{ url_for('add_company') }}\">
        <div class=\"row g-3\">
          <div class=\"col-md-3\">
            <label class=\"form-label\">Type</label>
            <select class=\"form-select\" name=\"type\" required>
              {% for t in types %}<option value=\"{{t}}\" {% if t==defaults.last_type %}selected{% endif %}>{{t}}</option>{% endfor %}
            </select>
          </div>
          <!-- Sources (tags) field -->
          <div class=\"col-md-9\">
            <label class=\"form-label\">Sources</label>
            <div class=\"mb-2\" id=\"srcTags\"></div>
            <div class=\"mt-2\">
              <div class=\"small text-muted mb-1\">Recent tags:</div>{% if latest_sources %}{% for s in latest_sources %}<button class=\"btn btn-sm btn-outline-secondary me-1 mb-1 src-suggest\" type=\"button\" data-tag=\"{{ s }}\">{{ s }}</button>{% endfor %}{% else %}<div class=\"text-muted small\">No tags yet.</div>{% endif %}
            </div>
            <div class=\"input-group\">
              <input class=\"form-control\" id=\"srcInput\" placeholder=\"Type a source and press Enter\">
              <button class=\"btn btn-outline-secondary\" type=\"button\" id=\"srcAddBtn\">Add</button>
            </div>
            <input type=\"hidden\" name=\"sources\" id=\"srcHidden\">
            <div class=\"form-text\">Examples: Clutch, Google, MadeWith, Indeed (you can enter any tags)</div>
          </div>

          <div class=\"col-md-3\">
            <label class=\"form-label\">Lead Owner</label>
            <select class=\"form-select\" name=\"owner\" required>
              {% for o in owners %}<option value=\"{{o}}\" {% if o==defaults.last_owner %}selected{% endif %}>{{o}}</option>{% endfor %}
            </select>
          </div>
          <div class=\"col-md-9\">
            <label class=\"form-label\">Status</label>
            <select class=\"form-select\" name=\"status\" required>
              {% for s in statuses %}<option value=\"{{s}}\" {% if s=='New' %}selected{% endif %}>{{s}}</option>{% endfor %}
            </select>
          </div>

          <div class=\"col-md-6\">
            <label class=\"form-label\">Company Name (optional)</label>
            <input class=\"form-control\" type=\"text\" name=\"name\" placeholder=\"Acme Inc\">
          </div>
          <div class=\"col-md-6\">
            <label class=\"form-label\">Email (optional)</label>
            <div class=\"input-group\">
              <input class=\"form-control\" type=\"email\" name=\"email\" placeholder=\"hello@acme.com\">
              <div class=\"input-group-text\"><input class=\"form-check-input mt-0\" type=\"checkbox\" name=\"contacted_email\" value=\"1\"></div>
            </div>
            <div class=\"form-hint\">Tick if contacted via email.</div>
          </div>

          <div class=\"col-md-6\">
            <label class=\"form-label\">Website URL (optional)</label>
            <div class=\"input-group\">
              <input class=\"form-control\" type=\"url\" name=\"url\" placeholder=\"https://acme.com\">
              <div class=\"input-group-text\"><input class=\"form-check-input mt-0\" type=\"checkbox\" name=\"contacted_url\" value=\"1\"></div>
            </div>
            <div class=\"form-hint\">Tick if contacted via website form.</div>
          </div>

          <div class=\"col-md-6\">
            <label class=\"form-label\">LinkedIn (optional)</label>
            <div class=\"input-group\">
              <input class=\"form-control\" type=\"url\" name=\"linkedin\" placeholder=\"https://linkedin.com/company/acme\">
              <div class=\"input-group-text\"><input class=\"form-check-input mt-0\" type=\"checkbox\" name=\"contacted_linkedin\" value=\"1\"></div>
            </div>
            <div class=\"form-hint\">Tick if contacted via LinkedIn.</div>
          </div>

          <div class=\"col-12\">
            <label class=\"form-label\">Notes (optional)</label>
            <textarea class=\"form-control\" name=\"notes\" rows=\"3\" placeholder=\"Context, summary, next steps...\"></textarea>
          </div>
        </div>
        <div id=\"dupWarn\" class=\"alert alert-warning d-none mt-3\"></div>
        <div class=\"mt-3 d-flex gap-2\">
          <button class=\"btn btn-primary\" type=\"submit\">Save</button>
          <a class=\"btn btn-secondary\" href=\"{{ url_for('board') }}\">Cancel</a>
        </div>
      </form>
    </div></div>
  </div>
</div>
<script>
  const nameInput = document.querySelector('input[name="name"]');
  const urlInput  = document.querySelector('input[name="url"]');
  async function checkDup(){
    const name = nameInput ? nameInput.value.trim() : '';
    const url  = urlInput  ? urlInput.value.trim()  : '';
    if(!name && !url) return;
    const r = await fetch(`{{ url_for('api_check_duplicate') }}?name=${encodeURIComponent(name)}&url=${encodeURIComponent(url)}`);
    const j = await r.json();
    const box = document.getElementById('dupWarn');
    if(j.duplicate){
      box.classList.remove('d-none');
      box.innerHTML = `Possible duplicate: <a href='${j.link}'>open existing</a>`;
    } else {
      box.classList.add('d-none'); box.innerHTML = '';
    }
  }
  nameInput && nameInput.addEventListener('blur', checkDup);
  urlInput && urlInput.addEventListener('blur', checkDup);

  // Sources tag editor (Add)
  (function(){
    const tagsBox = document.getElementById('srcTags');
    const input = document.getElementById('srcInput');
    const addBtn = document.getElementById('srcAddBtn');
    const suggestBtns = () => Array.from(document.querySelectorAll('.src-suggest'));
    const hidden = document.getElementById('srcHidden');
    if(!tagsBox || !input || !hidden) return;
    const initial = {{ (defaults.pre_sources or []) | tojson }};
    let tags = Array.isArray(initial) ? initial.slice() : [];
    function render(){
      tagsBox.innerHTML = '';
      tags.forEach((t,i)=>{
        const el = document.createElement('span');
        el.className = 'chip me-2 mb-2';
        el.innerHTML = `<span>${t}</span><span class="x" data-i="${i}">&times;</span>`;
        tagsBox.appendChild(el);
      });
      hidden.value = JSON.stringify(tags);
    }
    function addTag(val){
      const v = (val||'').trim();
      if(!v) return;
      if(!tags.includes(v)) tags.push(v);
      input.value=''; render();
    }
    function wireSuggest(){
      suggestBtns().forEach(btn=>{
        btn.addEventListener('click', ()=> addTag(btn.getAttribute('data-tag')||''));
      });
    }
    tagsBox.addEventListener('click', e=>{
      const i = e.target.getAttribute('data-i');
      if(i!==null){ tags.splice(Number(i),1); render(); }
    });
    input.addEventListener('keydown', e=>{
      if(e.key==='Enter'){ e.preventDefault(); addTag(input.value); }
      if(e.key===',' ){ e.preventDefault(); addTag(input.value.replace(',','')); }
    });
    addBtn && addBtn.addEventListener('click', ()=> addTag(input.value));
    render(); wireSuggest();
  })();
</script>
"""

DETAIL_HTML = """
<div class=\"row\">
  <div class=\"col-lg-8\">
    <div class=\"card shadow-sm mb-3\"><div class=\"card-body\">
      <div class=\"d-flex justify-content-between align-items-start\">
        <div>
          <h1 class=\"h4 mb-1\">{{ company.get('name') or 'Unnamed Company' }}</h1>
          <div class=\"d-flex gap-2 align-items-center\">
            <span class=\"badge text-bg-secondary badge-type\">{{ company['type'] }}</span>
            <span class=\"badge text-bg-info\">{{ company['status'] }}</span>
            <span class=\"badge text-bg-warning\">Owner: {{ company.get('owner') or '-' }}</span>
          </div>
        </div>
        <a href=\"{{ url_for('board') }}\" class=\"btn btn-outline-secondary\">Back to Board</a>
      </div>
      <hr>
      <div class=\"row g-3\">
        {% if company.get('email') %}
        <div class=\"col-md-6\"><strong>Email:</strong> <a href=\"mailto:{{ company['email'] }}\">{{ company['email'] }}</a>{% if company['contacted_via'].get('email') %}<span class=\"badge text-bg-success ms-2\">contacted</span>{% endif %}</div>
        {% endif %}
        {% if company.get('url') %}
        <div class=\"col-md-6\"><strong>Website:</strong> <a href=\"{{ company['url'] }}\" target=\"_blank\">{{ company['url'] }}</a>{% if company['contacted_via'].get('url') %}<span class=\"badge text-bg-success ms-2\">contacted</span>{% endif %}</div>
        {% endif %}
        {% if company.get('linkedin') %}
        <div class=\"col-md-6\"><strong>LinkedIn:</strong> <a href=\"{{ company['linkedin'] }}\" target=\"_blank\">{{ company['linkedin'] }}</a>{% if company['contacted_via'].get('linkedin') %}<span class=\"badge text-bg-success ms-2\">contacted</span>{% endif %}</div>
        {% endif %}
      </div>
      <hr>
      <form method=\"post\" action=\"{{ url_for('update_company', cid=company['id']) }}\" class=\"row g-3\">
        <div class=\"col-md-4\">
          <label class=\"form-label\">Status</label>
          <select class=\"form-select\" name=\"status\">{% for s in statuses %}<option value=\"{{s}}\" {% if s==company['status'] %}selected{% endif %}>{{s}}</option>{% endfor %}</select>
        </div>
        <div class=\"col-md-4\">
          <label class=\"form-label\">Lead Owner</label>
          <select class=\"form-select\" name=\"owner\">{% for o in owners %}<option value=\"{{o}}\" {% if o==company.get('owner') %}selected{% endif %}>{{o}}</option>{% endfor %}</select>
        </div>
        <div class=\"col-12\">
          <label class=\"form-label\">Sources</label>
          <div class=\"mb-2\" id=\"srcTagsDetail\"></div>
          <div class=\"input-group\">
            <input class=\"form-control\" id=\"srcInputDetail\" placeholder=\"Type a source and press Enter\">
            <button class=\"btn btn-outline-secondary\" type=\"button\" id=\"srcAddBtnDetail\">Add</button>
          </div>
          <input type=\"hidden\" name=\"sources\" id=\"srcHiddenDetail\" value='{{ (company.get(\"sources\") or []) | tojson }}'>
          <div class=\"form-text\">Add/remove tags; they will be saved on Update.</div>
        </div>
        <div class=\"col-12\">
          <label class=\"form-label\">Contacted via</label>
          <div class=\"form-check form-check-inline\"><input class=\"form-check-input\" type=\"checkbox\" name=\"contacted_email\" value=\"1\" {% if company['contacted_via'].get('email') %}checked{% endif %}><label class=\"form-check-label\">Email</label></div>
          <div class=\"form-check form-check-inline\"><input class=\"form-check-input\" type=\"checkbox\" name=\"contacted_url\" value=\"1\" {% if company['contacted_via'].get('url') %}checked{% endif %}><label class=\"form-check-label\">Website form</label></div>
          <div class=\"form-check form-check-inline\"><input class=\"form-check-input\" type=\"checkbox\" name=\"contacted_linkedin\" value=\"1\" {% if company['contacted_via'].get('linkedin') %}checked{% endif %}><label class=\"form-check-label\">LinkedIn</label></div>
        </div>
        <div class=\"col-12 d-flex gap-2\"><button class=\"btn btn-primary\" type=\"submit\">Update</button><a class=\"btn btn-outline-secondary\" href=\"{{ url_for('board') }}\">Cancel</a></div>
      </form>
    </div></div>

    <div class=\"card shadow-sm\"><div class=\"card-body\">
      <h2 class=\"h5\">Notes</h2>
      <form method=\"post\" action=\"{{ url_for('add_note', cid=company['id']) }}\" class=\"mb-3\">
        <div class=\"input-group\"><textarea class=\"form-control\" name=\"note\" rows=\"2\" placeholder=\"Add a note...\"></textarea><button class=\"btn btn-primary\" type=\"submit\">Add</button></div>
      </form>
      {% if company['notes'] %}
        <div class=\"vstack gap-2\">{% for n in company['notes']|reverse %}<div class=\"note\"><div class=\"small text-muted\">{{ n['time'] }}</div><div>{{ n['text']|replace('\\n','<br>')|safe }}</div></div>{% endfor %}</div>
      {% else %}<div class=\"text-muted\">No notes yet.</div>{% endif %}
    </div></div>
  </div>
  <div class=\"col-lg-4\"><div class=\"card shadow-sm\"><div class=\"card-body\"><h2 class=\"h6\">Meta</h2><div class=\"small text-muted\">Created: {{ company['created_at'] }}<br>Updated: {{ company['updated_at'] }}</div></div></div></div>
</div>
<script>
  // Sources tag editor (Detail)
  (function(){
    const tagsBox = document.getElementById('srcTagsDetail');
    const input = document.getElementById('srcInputDetail');
    const addBtn = document.getElementById('srcAddBtnDetail');
    const hidden = document.getElementById('srcHiddenDetail');
    if(!tagsBox || !input || !hidden) return;
    let tags = [];
    try { tags = JSON.parse(hidden.value || '[]'); } catch(e){ tags = []; }
    function render(){
      tagsBox.innerHTML = '';
      tags.forEach((t,i)=>{
        const el = document.createElement('span');
        el.className = 'chip me-2 mb-2';
        el.innerHTML = `<span>${t}</span><span class="x" data-i="${i}">&times;</span>`;
        tagsBox.appendChild(el);
      });
      hidden.value = JSON.stringify(tags);
    }
    function addTag(val){
      const v = (val||'').trim();
      if(!v) return;
      if(!tags.includes(v)) tags.push(v);
      input.value=''; render();
    }
    tagsBox.addEventListener('click', e=>{
      const i = e.target.getAttribute('data-i');
      if(i!==null){ tags.splice(Number(i),1); render(); }
    });
    input.addEventListener('keydown', e=>{
      if(e.key==='Enter'){ e.preventDefault(); addTag(input.value); }
      if(e.key===',' ){ e.preventDefault(); addTag(input.value.replace(',','')); }
    });
    addBtn && addBtn.addEventListener('click', ()=> addTag(input.value));
    render();
  })();
</script>
"""

BOARD_HTML = """
<div class=\"d-flex justify-content-between align-items-center mb-2\">
  <h1 class=\"h4 mb-0\">Company Board</h1>
  <div class=\"d-flex align-items-center gap-2\">
    <input id=\"boardSearch\" class=\"form-control\" placeholder=\"Search name, email, url, owner...\" style=\"min-width:320px\">
    <button class=\"btn btn-outline-secondary\" onclick=\"document.getElementById('boardSearch').value=''; filterCards();\">Clear</button>
  </div>
</div>
<div class=\"kanban\" id=\"kanban\">
  {% for s in statuses %}
  <div class=\"kanban-column\">
    <div class=\"kanban-header\">{{ s }} <span class=\"badge text-bg-light\">{{ companies|selectattr('status','equalto',s)|list|length }}</span></div>
    <div class=\"kanban-list\" id=\"col-{{ loop.index0 }}\" data-status=\"{{ s }}\">
      {% for c in companies if c['status']==s %}
      <div class=\"card mb-2 crm-card\" data-id=\"{{ c['id'] }}\" data-name=\"{{ (c.get('name') or '')|lower }}\" data-email=\"{{ (c.get('email') or '')|lower }}\" data-url=\"{{ (c.get('url') or '')|lower }}\" data-owner=\"{{ (c.get('owner') or '')|lower }}\" onclick=\"openCompany(event, '{{ url_for('company_detail', cid=c['id']) }}')\">
        <div class=\"card-body py-2\">
          <div class=\"d-flex justify-content-between align-items-center\">
            <div class=\"fw-semibold\">{{ c.get('name') or 'Unnamed' }}</div>
            <span class=\"badge rounded-pill text-bg-secondary badge-type\">{{ c['type'] }}</span>
          </div>
          <div class=\"small text-muted\">{{ c.get('email') or c.get('url') or '' }}</div>
          <div class=\"small\"><span class=\"badge text-bg-warning\">{{ c.get('owner') or '-' }}</span></div>
        </div>
      </div>
      {% endfor %}
    </div>
  </div>
  {% endfor %}
</div>
<script>
  function openCompany(ev, href){ if(document.body.classList.contains('dragging')) return; window.location = href; }
  function filterCards(){
    const q = (document.getElementById('boardSearch').value || '').toLowerCase();
    document.querySelectorAll('.crm-card').forEach(card => {
      const hay = [card.dataset.name, card.dataset.email, card.dataset.url, card.dataset.owner].join(' ');
      card.style.display = hay.includes(q) ? '' : 'none';
    });
  }
  document.getElementById('boardSearch').addEventListener('input', filterCards);
  document.querySelectorAll('.kanban-list').forEach(function(list){
    new Sortable(list, {
      group: 'kanban', animation: 150,
      onStart: () => document.body.classList.add('dragging'),
      onEnd:   () => document.body.classList.remove('dragging'),
      onAdd: function (evt) {
        const card = evt.item; const id = card.getAttribute('data-id'); const newStatus = evt.to.getAttribute('data-status');
        fetch('{{ url_for('api_update_status') }}', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ id, status:newStatus })})
          .then(r => r.json()).then(j => { if(!j.ok){ alert('Failed to update'); evt.from.insertBefore(card, evt.from.children[evt.oldIndex]); } });
      }
    });
  });
</script>
"""

LIST_HTML = """
<div class='d-flex justify-content-between align-items-center mb-3'>
  <h1 class='h4 mb-0'>Companies</h1>
  <form class='d-flex gap-2' method='get'>
    <input class='form-control' type='search' name='q' value='{{ q }}' placeholder='Search name, email, url, owner...'>
    <select class='form-select' name='sort'>
      <option value='updated' {% if sort=='updated' %}selected{% endif %}>Modified (newest)</option>
      <option value='created' {% if sort=='created' %}selected{% endif %}>Added (newest)</option>
    </select>
    <button class='btn btn-outline-secondary' type='submit'>Apply</button>
  </form>
</div>

<form method="post" action="{{ url_for('mass_delete') }}" id="massForm">
<table class='table table-hover align-middle'>
  <thead>
    <tr>
      <th style="width:36px;"><input type="checkbox" id="chkAll"></th>
      <th>Name</th><th>Type</th><th>Owner</th><th>Status</th><th>Email</th><th>URL</th><th>Created</th><th>Updated</th><th></th>
    </tr>
  </thead>
  <tbody>
    {% for c in companies %}
    <tr>
      <td><input type="checkbox" name="ids" value="{{ c['id'] }}" class="rowchk"></td>
      <td><a href='{{ url_for('company_detail', cid=c['id']) }}'>{{ c.get('name') or 'Unnamed' }}</a></td>
      <td>{{ c['type'] }}</td>
      <td>{{ c.get('owner','-') }}</td>
      <td>{{ c['status'] }}</td>
      <td>{{ c.get('email','') }}</td>
      <td>{% if c.get('url') %}<a href='{{ c['url'] }}' target='_blank'>link</a>{% endif %}</td>
      <td>{{ c['created_at'] }}</td>
      <td>{{ c['updated_at'] }}</td>
      <td>
        <!-- Use mass_delete endpoint for single-row delete as well -->
        <button class='btn btn-sm btn-outline-danger'
                type='submit'
                name='ids' value='{{ c["id"] }}'
                formaction='{{ url_for("mass_delete") }}'
                formmethod='post'
                onclick="return confirm('Delete this company?');">
          Delete
        </button>
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
<div class="d-flex gap-2">
  <button class="btn btn-danger" type="submit" onclick="return confirmMass()">Delete selected</button>
  <a class="btn btn-outline-secondary" href="{{ url_for('list_view', q=q, sort=sort) }}">Refresh</a>
</div>
</form>

<script>
  const chkAll = document.getElementById('chkAll');
  const rowChks = Array.from(document.querySelectorAll('.rowchk'));
  chkAll && chkAll.addEventListener('change', ()=> rowChks.forEach(c => c.checked = chkAll.checked));
  function confirmMass(){
    const any = rowChks.some(c => c.checked);
    if(!any){ return confirm('No checkboxes selected. Delete the row you clicked instead?'); }
    return confirm('Delete selected records? This cannot be undone.');
  }
</script>
"""

IMPORT_HTML = """
<div class='row'>
  <div class='col-lg-8'>
    <div class='card shadow-sm'><div class='card-body'>
      <h1 class='h5'>Import Leads (CSV)</h1>
      <p class='text-muted'>Columns supported (headers optional): name, url, email, linkedin, type, status, owner, notes</p>
      <form method='post' enctype='multipart/form-data'>
        <div class='mb-3'><input class='form-control' type='file' accept='.csv' name='file' required></div>
        <div class='form-check mb-3'>
          <input class='form-check-input' type='checkbox' name='skip_dups' value='1' checked id='skipdups'>
          <label class='form-check-label' for='skipdups'>Skip duplicates (match by name or URL)</label>
        </div>
        <button class='btn btn-primary' type='submit'>Import</button>
        <a class='btn btn-outline-secondary' href='{{ url_for('list_view') }}'>Back</a>
      </form>
    </div></div>
  </div>
</div>
"""

# ------------------------ Routes ------------------------

@app.route('/')
def home():
    return redirect(url_for('board'))

@app.route('/add', methods=['GET', 'POST'])
def add_company():
    prefs = load_prefs()
    if request.method == 'POST':
        name_in = (request.form.get('name') or '').strip()
        url_in  = (request.form.get('url') or '').strip()

        # Duplicate validation
        with db() as con:
            dup = con.execute("SELECT id, name, url FROM companies").fetchall()
        for c in dup:
            if name_in and norm_text(c['name']) == norm_text(name_in):
                flash("Duplicate by name. Open existing: ")
                return redirect(url_for('company_detail', cid=c['id']))
            if url_in and norm_url(c['url']) == norm_url(url_in):
                flash("Duplicate by URL. Open existing: ")
                return redirect(url_for('company_detail', cid=c['id']))

        cid = str(uuid.uuid4())
        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        payload = {
            'id': cid,
            'type': request.form.get('type') or prefs.get('last_type') or TYPES[0],
            'owner': request.form.get('owner') or prefs.get('last_owner') or OWNERS[0],
            'name': name_in,
            'url': url_in,
            'linkedin': (request.form.get('linkedin') or '').strip(),
            'email': (request.form.get('email') or '').strip(),
            'contacted_email': 1 if request.form.get('contacted_email') else 0,
            'contacted_url': 1 if request.form.get('contacted_url') else 0,
            'contacted_linkedin': 1 if request.form.get('contacted_linkedin') else 0,
            'status': request.form.get('status') or 'New',
            'created_at': now,
            'updated_at': now,
        }
        # Parse sources (JSON array preferred; comma-separated fallback)
        sources_raw = (request.form.get('sources') or '').strip()
        try:
            sources = [s for s in json.loads(sources_raw) if isinstance(s, str)]
        except Exception:
            sources = [s.strip() for s in sources_raw.split(',') if s.strip()]

        with db() as con:
            con.execute("""
                INSERT INTO companies(id,type,owner,name,url,linkedin,email,
                                      contacted_email,contacted_url,contacted_linkedin,status,created_at,updated_at)
                VALUES(:id,:type,:owner,:name,:url,:linkedin,:email,
                       :contacted_email,:contacted_url,:contacted_linkedin,:status,:created_at,:updated_at)
            """, payload)
            if sources:
                con.executemany("INSERT OR IGNORE INTO sources(company_id, source) VALUES(?,?)",
                                [(cid, s) for s in sources])
            first_note = (request.form.get('notes') or '').strip()
            if first_note:
                con.execute("INSERT INTO notes(id,company_id,time,text) VALUES(?,?,?,?)",
                            (str(uuid.uuid4()), cid, now, first_note))
            con.commit()

        # remember last selections
        prefs['last_type'] = payload['type']
        prefs['last_owner'] = payload['owner']
        prefs['last_sources'] = json.dumps(sources)
        save_prefs(prefs)
        flash('Company added.')
        return redirect(url_for('board'))

    defaults = {
      'last_type': prefs.get('last_type', TYPES[0]),
      'last_owner': prefs.get('last_owner', OWNERS[0]),
      'pre_sources': json.loads(prefs.get('last_sources', '[]') or '[]')
    }
    latest_sources = get_latest_sources(10)
    body = render_template_string(ADD_HTML, types=TYPES, owners=OWNERS, statuses=STATUSES,
      defaults=defaults, latest_sources=latest_sources)
    return render_template_string(BASE_HTML, title='Add Company', body=body)

@app.route('/company/<cid>')
def company_detail(cid):
    c = get_company(cid)
    if not c:
        return render_template_string(BASE_HTML, title='Not Found', body='<div class="alert alert-warning">Company not found.</div>')
    body = render_template_string(DETAIL_HTML, company=c, statuses=STATUSES, owners=OWNERS)
    return render_template_string(BASE_HTML, title='Company Detail', body=body)

@app.route('/company/<cid>', methods=['POST'])
def update_company(cid):
    prefs = load_prefs()
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    # Parse sources
    sources_raw = (request.form.get('sources') or '').strip()
    try:
        sources = [s for s in json.loads(sources_raw) if isinstance(s, str)]
    except Exception:
        sources = [s.strip() for s in sources_raw.split(',') if s.strip()]
    with db() as con:
        con.execute("""
            UPDATE companies SET
              status = COALESCE(?, status),
              owner = COALESCE(?, owner),
              contacted_email = ?,
              contacted_url = ?,
              contacted_linkedin = ?,
              updated_at = ?
            WHERE id=?
        """, (
            request.form.get('status'),
            request.form.get('owner'),
            1 if request.form.get('contacted_email') else 0,
            1 if request.form.get('contacted_url') else 0,
            1 if request.form.get('contacted_linkedin') else 0,
            now, cid
        ))
        # Update sources: replace set
        con.execute("DELETE FROM sources WHERE company_id=?", (cid,))
        if sources:
            con.executemany("INSERT OR IGNORE INTO sources(company_id, source) VALUES(?,?)",
                            [(cid, s) for s in sources])
        con.commit()
    if request.form.get('owner'):
        prefs['last_owner'] = request.form.get('owner')
        save_prefs(prefs)
    flash('Company updated.')
    return redirect(url_for('company_detail', cid=cid))

@app.route('/company/<cid>/note', methods=['POST'])
def add_note(cid):
    note = (request.form.get('note') or '').strip()
    if not note:
        return redirect(url_for('company_detail', cid=cid))
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    with db() as con:
        con.execute("INSERT INTO notes(id,company_id,time,text) VALUES(?,?,?,?)",
                    (str(uuid.uuid4()), cid, now, note))
        con.execute("UPDATE companies SET updated_at=? WHERE id=?", (now, cid))
        con.commit()
    flash('Note added.')
    return redirect(url_for('company_detail', cid=cid))

@app.route('/board')
def board():
    data = load_data()
    companies = sorted(data['companies'], key=lambda x: x.get('updated_at',''), reverse=True)
    body = render_template_string(BOARD_HTML, statuses=STATUSES, companies=companies)
    return render_template_string(BASE_HTML, title='Board', body=body)

@app.route('/list')
def list_view():
    data = load_data()
    q = (request.args.get('q') or '').strip().lower()
    sort = request.args.get('sort') or 'updated'
    items = data['companies']
    if q:
        def match(c):
            hay = ' '.join([
                (c.get('name') or '').lower(),
                (c.get('url') or '').lower(),
                (c.get('email') or '').lower(),
                (c.get('owner') or '').lower(),
            ])
            return q in hay
        items = [c for c in items if match(c)]
    if sort == 'created':
        items = sorted(items, key=lambda x: x.get('created_at',''), reverse=True)
    else:
        items = sorted(items, key=lambda x: x.get('updated_at',''), reverse=True)
    body = render_template_string(LIST_HTML, companies=items, q=q, sort=sort)
    return render_template_string(BASE_HTML, title='Companies', body=body)

@app.route('/company/<cid>/delete', methods=['POST'])
def delete_company(cid):
    with db() as con:
        con.execute("DELETE FROM notes WHERE company_id=?", (cid,))
        con.execute("DELETE FROM sources WHERE company_id=?", (cid,))
        con.execute("DELETE FROM companies WHERE id=?", (cid,))
        con.commit()
    flash('Deleted 1 company.')
    return redirect(url_for('list_view'))

@app.route('/mass_delete', methods=['POST'])
def mass_delete():
    ids = request.form.getlist('ids')
    if not ids:
        flash('No records selected.')
        return redirect(url_for('list_view'))
    with db() as con:
        q = ",".join("?" for _ in ids)
        con.execute(f"DELETE FROM notes WHERE company_id IN ({q})", ids)
        con.execute(f"DELETE FROM sources WHERE company_id IN ({q})", ids)
        cur = con.execute(f"DELETE FROM companies WHERE id IN ({q})", ids)
        deleted = cur.rowcount
        con.commit()
    flash(f'Deleted {deleted} record(s).')
    return redirect(url_for('list_view'))

# CSV import (unchanged; does not set sources)
@app.route('/import', methods=['GET','POST'])
def import_csv():
    if request.method == 'POST':
        f = request.files.get('file')
        if not f:
            flash('No file provided')
            return redirect(url_for('import_csv'))
        content = f.stream.read().decode('utf-8', errors='ignore')
        lines = [ln for ln in content.splitlines() if ln.strip()]
        try:
            reader = csv.DictReader(lines)
            rows = list(reader)
        except Exception:
            rows = []
        if not rows:
            rows = []
            for line in lines:
                parts = [p.strip() for p in line.split(',')]
                while len(parts) < 8: parts.append('')
                rows.append({'name':parts[0],'url':parts[1],'email':parts[2],'linkedin':parts[3],'type':parts[4],'status':parts[5],'owner':parts[6],'notes':parts[7]})

        prefs = load_prefs()
        skip_dups = bool(request.form.get('skip_dups', '1'))
        added = skipped = 0

        with db() as con:
            existing = con.execute("SELECT id, name, url FROM companies").fetchall()
            for r in rows:
                name_in = r.get('name','')
                url_in = r.get('url','')
                dup_id = None
                for c in existing:
                    if name_in and norm_text(c['name']) == norm_text(name_in): dup_id = c['id']; break
                    if url_in and norm_url(c['url']) == norm_url(url_in): dup_id = c['id']; break
                if dup_id and skip_dups:
                    skipped += 1
                    continue
                cid = str(uuid.uuid4())
                now = datetime.now().strftime('%Y-%m-%d %H:%M')
                payload = {
                    'id': cid,
                    'type': (r.get('type') or prefs.get('last_type') or TYPES[0]).strip() or TYPES[0],
                    'owner': (r.get('owner') or prefs.get('last_owner') or OWNERS[0]).strip() or OWNERS[0],
                    'name': (name_in or '').strip(),
                    'url': (url_in or '').strip(),
                    'linkedin': (r.get('linkedin') or '').strip(),
                    'email': (r.get('email') or '').strip(),
                    'status': (r.get('status') or 'New').strip() or 'New',
                    'created_at': now,
                    'updated_at': now,
                }
                con.execute("""
                    INSERT INTO companies(id,type,owner,name,url,linkedin,email,
                                          contacted_email,contacted_url,contacted_linkedin,status,created_at,updated_at)
                    VALUES(:id,:type,:owner,:name,:url,:linkedin,:email,0,0,0,:status,:created_at,:updated_at)
                """, payload)
                note = (r.get('notes') or '').strip()
                if note:
                    con.execute("INSERT INTO notes(id,company_id,time,text) VALUES(?,?,?,?)",
                                (str(uuid.uuid4()), cid, now, note))
                existing.append({'id': cid, 'name': payload['name'], 'url': payload['url']})
                added += 1
            con.commit()

        flash(f'Import finished. Added {added}, skipped {skipped}.')
        return redirect(url_for('list_view'))
    body = render_template_string(IMPORT_HTML)
    return render_template_string(BASE_HTML, title='Import', body=body)

# API endpoints
@app.route('/api/update_status', methods=['POST'])
def api_update_status():
    try:
        payload = request.get_json(force=True)
        cid = payload.get('id'); status = payload.get('status')
        if status not in STATUSES:
            return jsonify({"ok": False, "error": "Invalid status"})
        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        with db() as con:
            cur = con.execute("UPDATE companies SET status=?, updated_at=? WHERE id=?", (status, now, cid))
            con.commit()
            if cur.rowcount:
                return jsonify({"ok": True})
            return jsonify({"ok": False, "error": "Company not found"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route('/api/check_duplicate')
def api_check_duplicate():
    name = request.args.get('name',''); url_ = request.args.get('url','')
    with db() as con:
        rows = con.execute("SELECT id, name, url FROM companies").fetchall()
        for c in rows:
            if name and norm_text(name) == norm_text(c['name']):
                return jsonify({'duplicate': True, 'id': c['id'], 'link': url_for('company_detail', cid=c['id'])})
            if url_ and norm_url(url_) == norm_url(c['url']):
                return jsonify({'duplicate': True, 'id': c['id'], 'link': url_for('company_detail', cid=c['id'])})
    return jsonify({'duplicate': False})

if __name__ == '__main__':
    ensure_storage()
    app.run(debug=True, host='0.0.0.0', port=4500)