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
from flask import Flask, request, redirect, url_for, render_template_string, jsonify, flash, session
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, 'data')
DB_PATH   = os.path.join(DATA_DIR, 'crm.db')

LEAD_STATUSES = ["New", "Contacted", "Qualified", "Negotiation", "Lost"]
PARTNER_STATUSES = ["Onboarding", "Active Project", "Follow-up Needed", "Paused", "Source Partner"]
ALL_STATUSES = LEAD_STATUSES + PARTNER_STATUSES + ["Past Client"]
TYPES = ["Marketing Agency", "Agency", "Direct Customer", "Hosting Provider"]
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
          starred INTEGER DEFAULT 0,
          category TEXT DEFAULT 'General',
          FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
        )""")
        # Backward-compat for existing DBs
        try:
            cur.execute("ALTER TABLE notes ADD COLUMN starred INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE notes ADD COLUMN category TEXT DEFAULT 'General'")
        except Exception:
            pass
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

        # users table
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          username TEXT UNIQUE NOT NULL,
          password_hash TEXT NOT NULL,
          role TEXT NOT NULL CHECK(role IN ('Admin','Manager','Sales'))
        )""")
        # seed dev users if table empty
        row = cur.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        if not row or row["n"] == 0:
            cur.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                        ('oskars', generate_password_hash('K0k0k00la'), 'Admin'))
            cur.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                        ('shawn', generate_password_hash('shawn123'), 'Manager'))
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
            "SELECT id, time, text, starred, category FROM notes WHERE company_id=? ORDER BY time ASC", (cid,)
        ).fetchall()
        sources = con.execute(
            "SELECT source FROM sources WHERE company_id=? ORDER BY source COLLATE NOCASE", (cid,)
        ).fetchall()
        c = row_to_company(row)
        c['notes'] = [{'id': n['id'], 'time': n['time'], 'text': n['text'], 'starred': bool(n['starred']), 'category': n['category'] or 'General'} for n in notes]
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

def current_user():
    uid = session.get('user_id')
    if not uid:
        return None
    with db() as con:
        row = con.execute("SELECT id, username, role FROM users WHERE id=?", (uid,)).fetchone()
    return dict(row) if row else None

def login_required(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for('login', next=request.path))
        return fn(*args, **kwargs)
    return wrapper

def role_required(*roles):
    from functools import wraps
    @wraps
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                return redirect(url_for('login', next=request.path))
            if roles and user['role'] not in roles:
                flash('You do not have permission to access this page.')
                return redirect(url_for('home'))
            return fn(*args, **kwargs)
        return wrapper
    return deco

@app.context_processor
def inject_user():
    return {'user': current_user()}
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
                <div class="d-flex gap-2">
          {% if user %}
            <span class="navbar-text me-2">Hi, {{ user.username }} ({{ user.role }})</span>
            <a class="btn btn-primary" href="{{ url_for('add_company') }}">Add Company</a>
            <a class="btn btn-outline-secondary" href="{{ url_for('board') }}">Lead Board</a>
            <a class="btn btn-outline-secondary" href="{{ url_for('partners_board') }}">Partners Board</a>
            <a class="btn btn-outline-secondary" href="{{ url_for('list_view') }}">List</a>
            <a class="btn btn-outline-secondary" href="{{ url_for('import_csv') }}">Import</a>
            <a class="btn btn-outline-danger" href="{{ url_for('logout') }}">Logout</a>
          {% else %}
            <a class="btn btn-outline-primary" href="{{ url_for('login') }}">Login</a>
          {% endif %}
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

