# mini_crm.py (updated)
# Added features: search on board, remember last Type & Lead Owner, Lead Owner dropdown, duplicate validation
# Run:  python3 mini_crm.py  (open http://127.0.0.1:5000)

import os
import json
import uuid
from datetime import datetime
from flask import Flask, request, redirect, url_for, render_template_string, jsonify, flash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, 'data')
DATA_FILE = os.path.join(DATA_DIR, 'companies.json')
PREFS_FILE = os.path.join(DATA_DIR, 'prefs.json')

STATUSES = ["Contacted", "Followup Sent", "Replied", "Discovery"]
TYPES = ["marketing", "development", "merchant"]
OWNERS = ["Oskars", "Shawn"]

# ------------------------ Data helpers ------------------------

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


essential_keys = {"last_type": TYPES[0], "last_owner": OWNERS[0]}

def load_prefs():
    ensure_storage()
    try:
        with open(PREFS_FILE, 'r', encoding='utf-8') as f:
            prefs = json.load(f)
    except Exception:
        prefs = {}
    # fill defaults if missing
    for k, v in essential_keys.items():
        prefs.setdefault(k, v)
    return prefs


def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def save_prefs(prefs):
    with open(PREFS_FILE, 'w', encoding='utf-8') as f:
        json.dump(prefs, f, indent=2, ensure_ascii=False)


def get_company(cid):
    data = load_data()
    for c in data["companies"]:
        if c["id"] == cid:
            return c
    return None


def norm_text(s: str) -> str:
    return (s or '').strip().lower()


