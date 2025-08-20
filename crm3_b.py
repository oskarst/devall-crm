# mini_crm.py — Simple Bootstrap CRM (JSON storage)
# Features:
# - Add Company with Type, Owner, URL, LinkedIn, Email, checkboxes for contact channels, Notes, Status (incl. New)
# - Remember last selected Type & Owner (prefs.json)
# - Company detail: timestamped notes, change status/contacted flags
# - Kanban board: columns for New, Contacted, Followup Sent, Replied, Discovery; drag & drop + live search
# - List view: search, sort by created/updated, delete
# - CSV import: header or headerless; duplicate skipping; maps columns
# - Duplicate detection: on-blur in Add form and on submit (name/URL) with link to existing

import os
import json
import uuid
import csv
from datetime import datetime
from flask import Flask, request, redirect, url_for, render_template_string, jsonify, flash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, 'data')
DATA_FILE = os.path.join(DATA_DIR, 'companies.json')
PREFS_FILE = os.path.join(DATA_DIR, 'prefs.json')

STATUSES = ["New", "Contacted", "Followup Sent", "Replied", "Discovery"]
TYPES = ["marketing", "development", "merchant"]
OWNERS = ["Oskars", "Shawn"]

# ------------------------ Helpers ------------------------

def ensure_storage():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump({"companies": []}, f, indent=2)
    if not os.path.exists(PREFS_FILE):
        with open(PREFS_FILE, 'w', encoding='utf-8') as f:
            json.dump({"last_type": TYPES[0], "last_owner": OWNERS[0]}, f, indent=2)