LOGIN_HTML = """
<div class="row justify-content-center">
  <div class="col-md-5">
    <div class="card shadow-sm">
      <div class="card-body">
        <h1 class="h4 mb-3">Sign in</h1>
        <form method="post" action="{{ url_for('login', next=next_url) }}">
          <div class="mb-3">
            <label class="form-label">Username</label>
            <input class="form-control" type="text" name="username" autofocus required>
          </div>
          <div class="mb-3">
            <label class="form-label">Password</label>
            <input class="form-control" type="password" name="password" required>
          </div>
          <button class="btn btn-primary" type="submit">Sign in</button>
          <a class="btn btn-link" href="{{ url_for('home') }}">Cancel</a>
        </form>
      </div>
    </div>
  </div>
</div>
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
        <div class="col-12 d-flex justify-content-end">
          {% if is_partner %}
            <button class="btn btn-sm btn-outline-secondary" type="button" data-bs-toggle="collapse" data-bs-target="#advEdit">Show more fields</button>
          {% endif %}
        </div>
        <div id="advEdit" class="collapse {% if not is_partner %}show{% endif %}">
        <div class="row g-3">
          <div class="col-md-4">
            <label class="form-label">Type</label>
            <select class="form-select" name="type">
              {% for t in types %}<option value="{{t}}" {% if t==company.get('type') %}selected{% endif %}>{{t}}</option>{% endfor %}
            </select>
          </div>

          <div class="col-12">
            <label class="form-label">Sources</label>
            <div class="mb-2" id="srcTagsDetail"></div>
            <div class="input-group">
              <input class="form-control" id="srcInputDetail" placeholder="Type a source and press Enter">
              <button class="btn btn-outline-secondary" type="button" id="srcAddBtnDetail">Add</button>
            </div>
            <input type="hidden" name="sources" id="srcHiddenDetail" value='{{ (company.get("sources") or []) | tojson }}'>
            <div class="form-text">Add/remove tags; they will be saved on Update.</div>
          </div>

          <div class="col-12">
            <label class="form-label">Contacted via</label>
            <div class="form-check form-check-inline"><input class="form-check-input" type="checkbox" name="contacted_email" value="1" {% if company['contacted_via'].get('email') %}checked{% endif %}><label class="form-check-label">Email</label></div>
            <div class="form-check form-check-inline"><input class="form-check-input" type="checkbox" name="contacted_url" value="1" {% if company['contacted_via'].get('url') %}checked{% endif %}><label class="form-check-label">Website form</label></div>
            <div class="form-check form-check-inline"><input class="form-check-input" type="checkbox" name="contacted_linkedin" value="1" {% if company['contacted_via'].get('linkedin') %}checked{% endif %}><label class="form-check-label">LinkedIn</label></div>
          </div>
        </div>
      </div>
        <div class=\"col-12 d-flex gap-2\"><button class=\"btn btn-primary\" type=\"submit\">Update</button><a class=\"btn btn-outline-secondary\" href=\"{{ url_for('board') }}\">Cancel</a></div>
      </form>
    </div></div>

    <div class="card shadow-sm"><div class="card-body">
      <h2 class="h5 mb-3">Notes</h2>
      <form method="post" action="{{ url_for('add_note', cid=company['id']) }}" class="mb-3 row g-2">
        <div class="col-12">
          <div class="input-group">
            <textarea class="form-control" name="note" rows="2" placeholder="Add a note..."></textarea>
            <button class="btn btn-primary" type="submit">Add</button>
          </div>
        </div>
        <div class="col-md-6">
          <label class="form-label">Category</label>
          <select class="form-select" name="category">
            <option value="General">General</option>
            <option value="Contacts">Contacts</option>
            <option value="Agreements">Agreements</option>
          </select>
        </div>
        <div class="col-md-6 d-flex align-items-end">
          <div class="form-check">
            <input class="form-check-input" type="checkbox" id="starNew" name="starred" value="1">
            <label class="form-check-label" for="starNew">Star this note</label>
          </div>
        </div>
      </form>

      <ul class="nav nav-tabs" id="noteTabs" role="tablist">
        <li class="nav-item" role="presentation">
          <button class="nav-link active" id="tab-general" data-bs-toggle="tab" data-bs-target="#pane-general" type="button" role="tab">
            General Notes ({{ company['notes'] | selectattr('category','equalto','General') | list | length }})
          </button>
        </li>
        <li class="nav-item" role="presentation">
          <button class="nav-link" id="tab-contacts" data-bs-toggle="tab" data-bs-target="#pane-contacts" type="button" role="tab">
            Contacts ({{ company['notes'] | selectattr('category','equalto','Contacts') | list | length }})
          </button>
        </li>
        {% if user and user.role == 'Admin' %}
        <li class="nav-item" role="presentation">
          <button class="nav-link" id="tab-agreements" data-bs-toggle="tab" data-bs-target="#pane-agreements" type="button" role="tab">
            Agreements ({{ company['notes'] | selectattr('category','equalto','Agreements') | list | length }})
          </button>
        </li>
        {% endif %}
        <li class="nav-item" role="presentation">
          <button class="nav-link" id="tab-starred" data-bs-toggle="tab" data-bs-target="#pane-starred" type="button" role="tab">
            Starred ({{ company['notes'] | selectattr('starred') | list | length }})
          </button>
        </li>
      </ul>

      <div class="tab-content pt-3">
        <div class="tab-pane fade show active" id="pane-general" role="tabpanel">
          {% set items = company['notes'] | selectattr('category', 'equalto', 'General') | list %}
          {% if items %}
            <div class="vstack gap-2">
              {% for n in items | reverse %}
                <div class="note">
                  <div class="small text-muted d-flex justify-content-between align-items-center">
                    <span>{{ n['time'] }}{% if n['category'] %} • {{ n['category'] }}{% endif %}</span>
                    <div class="d-flex gap-1">
                      <form method="post" action="{{ url_for('toggle_star', cid=company['id'], nid=n['id']) }}">
                        <input type="hidden" name="from" value="general">
                        <button class="btn btn-sm {% if n['starred'] %}btn-warning{% else %}btn-outline-secondary{% endif %}" type="submit" title="Star / Unstar">
                          {% if n['starred'] %}★{% else %}☆{% endif %}
                        </button>
                      </form>
                      <button class="btn btn-sm btn-outline-primary" type="button" data-bs-toggle="collapse" data-bs-target="#edit-{{ n['id'] }}" title="Edit note">
                        Edit
                      </button>
                      <form method="post" action="{{ url_for('delete_note', cid=company['id'], nid=n['id']) }}" onsubmit="return confirm('Delete this note?');">
                        <button class="btn btn-sm btn-outline-danger" type="submit" title="Delete note">Delete</button>
                      </form>
                    </div>
                  </div>

                  <div class="mt-1">{{ n['text']|replace('\n','<br>')|safe }}</div>

                  <div class="collapse mt-2" id="edit-{{ n['id'] }}">
                    <form method="post" action="{{ url_for('edit_note', cid=company['id'], nid=n['id']) }}">
                      <div class="mb-2">
                        <textarea class="form-control" name="text" rows="3">{{ n['text'] }}</textarea>
                      </div>
                      <div class="row g-2">
                        <div class="col-md-6">
                          <select class="form-select" name="category">
                            <option value="General" {% if n['category']=='General' %}selected{% endif %}>General</option>
                            <option value="Contacts" {% if n['category']=='Contacts' %}selected{% endif %}>Contacts</option>
                            <option value="Agreements" {% if n['category']=='Agreements' %}selected{% endif %}>Agreements</option>
                          </select>
                        </div>
                        <div class="col-md-6 d-flex align-items-center">
                          <div class="form-check">
                            <input class="form-check-input" type="checkbox" name="starred" value="1" {% if n['starred'] %}checked{% endif %} id="star-{{ n['id'] }}">
                            <label class="form-check-label" for="star-{{ n['id'] }}">Starred</label>
                          </div>
                        </div>
                      </div>
                      <div class="mt-2">
                        <button class="btn btn-sm btn-primary" type="submit">Save</button>
                        <button class="btn btn-sm btn-secondary" type="button" data-bs-toggle="collapse" data-bs-target="#edit-{{ n['id'] }}">Cancel</button>
                      </div>
                    </form>
                  </div>
                </div>
              {% endfor %}
            </div>
          {% else %}<div class="text-muted">No notes yet.</div>{% endif %}
        </div>

        <div class="tab-pane fade" id="pane-contacts" role="tabpanel">
          {% set items = company['notes'] | selectattr('category', 'equalto', 'Contacts') | list %}
          {% if items %}
            <div class="vstack gap-2">
              {% for n in items | reverse %}
                <div class="note">
                  <div class="small text-muted d-flex justify-content-between align-items-center">
                    <span>{{ n['time'] }}{% if n['category'] %} • {{ n['category'] }}{% endif %}</span>
                    <div class="d-flex gap-1">
                      <form method="post" action="{{ url_for('toggle_star', cid=company['id'], nid=n['id']) }}">
                        <input type="hidden" name="from" value="general">
                        <button class="btn btn-sm {% if n['starred'] %}btn-warning{% else %}btn-outline-secondary{% endif %}" type="submit" title="Star / Unstar">
                          {% if n['starred'] %}★{% else %}☆{% endif %}
                        </button>
                      </form>
                      <button class="btn btn-sm btn-outline-primary" type="button" data-bs-toggle="collapse" data-bs-target="#edit-{{ n['id'] }}" title="Edit note">
                        Edit
                      </button>
                      <form method="post" action="{{ url_for('delete_note', cid=company['id'], nid=n['id']) }}" onsubmit="return confirm('Delete this note?');">
                        <button class="btn btn-sm btn-outline-danger" type="submit" title="Delete note">Delete</button>
                      </form>
                    </div>
                  </div>

                  <div class="mt-1">{{ n['text']|replace('\n','<br>')|safe }}</div>

                  <div class="collapse mt-2" id="edit-{{ n['id'] }}">
                    <form method="post" action="{{ url_for('edit_note', cid=company['id'], nid=n['id']) }}">
                      <div class="mb-2">
                        <textarea class="form-control" name="text" rows="3">{{ n['text'] }}</textarea>
                      </div>
                      <div class="row g-2">
                        <div class="col-md-6">
                          <select class="form-select" name="category">
                            <option value="General" {% if n['category']=='General' %}selected{% endif %}>General</option>
                            <option value="Contacts" {% if n['category']=='Contacts' %}selected{% endif %}>Contacts</option>
                            <option value="Agreements" {% if n['category']=='Agreements' %}selected{% endif %}>Agreements</option>
                          </select>
                        </div>
                        <div class="col-md-6 d-flex align-items-center">
                          <div class="form-check">
                            <input class="form-check-input" type="checkbox" name="starred" value="1" {% if n['starred'] %}checked{% endif %} id="star-{{ n['id'] }}">
                            <label class="form-check-label" for="star-{{ n['id'] }}">Starred</label>
                          </div>
                        </div>
                      </div>
                      <div class="mt-2">
                        <button class="btn btn-sm btn-primary" type="submit">Save</button>
                        <button class="btn btn-sm btn-secondary" type="button" data-bs-toggle="collapse" data-bs-target="#edit-{{ n['id'] }}">Cancel</button>
                      </div>
                    </form>
                  </div>
                </div>
              {% endfor %}
            </div>
          {% else %}<div class="text-muted">No notes yet.</div>{% endif %}
        </div>

        {% if user and user.role == 'Admin' %}
        <div class="tab-pane fade" id="pane-agreements" role="tabpanel">
          {% set items = company['notes'] | selectattr('category', 'equalto', 'Agreements') | list %}
          {% if items %}
            <div class="vstack gap-2">
              {% for n in items | reverse %}
                <div class="note">
                  <div class="small text-muted d-flex justify-content-between align-items-center">
                    <span>{{ n['time'] }}{% if n['category'] %} • {{ n['category'] }}{% endif %}</span>
                    <div class="d-flex gap-1">
                      <form method="post" action="{{ url_for('toggle_star', cid=company['id'], nid=n['id']) }}">
                        <input type="hidden" name="from" value="general">
                        <button class="btn btn-sm {% if n['starred'] %}btn-warning{% else %}btn-outline-secondary{% endif %}" type="submit" title="Star / Unstar">
                          {% if n['starred'] %}★{% else %}☆{% endif %}
                        </button>
                      </form>
                      <button class="btn btn-sm btn-outline-primary" type="button" data-bs-toggle="collapse" data-bs-target="#edit-{{ n['id'] }}" title="Edit note">
                        Edit
                      </button>
                      <form method="post" action="{{ url_for('delete_note', cid=company['id'], nid=n['id']) }}" onsubmit="return confirm('Delete this note?');">
                        <button class="btn btn-sm btn-outline-danger" type="submit" title="Delete note">Delete</button>
                      </form>
                    </div>
                  </div>

                  <div class="mt-1">{{ n['text']|replace('\n','<br>')|safe }}</div>

                  <div class="collapse mt-2" id="edit-{{ n['id'] }}">
                    <form method="post" action="{{ url_for('edit_note', cid=company['id'], nid=n['id']) }}">
                      <div class="mb-2">
                        <textarea class="form-control" name="text" rows="3">{{ n['text'] }}</textarea>
                      </div>
                      <div class="row g-2">
                        <div class="col-md-6">
                          <select class="form-select" name="category">
                            <option value="General" {% if n['category']=='General' %}selected{% endif %}>General</option>
                            <option value="Contacts" {% if n['category']=='Contacts' %}selected{% endif %}>Contacts</option>
                            <option value="Agreements" {% if n['category']=='Agreements' %}selected{% endif %}>Agreements</option>
                          </select>
                        </div>
                        <div class="col-md-6 d-flex align-items-center">
                          <div class="form-check">
                            <input class="form-check-input" type="checkbox" name="starred" value="1" {% if n['starred'] %}checked{% endif %} id="star-{{ n['id'] }}">
                            <label class="form-check-label" for="star-{{ n['id'] }}">Starred</label>
                          </div>
                        </div>
                      </div>
                      <div class="mt-2">
                        <button class="btn btn-sm btn-primary" type="submit">Save</button>
                        <button class="btn btn-sm btn-secondary" type="button" data-bs-toggle="collapse" data-bs-target="#edit-{{ n['id'] }}">Cancel</button>
                      </div>
                    </form>
                  </div>
                </div>
              {% endfor %}
            </div>
          {% else %}<div class="text-muted">No notes yet.</div>{% endif %}
        </div>
        {% endif %}
        <div class="tab-pane fade" id="pane-starred" role="tabpanel">
          {% set items = company['notes'] | selectattr('starred') | list %}
          {% if items %}
            <div class="vstack gap-2">
              {% for n in items | reverse %}
                <div class="note">
                  <div class="small text-muted d-flex justify-content-between align-items-center">
                    <span>{{ n['time'] }}{% if n['category'] %} • {{ n['category'] }}{% endif %}</span>
                    <div class="d-flex gap-1">
                      <form method="post" action="{{ url_for('toggle_star', cid=company['id'], nid=n['id']) }}">
                        <input type="hidden" name="from" value="general">
                        <button class="btn btn-sm {% if n['starred'] %}btn-warning{% else %}btn-outline-secondary{% endif %}" type="submit" title="Star / Unstar">
                          {% if n['starred'] %}★{% else %}☆{% endif %}
                        </button>
                      </form>
                      <button class="btn btn-sm btn-outline-primary" type="button" data-bs-toggle="collapse" data-bs-target="#edit-{{ n['id'] }}" title="Edit note">
                        Edit
                      </button>
                      <form method="post" action="{{ url_for('delete_note', cid=company['id'], nid=n['id']) }}" onsubmit="return confirm('Delete this note?');">
                        <button class="btn btn-sm btn-outline-danger" type="submit" title="Delete note">Delete</button>
                      </form>
                    </div>
                  </div>

                  <div class="mt-1">{{ n['text']|replace('\n','<br>')|safe }}</div>

                  <div class="collapse mt-2" id="edit-{{ n['id'] }}">
                    <form method="post" action="{{ url_for('edit_note', cid=company['id'], nid=n['id']) }}">
                      <div class="mb-2">
                        <textarea class="form-control" name="text" rows="3">{{ n['text'] }}</textarea>
                      </div>
                      <div class="row g-2">
                        <div class="col-md-6">
                          <select class="form-select" name="category">
                            <option value="General" {% if n['category']=='General' %}selected{% endif %}>General</option>
                            <option value="Contacts" {% if n['category']=='Contacts' %}selected{% endif %}>Contacts</option>
                            <option value="Agreements" {% if n['category']=='Agreements' %}selected{% endif %}>Agreements</option>
                          </select>
                        </div>
                        <div class="col-md-6 d-flex align-items-center">
                          <div class="form-check">
                            <input class="form-check-input" type="checkbox" name="starred" value="1" {% if n['starred'] %}checked{% endif %} id="star-{{ n['id'] }}">
                            <label class="form-check-label" for="star-{{ n['id'] }}">Starred</label>
                          </div>
                        </div>
                      </div>
                      <div class="mt-2">
                        <button class="btn btn-sm btn-primary" type="submit">Save</button>
                        <button class="btn btn-sm btn-secondary" type="button" data-bs-toggle="collapse" data-bs-target="#edit-{{ n['id'] }}">Cancel</button>
                      </div>
                    </form>
                  </div>
                </div>
              {% endfor %}
            </div>
          {% else %}<div class="text-muted">No starred notes yet.</div>{% endif %}
        </div>
      </div>
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
  <h1 class="h4 mb-0">{{ board_title }}</h1>
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
            <div class=\"fw-semibold\">{{ c.get('name') or 'Unnamed' }}<br>
              <span class=\"badge rounded-pill text-bg-secondary badge-type\">{{ c['type'] }}</span>
            </div>
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
    <input type='hidden' name='sort' value='{{ sort }}'>
    <input type='hidden' name='dir' value='{{ dir }}'>
    <button class='btn btn-outline-secondary' type='submit'>Search</button>
  </form>
</div>

<form method="post" action="{{ url_for('mass_delete') }}" id="massForm">
<table class='table table-hover align-middle'>
  <thead>
    <tr>
      <th style="width:36px;"><input type="checkbox" id="chkAll"></th>
      <th>
        <a href="{{ url_for('list_view', q=q, sort='name', dir=next_dir('name')) }}">
          Name {{ caret('name') }}
        </a>
      </th>
      <th>
        <a href="{{ url_for('list_view', q=q, sort='type', dir=next_dir('type')) }}">
          Type {{ caret('type') }}
        </a>
      </th>
      <th>
        <a href="{{ url_for('list_view', q=q, sort='owner', dir=next_dir('owner')) }}">
          Owner {{ caret('owner') }}
        </a>
      </th>
      <th>
        <a href="{{ url_for('list_view', q=q, sort='status', dir=next_dir('status')) }}">
          Status {{ caret('status') }}
        </a>
      </th>
      <th>
        <a href="{{ url_for('list_view', q=q, sort='email', dir=next_dir('email')) }}">
          Email {{ caret('email') }}
        </a>
      </th>
      <th>
        <a href="{{ url_for('list_view', q=q, sort='url', dir=next_dir('url')) }}">
          URL {{ caret('url') }}
        </a>
      </th>
      <th>
        <a href="{{ url_for('list_view', q=q, sort='created', dir=next_dir('created')) }}">
          Created {{ caret('created') }}
        </a>
      </th>
      <th>
        <a href="{{ url_for('list_view', q=q, sort='updated', dir=next_dir('updated')) }}">
          Updated {{ caret('updated') }}
        </a>
      </th>
      <th></th>
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
  <a class="btn btn-outline-secondary" href="{{ url_for('list_view', q=q, sort=sort, dir=dir) }}">Refresh</a>
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

@app.route('/login', methods=['GET','POST'])
def login():
    next_url = request.args.get('next') or request.form.get('next') or url_for('home')
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        with db() as con:
            row = con.execute("SELECT id, username, password_hash, role FROM users WHERE username=?", (username,)).fetchone()
        if row and check_password_hash(row['password_hash'], password):
            session['user_id'] = row['id']
            flash('Signed in.')
            return redirect(next_url)
        flash('Invalid credentials.')
    body = render_template_string(LOGIN_HTML, next_url=next_url)
    return render_template_string(BASE_HTML, title='Sign in', body=body, user=current_user())

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    flash('Signed out.')
    return redirect(url_for('login'))

@app.route('/add', methods=['GET', 'POST'])
@login_required
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
    body = render_template_string(ADD_HTML, types=TYPES, owners=OWNERS, statuses=ALL_STATUSES,
      defaults=defaults, latest_sources=latest_sources)
    return render_template_string(BASE_HTML, title='Add Company', body=body, user=current_user())

@app.route('/company/<cid>')
@login_required
def company_detail(cid):
    c = get_company(cid)
    if not c:
        return render_template_string(BASE_HTML, title='Not Found', body='<div class="alert alert-warning">Company not found.</div>', user=current_user())
    body = render_template_string(
    DETAIL_HTML,
    company=c,
    statuses=ALL_STATUSES,
    owners=OWNERS,
    types=TYPES,
    is_partner=(c.get('status') in PARTNER_STATUSES)
)
    return render_template_string(BASE_HTML, title='Company Detail', body=body, user=current_user())

@app.route('/company/<cid>', methods=['POST'])
@login_required
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
        new_status = request.form.get('status')

        con.execute("""
            UPDATE companies SET
              type = COALESCE(?, type),
              status = COALESCE(?, status),
              owner = COALESCE(?, owner),
              contacted_email = ?,
              contacted_url = ?,
              contacted_linkedin = ?,
              updated_at = ?
            WHERE id=?
        """, (
            request.form.get('type'),
            new_status,
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
@login_required
def add_note(cid):
    note = (request.form.get('note') or '').strip()
    if not note:
        return redirect(url_for('company_detail', cid=cid))
    category = (request.form.get('category') or 'General').strip() or 'General'
    starred = 1 if request.form.get('starred') else 0
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    with db() as con:
        con.execute("INSERT INTO notes(id,company_id,time,text,starred,category) VALUES(?,?,?,?,?,?)",
                    (str(uuid.uuid4()), cid, now, note, starred, category))
        con.execute("UPDATE companies SET updated_at=? WHERE id=?", (now, cid))
        con.commit()
    flash('Note added.')
    return redirect(url_for('company_detail', cid=cid))

@app.route('/company/<cid>/note/<nid>/star', methods=['POST'])
@login_required
def toggle_star(cid, nid):
    refer = request.form.get('from','')
    with db() as con:
        row = con.execute("SELECT starred FROM notes WHERE id=? AND company_id=?", (nid, cid)).fetchone()
        if row is not None:
            new_val = 0 if row['starred'] else 1
            con.execute("UPDATE notes SET starred=? WHERE id=? AND company_id=?", (new_val, nid, cid))
            con.execute("UPDATE companies SET updated_at=? WHERE id=?", (datetime.now().strftime('%Y-%m-%d %H:%M'), cid))
            con.commit()
    return redirect(url_for('company_detail', cid=cid) + (f"#{refer}" if refer else ""))

@app.route('/company/<cid>/note/<nid>/edit', methods=['POST'])
@login_required
def edit_note(cid, nid):
    new_text = (request.form.get('text') or '').strip()
    new_category = (request.form.get('category') or 'General').strip() or 'General'
    new_starred = 1 if request.form.get('starred') else 0
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    with db() as con:
        con.execute("""
            UPDATE notes
               SET text = ?,
                   category = ?,
                   starred = ?
             WHERE id = ? AND company_id = ?
        """, (new_text, new_category, new_starred, nid, cid))
        con.execute("UPDATE companies SET updated_at=? WHERE id=?", (now, cid))
        con.commit()
    return redirect(url_for('company_detail', cid=cid))

@app.route('/company/<cid>/note/<nid>/delete', methods=['POST'])
@login_required
def delete_note(cid, nid):
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    with db() as con:
        con.execute("DELETE FROM notes WHERE id=? AND company_id=?", (nid, cid))
        con.execute("UPDATE companies SET updated_at=? WHERE id=?", (now, cid))
        con.commit()
    return redirect(url_for('company_detail', cid=cid))

@app.route('/board')
@login_required
def board():
    data = load_data()
    # Only show leads (not partners)
    companies = [c for c in data['companies'] if (c.get('status') in LEAD_STATUSES)]
    companies = sorted(companies, key=lambda x: x.get('updated_at',''), reverse=True)
    body = render_template_string(BOARD_HTML,
                                  board_title='Lead Board',
                                  statuses=LEAD_STATUSES,
                                  companies=companies)
    return render_template_string(BASE_HTML, title='Lead Board', body=body, user=current_user())

@app.route('/partners')
@login_required
def partners_board():
    data = load_data()
    # Only show partner statuses
    companies = [c for c in data['companies'] if (c.get('status') in PARTNER_STATUSES)]
    companies = sorted(companies, key=lambda x: x.get('updated_at',''), reverse=True)
    body = render_template_string(BOARD_HTML,
                                  board_title='Partners Board',
                                  statuses=PARTNER_STATUSES,
                                  companies=companies)
    return render_template_string(BASE_HTML, title='Partners Board', body=body, user=current_user())

@app.route('/list')
@login_required
def list_view():
    data = load_data()
    q = (request.args.get('q') or '').strip().lower()
    sort = (request.args.get('sort') or 'updated').lower()
    direction = (request.args.get('dir') or 'desc').lower()
    if direction not in ('asc', 'desc'):
        direction = 'desc'

    items = data['companies']

    if q:
        def match(c):
            hay = ' '.join([
                (c.get('name') or '').lower(),
                (c.get('url') or '').lower(),
                (c.get('email') or '').lower(),
                (c.get('owner') or '').lower(),
                (c.get('type') or '').lower(),
                (c.get('status') or '').lower(),
            ])
            return q in hay
        items = [c for c in items if match(c)]

    def key_for(c, field):
        if field == 'name':   return (c.get('name') or '').lower()
        if field == 'type':   return (c.get('type') or '').lower()
        if field == 'owner':  return (c.get('owner') or '').lower()
        if field == 'status': return (c.get('status') or '').lower()
        if field == 'email':  return (c.get('email') or '').lower()
        if field == 'url':    return (c.get('url') or '').lower()
        if field in ('created', 'created_at'): return c.get('created_at','')
        if field in ('updated', 'updated_at'): return c.get('updated_at','')
        return c.get('updated_at','')

    reverse = (direction == 'desc')
    items = sorted(items, key=lambda c: key_for(c, sort), reverse=reverse)

    def caret(field):
        if field != sort:
            return ''
        return '▲' if direction == 'asc' else '▼'

    def next_dir(field):
        if field != sort:
            return 'asc'
        return 'desc' if direction == 'asc' else 'asc'

    body = render_template_string(
        LIST_HTML,
        companies=items,
        q=q,
        sort=sort,
        dir=direction,
        caret=caret,
        next_dir=next_dir
    )
    return render_template_string(BASE_HTML, title='Companies', body=body, user=current_user())

@app.route('/company/<cid>/delete', methods=['POST'])
@login_required
def delete_company(cid):
    with db() as con:
        con.execute("DELETE FROM notes WHERE company_id=?", (cid,))
        con.execute("DELETE FROM sources WHERE company_id=?", (cid,))
        con.execute("DELETE FROM companies WHERE id=?", (cid,))
        con.commit()
    flash('Deleted 1 company.')
    return redirect(url_for('list_view'))

@app.route('/mass_delete', methods=['POST'])
@login_required
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
@login_required
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
    return render_template_string(BASE_HTML, title='Import', body=body, user=current_user())

# API endpoints
@app.route('/api/update_status', methods=['POST'])
@login_required
def api_update_status():
    try:
        payload = request.get_json(force=True)
        cid = payload.get('id'); status = payload.get('status')
        if status not in ALL_STATUSES:
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
@login_required
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