def norm_url(u: str) -> str:
    u = (u or '').strip().lower()
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
      body { background: #f8f9fa; }
      .kanban { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; }
      .kanban-column { background: white; border-radius: .75rem; box-shadow: 0 2px 12px rgba(0,0,0,.05); padding: .75rem; }
      .kanban-header { font-weight: 700; font-size: 1rem; margin-bottom: .5rem; }
      .card { cursor: pointer; }
      .form-hint { font-size: .875rem; color: #6c757d; }
      .note { background: #fff; border-radius: .5rem; padding: .75rem; border: 1px solid #e9ecef; }
      .badge-type { text-transform: capitalize; }
      .search-wrap { position: sticky; top: .5rem; z-index: 2; }
      .dim { opacity: .35; }
    </style>
  </head>
  <body>
    <nav class=\"navbar navbar-expand-lg bg-body-tertiary mb-3\">
      <div class=\"container\">
        <a class=\"navbar-brand\" href=\"{{ url_for('board') }}\">Mini CRM</a>
        <div class=\"d-flex gap-2\">
          <a class=\"btn btn-primary\" href=\"{{ url_for('add_company') }}\">Add Company</a>
          <a class=\"btn btn-outline-secondary\" href=\"{{ url_for('board') }}\">Board</a>
        </div>
      </div>
    </nav>

    <div class=\"container\">
      {% with messages = get_flashed_messages() %}
        {% if messages %}
          <div class=\"alert alert-warning\">{{ messages[0]|safe }}</div>
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
  <div class=\"col-lg-8\">
    <div class=\"card shadow-sm\">
      <div class=\"card-body\">
        <h1 class=\"h4 mb-3\">Add Company / Lead</h1>
        <form method=\"post\" action=\"{{ url_for('add_company') }}\">
          <div class=\"row g-3\">
            <div class=\"col-md-4\">
              <label class=\"form-label\">Type</label>
              <select class=\"form-select\" name=\"type\" required>
                {% for t in types %}
                <option value=\"{{t}}\" {% if t==defaults.get('type') %}selected{% endif %}>{{t}}</option>
                {% endfor %}
              </select>
            </div>
            <div class=\"col-md-4\">
              <label class=\"form-label\">Lead Owner</label>
              <select class=\"form-select\" name=\"owner\" required>
                {% for o in owners %}
                <option value=\"{{o}}\" {% if o==defaults.get('owner') %}selected{% endif %}>{{o}}</option>
                {% endfor %}
              </select>
            </div>
            <div class=\"col-md-4\">
              <label class=\"form-label\">Status</label>
              <select class=\"form-select\" name=\"status\" required>
                {% for s in statuses %}
                <option value=\"{{s}}\">{{s}}</option>
                {% endfor %}
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
                <div class=\"input-group-text\">
                  <input class=\"form-check-input mt-0\" type=\"checkbox\" name=\"contacted_email\" value=\"1\" aria-label=\"Contacted via Email\">
                </div>
              </div>
              <div class=\"form-hint\">Tick box if you contacted them via email.</div>
            </div>

            <div class=\"col-md-6\">
              <label class=\"form-label\">Website URL (optional)</label>
              <div class=\"input-group\">
                <input class=\"form-control\" type=\"url\" name=\"url\" placeholder=\"https://acme.com\">
                <div class=\"input-group-text\">
                  <input class=\"form-check-input mt-0\" type=\"checkbox\" name=\"contacted_url\" value=\"1\" aria-label=\"Contacted via Web Form\">
                </div>
              </div>
              <div class=\"form-hint\">Tick box if you contacted them via website form.</div>
            </div>

            <div class=\"col-md-6\">
              <label class=\"form-label\">LinkedIn (optional)</label>
              <div class=\"input-group\">
                <input class=\"form-control\" type=\"url\" name=\"linkedin\" placeholder=\"https://linkedin.com/company/acme\">
                <div class=\"input-group-text\">
                  <input class=\"form-check-input mt-0\" type=\"checkbox\" name=\"contacted_linkedin\" value=\"1\" aria-label=\"Contacted via LinkedIn\">
                </div>
              </div>
              <div class=\"form-hint\">Tick box if you contacted them via LinkedIn.</div>
            </div>

            <div class=\"col-12\">
              <label class=\"form-label\">Notes (optional)</label>
              <textarea class=\"form-control\" name=\"notes\" rows=\"3\" placeholder=\"Context, summary, next steps...\"></textarea>
            </div>
          </div>
          <div class=\"mt-3 d-flex gap-2\">
            <button class=\"btn btn-primary\" type=\"submit\">Save</button>
            <a class=\"btn btn-secondary\" href=\"{{ url_for('board') }}\">Cancel</a>
          </div>
        </form>
      </div>
    </div>
  </div>
</div>
"""

DETAIL_HTML = """
<div class=\"row\">
  <div class=\"col-lg-8\">
    <div class=\"card shadow-sm mb-3\">
      <div class=\"card-body\">
        <div class=\"d-flex justify-content-between align-items-start\">
          <div>
            <h1 class=\"h4 mb-1\">{{ company.get('name') or 'Unnamed Company' }}</h1>
            <div class=\"d-flex gap-2 align-items-center\">
              <span class=\"badge text-bg-secondary badge-type\">{{ company['type'] }}</span>
              <span class=\"badge text-bg-dark\">Owner: {{ company.get('owner','-') }}</span>
              <span class=\"badge text-bg-info\">{{ company['status'] }}</span>
            </div>
          </div>
          <a href=\"{{ url_for('board') }}\" class=\"btn btn-outline-secondary\">Back to Board</a>
        </div>

        <hr>
        <div class=\"row g-3\">
          {% if company.get('email') %}
          <div class=\"col-md-6\">
            <strong>Email:</strong> <a href=\"mailto:{{ company['email'] }}\">{{ company['email'] }}</a>
            {% if company['contacted_via'].get('email') %}<span class=\"badge text-bg-success ms-2\">contacted</span>{% endif %}
          </div>
          {% endif %}

          {% if company.get('url') %}
          <div class=\"col-md-6\">
            <strong>Website:</strong> <a href=\"{{ company['url'] }}\" target=\"_blank\">{{ company['url'] }}</a>
            {% if company['contacted_via'].get('url') %}<span class=\"badge text-bg-success ms-2\">contacted</span>{% endif %}
          </div>
          {% endif %}

          {% if company.get('linkedin') %}
          <div class=\"col-md-6\">
            <strong>LinkedIn:</strong> <a href=\"{{ company['linkedin'] }}\" target=\"_blank\">{{ company['linkedin'] }}</a>
            {% if company['contacted_via'].get('linkedin') %}<span class=\"badge text-bg-success ms-2\">contacted</span>{% endif %}
          </div>
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
          <div class=\"col-md-4 d-flex align-items-end gap-2\">
            <button class=\"btn btn-primary\" type=\"submit\">Update</button>
            <a class=\"btn btn-outline-secondary\" href=\"{{ url_for('board') }}\">Cancel</a>
          </div>

          <div class=\"col-12\">
            <label class=\"form-label\">Contacted via</label>
            <div class=\"form-check form-check-inline\">
              <input class=\"form-check-input\" type=\"checkbox\" name=\"contacted_email\" value=\"1\" {% if company['contacted_via'].get('email') %}checked{% endif %}>
              <label class=\"form-check-label\">Email</label>
            </div>
            <div class=\"form-check form-check-inline\">
              <input class=\"form-check-input\" type=\"checkbox\" name=\"contacted_url\" value=\"1\" {% if company['contacted_via'].get('url') %}checked{% endif %}>
              <label class=\"form-check-label\">Website form</label>
            </div>
            <div class=\"form-check form-check-inline\">
              <input class=\"form-check-input\" type=\"checkbox\" name=\"contacted_linkedin\" value=\"1\" {% if company['contacted_via'].get('linkedin') %}checked{% endif %}>
              <label class=\"form-check-label\">LinkedIn</label>
            </div>
          </div>
        </form>
      </div>
    </div>

    <div class=\"card shadow-sm\">
      <div class=\"card-body\">
        <h2 class=\"h5\">Notes</h2>
        <form method=\"post\" action=\"{{ url_for('add_note', cid=company['id']) }}\" class=\"mb-3\">
          <div class=\"input-group\">
            <textarea class=\"form-control\" name=\"note\" rows=\"2\" placeholder=\"Add a note...\"></textarea>
            <button class=\"btn btn-primary\" type=\"submit\">Add</button>
          </div>
        </form>
        {% if company['notes'] %}
          <div class=\"vstack gap-2\">
            {% for n in company['notes']|reverse %}
              <div class=\"note\">
                <div class=\"small text-muted\">{{ n['time'] }}</div>
                <div>{{ n['text']|replace('\n','<br>')|safe }}</div>
              </div>
            {% endfor %}
          </div>
        {% else %}
          <div class=\"text-muted\">No notes yet.</div>
        {% endif %}
      </div>
    </div>
  </div>

  <div class=\"col-lg-4\">
    <div class=\"card shadow-sm\">
      <div class=\"card-body\">
        <h2 class=\"h6\">Meta</h2>
        <div class=\"small text-muted\">Created: {{ company['created_at'] }}<br>Updated: {{ company['updated_at'] }}</div>
      </div>
    </div>
  </div>
</div>
"""

BOARD_HTML = """
<div class=\"d-flex justify-content-between align-items-center mb-3\">
  <h1 class=\"h4 mb-0\">Company Board</h1>
  <div class=\"search-wrap\" style=\"min-width: 260px;\">
    <div class=\"input-group\">
      <span class=\"input-group-text\">Search</span>
      <input id=\"searchInput\" class=\"form-control\" type=\"search\" placeholder=\"Type company, url, email, owner...\">
      <button class=\"btn btn-outline-secondary\" type=\"button\" id=\"clearBtn\">Clear</button>
    </div>
  </div>
</div>

<div class=\"kanban\" id=\"kanban\">
  {% for s in statuses %}
  <div class=\"kanban-column\">
    <div class=\"kanban-header\">{{ s }}</div>
    <div class=\"kanban-list\" id=\"col-{{ loop.index0 }}\" data-status=\"{{ s }}\">
      {% for c in companies if c['status']==s %}
        <div class=\"card mb-2\" data-id=\"{{ c['id'] }}\" data-name=\"{{ (c.get('name') or '')|lower }}\" data-url=\"{{ (c.get('url') or '')|lower }}\" data-email=\"{{ (c.get('email') or '')|lower }}\" data-owner=\"{{ (c.get('owner') or '')|lower }}\" onclick=\"openCompany(event, '{{ url_for('company_detail', cid=c['id']) }}')\">
          <div class=\"card-body py-2\">
            <div class=\"d-flex justify-content-between align-items-center\">
              <div class=\"fw-semibold\">{{ c.get('name') or 'Unnamed' }}</div>
              <div class=\"d-flex gap-1 align-items-center\">
                {% if c.get('owner') %}<span class=\"badge rounded-pill text-bg-dark\">{{ c['owner'] }}</span>{% endif %}
                <span class=\"badge rounded-pill text-bg-secondary badge-type\">{{ c['type'] }}</span>
              </div>
            </div>
            {% if c.get('email') %}<div class=\"small text-muted\">{{ c['email'] }}</div>{% endif %}
            {% if c.get('url') %}<div class=\"small text-muted\">{{ c['url'] }}</div>{% endif %}
          </div>
        </div>
      {% endfor %}
    </div>
  </div>
  {% endfor %}
</div>

<script>
  function openCompany(ev, href){
    if (document.body.classList.contains('dragging')) return;
    window.location = href;
  }

  // Drag & drop
  document.querySelectorAll('.kanban-list').forEach(function(list){
    new Sortable(list, {
      group: 'kanban',
      animation: 150,
      onStart: () => document.body.classList.add('dragging'),
      onEnd: () => document.body.classList.remove('dragging'),
      onAdd: function (evt) {
        const card = evt.item;
        const id = card.getAttribute('data-id');
        const newStatus = evt.to.getAttribute('data-status');
        fetch('{{ url_for('api_update_status') }}', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: id, status: newStatus })
        }).then(r => r.json()).then(j => {
          if(!j.ok){
            alert('Failed to update: ' + (j.error || 'unknown'));
            evt.from.insertBefore(card, evt.from.children[evt.oldIndex]);
          }
        }).catch(err => {
          alert('Network error');
          evt.from.insertBefore(card, evt.from.children[evt.oldIndex]);
        });
      }
    });
  });

  // Search filter
  const q = document.getElementById('searchInput');
  const clearBtn = document.getElementById('clearBtn');
  function applyFilter(){
    const term = (q.value || '').toLowerCase().trim();
    document.querySelectorAll('.kanban-list .card').forEach(card => {
      const hay = [
        card.dataset.name || '',
        card.dataset.url || '',
        card.dataset.email || '',
        card.dataset.owner || ''
      ].join(' ');
      const match = term === '' || hay.includes(term);
      card.style.display = match ? '' : 'none';
    });
    // Dim empty columns when searching
    document.querySelectorAll('.kanban-column').forEach(col => {
      const visible = col.querySelectorAll('.card:not([style*="display: none"])').length;
      col.classList.toggle('dim', q.value && visible === 0);
    });
  }
  q && q.addEventListener('input', applyFilter);
  clearBtn && clearBtn.addEventListener('click', () => { q.value=''; applyFilter(); q.focus(); });
  applyFilter();