def load_data():
    ensure_storage()
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_prefs():
    ensure_storage()
    with open(PREFS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_prefs(prefs):
    with open(PREFS_FILE, 'w', encoding='utf-8') as f:
        json.dump(prefs, f, indent=2, ensure_ascii=False)

def get_company(cid):
    data = load_data()
    for c in data["companies"]:
        if c["id"] == cid:
            return c
    return None

def norm_text(s):
    return (s or '').strip().lower()

def norm_url(u):
    u = (u or '').strip().lower()
    # strip scheme and trailing slash for matching
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
          <div class=\"col-md-3\">
            <label class=\"form-label\">Lead Owner</label>
            <select class=\"form-select\" name=\"owner\" required>
              {% for o in owners %}<option value=\"{{o}}\" {% if o==defaults.last_owner %}selected{% endif %}>{{o}}</option>{% endfor %}
            </select>
          </div>
          <div class=\"col-md-6\">
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
        <div class=\"vstack gap-2\">{% for n in company['notes']|reverse %}<div class=\"note\"><div class=\"small text-muted\">{{ n['time'] }}</div><div>{{ n['text']|replace('\n','<br>')|safe }}</div></div>{% endfor %}</div>
      {% else %}<div class=\"text-muted\">No notes yet.</div>{% endif %}
    </div></div>
  </div>
  <div class=\"col-lg-4\"><div class=\"card shadow-sm\"><div class=\"card-body\"><h2 class=\"h6\">Meta</h2><div class=\"small text-muted\">Created: {{ company['created_at'] }}<br>Updated: {{ company['updated_at'] }}</div></div></div></div>
</div>
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
<table class='table table-hover align-middle'>
  <thead><tr><th>Name</th><th>Type</th><th>Owner</th><th>Status</th><th>Email</th><th>URL</th><th>Created</th><th>Updated</th><th></th></tr></thead>
  <tbody>
    {% for c in companies %}
    <tr>
      <td><a href='{{ url_for('company_detail', cid=c['id']) }}'>{{ c.get('name') or 'Unnamed' }}</a></td>
      <td>{{ c['type'] }}</td>
      <td>{{ c.get('owner','-') }}</td>
      <td>{{ c['status'] }}</td>
      <td>{{ c.get('email','') }}</td>
      <td>{% if c.get('url') %}<a href='{{ c['url'] }}' target='_blank'>link</a>{% endif %}</td>
      <td>{{ c['created_at'] }}</td>
      <td>{{ c['updated_at'] }}</td>
      <td>
        <form method='post' action='{{ url_for('delete_company', cid=c['id']) }}' onsubmit="return confirm('Delete this company?');">
          <button class='btn btn-sm btn-outline-danger' type='submit'>Delete</button>
        </form>
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
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
        data = load_data()
        # Duplicate validation
        name_in = (request.form.get('name') or '').strip()
        url_in  = (request.form.get('url') or '').strip()
        for c in data['companies']:
            if name_in and norm_text(c.get('name')) == norm_text(name_in):
                flash(f"Duplicate by name. Open existing: ")
                return redirect(url_for('company_detail', cid=c['id']))
            if url_in and norm_url(c.get('url')) == norm_url(url_in):
                flash(f"Duplicate by URL. Open existing: ")
                return redirect(url_for('company_detail', cid=c['id']))
        payload = {
            'id': str(uuid.uuid4()),
            'type': request.form.get('type') or prefs.get('last_type') or TYPES[0],
            'owner': request.form.get('owner') or prefs.get('last_owner') or OWNERS[0],
            'name': name_in,
            'url': url_in,
            'linkedin': (request.form.get('linkedin') or '').strip(),
            'email': (request.form.get('email') or '').strip(),
            'contacted_via': {
                'email': bool(request.form.get('contacted_email')),
                'url': bool(request.form.get('contacted_url')),
                'linkedin': bool(request.form.get('contacted_linkedin')),
            },
            'notes': [],
            'status': request.form.get('status') or 'New',
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
        }
        first_note = (request.form.get('notes') or '').strip()
        if first_note:
            payload['notes'].append({'time': datetime.now().strftime('%Y-%m-%d %H:%M'), 'text': first_note})
        data['companies'].append(payload)
        save_data(data)
        # remember last selections
        prefs['last_type'] = payload['type']
        prefs['last_owner'] = payload['owner']
        save_prefs(prefs)
        flash('Company added.')
        return redirect(url_for('board'))

    defaults = {'last_type': prefs.get('last_type', TYPES[0]), 'last_owner': prefs.get('last_owner', OWNERS[0])}
    body = render_template_string(ADD_HTML, types=TYPES, owners=OWNERS, statuses=STATUSES, defaults=defaults)
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
    data = load_data()
    prefs = load_prefs()
    for c in data['companies']:
        if c['id'] == cid:
            c['status'] = request.form.get('status') or c['status']
            c['owner'] = request.form.get('owner') or c.get('owner') or OWNERS[0]
            c['contacted_via']['email'] = bool(request.form.get('contacted_email'))
            c['contacted_via']['url'] = bool(request.form.get('contacted_url'))
            c['contacted_via']['linkedin'] = bool(request.form.get('contacted_linkedin'))
            c['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M')
            save_data(data)
            # update prefs to most recent edits (optional but handy)
            prefs['last_owner'] = c['owner']
            save_prefs(prefs)
            flash('Company updated.')
            break
    return redirect(url_for('company_detail', cid=cid))

@app.route('/company/<cid>/note', methods=['POST'])
def add_note(cid):
    note = (request.form.get('note') or '').strip()
    if not note:
        return redirect(url_for('company_detail', cid=cid))
    data = load_data()
    for c in data['companies']:
        if c['id'] == cid:
            c['notes'].append({'time': datetime.now().strftime('%Y-%m-%d %H:%M'), 'text': note})
            c['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M')
            save_data(data)
            flash('Note added.')
            break
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
    data = load_data()
    before = len(data['companies'])
    data['companies'] = [c for c in data['companies'] if c['id'] != cid]
    save_data(data)
    flash('Deleted 1 company.' if len(data['companies']) < before else 'Company not found.')
    return redirect(url_for('list_view'))

# CSV import
@app.route('/import', methods=['GET','POST'])
def import_csv():
    if request.method == 'POST':
        f = request.files.get('file')
        if not f:
            flash('No file provided')
            return redirect(url_for('import_csv'))
        content = f.stream.read().decode('utf-8', errors='ignore')
        lines = [ln for ln in content.splitlines() if ln.strip()]
        reader = csv.DictReader(lines)
        rows = list(reader)
        # Fallback to headerless
        if not rows:
            rows = []
            for line in lines:
                parts = [p.strip() for p in line.split(',')]
                while len(parts) < 8: parts.append('')
                rows.append({'name':parts[0],'url':parts[1],'email':parts[2],'linkedin':parts[3],'type':parts[4],'status':parts[5],'owner':parts[6],'notes':parts[7]})
        data = load_data()
        prefs = load_prefs()
        skip_dups = bool(request.form.get('skip_dups', '1'))
        added = skipped = 0
        for r in rows:
            name_in = r.get('name',''); url_in = r.get('url','')
            dup_id = None
            for c in data['companies']:
                if name_in and norm_text(c.get('name')) == norm_text(name_in): dup_id = c['id']; break
                if url_in and norm_url(c.get('url')) == norm_url(url_in): dup_id = c['id']; break
            if dup_id and skip_dups:
                skipped += 1
                continue
            payload = {
                'id': str(uuid.uuid4()),
                'type': (r.get('type') or prefs.get('last_type') or TYPES[0]).strip() or TYPES[0],
                'owner': (r.get('owner') or prefs.get('last_owner') or OWNERS[0]).strip() or OWNERS[0],
                'name': (name_in or '').strip(),
                'url': (url_in or '').strip(),
                'linkedin': (r.get('linkedin') or '').strip(),
                'email': (r.get('email') or '').strip(),
                'contacted_via': {'email': False, 'url': False, 'linkedin': False},
                'notes': [],
                'status': (r.get('status') or 'New').strip() or 'New',
                'created_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
                'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            }
            note = (r.get('notes') or '').strip()
            if note:
                payload['notes'].append({'time': datetime.now().strftime('%Y-%m-%d %H:%M'), 'text': note})
            data['companies'].append(payload)
            added += 1
        save_data(data)
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
        data = load_data()
        for c in data['companies']:
            if c['id'] == cid:
                c['status'] = status
                c['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M')
                save_data(data)
                return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "Company not found"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route('/api/check_duplicate')
def api_check_duplicate():
    name = request.args.get('name',''); url_ = request.args.get('url','')
    data = load_data()
    for c in data['companies']:
        if name and norm_text(name) == norm_text(c.get('name')):
            return jsonify({'duplicate': True, 'id': c['id'], 'link': url_for('company_detail', cid=c['id'])})
        if url_ and norm_url(url_) == norm_url(c.get('url')):
            return jsonify({'duplicate': True, 'id': c['id'], 'link': url_for('company_detail', cid=c['id'])})
    return jsonify({'duplicate': False})

if __name__ == '__main__':
    ensure_storage()
    app.run(debug=True, host='0.0.0.0', port=4500)