</script>
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
        name_in = request.form.get('name') or ''
        url_in = request.form.get('url') or ''
        # Duplicate validation by name or URL (case-insensitive, trimmed)
        existing = None
        for c in data['companies']:
            if norm_text(c.get('name')) and norm_text(c.get('name')) == norm_text(name_in):
                existing = c
                break
            if norm_url(c.get('url')) and norm_url(c.get('url')) == norm_url(url_in):
                existing = c
                break
        if existing:
            link = url_for('company_detail', cid=existing['id'])
            flash(f"Possible duplicate found. <a href='{link}'>Open existing company</a>.")
            return redirect(url_for('add_company'))

        payload = {
            "id": str(uuid.uuid4()),
            "type": request.form.get('type') or prefs.get('last_type') or TYPES[0],
            "owner": request.form.get('owner') or prefs.get('last_owner') or OWNERS[0],
            "name": (name_in).strip(),
            "url": (url_in).strip(),
            "linkedin": (request.form.get('linkedin') or '').strip(),
            "email": (request.form.get('email') or '').strip(),
            "contacted_via": {
                "email": bool(request.form.get('contacted_email')),
                "url": bool(request.form.get('contacted_url')),
                "linkedin": bool(request.form.get('contacted_linkedin')),
            },
            "notes": [],
            "status": request.form.get('status') or STATUSES[0],
            "created_at": datetime.now().strftime('%Y-%m-%d %H:%M'),
            "updated_at": datetime.now().strftime('%Y-%m-%d %H:%M'),
        }
        first_note = (request.form.get('notes') or '').strip()
        if first_note:
            payload["notes"].append({"time": datetime.now().strftime('%Y-%m-%d %H:%M'), "text": first_note})
        data["companies"].append(payload)
        save_data(data)

        # remember last selections
        prefs['last_type'] = payload['type']
        prefs['last_owner'] = payload['owner']
        save_prefs(prefs)

        flash('Company added.')
        return redirect(url_for('board'))

    defaults = {"type": prefs.get('last_type', TYPES[0]), "owner": prefs.get('last_owner', OWNERS[0])}
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
    for c in data['companies']:
        if c['id'] == cid:
            c['status'] = request.form.get('status') or c['status']
            c['owner'] = request.form.get('owner') or c.get('owner')
            c['contacted_via']['email'] = bool(request.form.get('contacted_email'))
            c['contacted_via']['url'] = bool(request.form.get('contacted_url'))
            c['contacted_via']['linkedin'] = bool(request.form.get('contacted_linkedin'))
            c['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M')
            save_data(data)
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
            c['notes'].append({"time": datetime.now().strftime('%Y-%m-%d %H:%M'), "text": note})
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


@app.route('/api/update_status', methods=['POST'])
def api_update_status():
    try:
        payload = request.get_json(force=True)
        cid = payload.get('id')
        status = payload.get('status')
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


if __name__ == '__main__':
    ensure_storage()
    app.run(debug=True, host='0.0.0.0', port=4500)

