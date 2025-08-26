#!/usr/bin/env python3
import os, json, sqlite3, datetime
from pathlib import Path
from flask import Flask, request, jsonify, render_template_string, redirect, url_for, send_from_directory, flash

# ===== Cargar .env si existe =====
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ===== Paths / App =====
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
IMG_DIR  = DATA_DIR / "images"
DB_PATH  = DATA_DIR / "hub.db"
os.makedirs(IMG_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-insecure-change-me")  # poné una real en prod

# ===== DB helpers =====
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS employees(
            uid TEXT PRIMARY KEY,
            nombre TEXT,
            casco INTEGER DEFAULT 0,
            lentes INTEGER DEFAULT 0,
            guantes INTEGER DEFAULT 0,
            epp_completo INTEGER DEFAULT 0,
            bloqueado INTEGER DEFAULT 0,
            force_rewrite INTEGER DEFAULT 0,
            updated_at TEXT
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS records(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            uid TEXT,
            nombre_tag TEXT,
            epp_tag_json TEXT,
            api_result_json TEXT,
            image_file TEXT
        )""")
        # migración blanda
        try:
            con.execute("ALTER TABLE employees ADD COLUMN force_rewrite INTEGER DEFAULT 0")
        except Exception:
            pass

init_db()

# ===== UI base =====
TPL_BASE = """
<!doctype html><html><head><meta charset="utf-8"/><title>{{title}}</title>
<style>
body{font-family:system-ui,Segoe UI,Roboto,Arial;max-width:1100px;margin:24px auto;padding:0 12px}
header{display:flex;gap:12px;align-items:center}
header a{padding:8px 12px;background:#eee;border-radius:8px;text-decoration:none;color:#333}
table{width:100%;border-collapse:collapse}th,td{border-bottom:1px solid #eee;padding:8px;text-align:left;vertical-align:top}
.thumb{height:64px}.card{border:1px solid #eee;border-radius:12px;padding:16px;margin:16px 0}
.ok{color:#086a2e}.err{color:#a00000}
input[type="text"]{padding:6px 8px;border:1px solid #ccc;border-radius:8px;width:100%}
label.chk{display:inline-block;margin-right:12px}
.row{display:flex;gap:16px;align-items:flex-start}.col{flex:1}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;background:#fff4d6;color:#7a5b00;margin-left:8px}
.kv{display:flex;gap:8px;flex-wrap:wrap}
.kv span{background:#f6f6f6;border:1px solid #eee;border-radius:999px;padding:2px 8px}
small.mono{font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; color:#666}
</style></head><body>
<header>
  <h2 style="flex:1">{{title}}</h2>
  <nav>
    <a href="{{url_for('dashboard')}}">Dashboard</a>
    <a href="{{url_for('employees')}}">Empleados</a>
  </nav>
</header>
{% with messages = get_flashed_messages(with_categories=true) %}
  {% if messages %}<div class="card">{% for cat,msg in messages %}<div class="{{cat}}">{{msg}}</div>{% endfor %}</div>{% endif %}
{% endwith %}
{{ body|safe }}
</body></html>
"""

def render(body, **kw):
    return render_template_string(TPL_BASE, body=body, **kw)

# ===== Helpers EPP =====
HUB_MIN_CONF = 0.6

def row_to_dict(e):
    return dict(e) if (e is not None and not isinstance(e, dict)) else (e or {})

def desired_payload_from_employee(e_row):
    ed = row_to_dict(e_row)
    desired_e = []
    if ed.get("casco"):   desired_e.append("casco")
    if ed.get("lentes"):  desired_e.append("lentes")
    if ed.get("guantes"): desired_e.append("guantes")
    return {"n": ed.get("nombre") or "", "e": desired_e, "fc": bool(ed.get("epp_completo")), "blk": bool(ed.get("bloqueado"))}

def epp_required_from_employee_row(e_row):
    ed = row_to_dict(e_row)
    req = []
    if ed.get("casco"):   req.append("casco")
    if ed.get("lentes"):  req.append("lentes")
    if ed.get("guantes"): req.append("guantes")
    # if ed.get("epp_completo"): req = ["casco","lentes","guantes","chaleco","botas"]
    return req

def epp_detected_from_api_result(api_result_json, min_conf=HUB_MIN_CONF):
    have = []
    try:
        data = json.loads(api_result_json) if api_result_json else {}
        if not (data.get("ok") and isinstance(data.get("result"), dict)):
            return have
        r = data["result"]
        for k_api, k_std in [
            ("casco", "casco"),
            ("gafas", "lentes"),
            ("lentes", "lentes"),
            ("guantes", "guantes"),
            ("chaleco", "chaleco"),
            ("botas", "botas"),
        ]:
            slot = r.get(k_api)
            if isinstance(slot, dict) and slot.get("present") and float(slot.get("confidence", 0)) >= min_conf:
                if k_std not in have:
                    have.append(k_std)
    except Exception:
        pass
    return have

def epp_list_to_str(epp_list):
    return ", ".join(epp_list) if epp_list else "-"

# ===== Dashboard =====
@app.get("/")
def dashboard():
    with db() as con:
        rows = con.execute("SELECT * FROM records ORDER BY id DESC LIMIT 50").fetchall()
    with db() as con:
        emps = {e["uid"]: e for e in con.execute("SELECT * FROM employees").fetchall()}

    body = """
    <div class="card"><h3>Últimas fichadas</h3>
    <table>
      <tr>
        <th>Fecha</th>
        <th>Foto</th>
        <th>UID</th>
        <th>Nombre</th>
        <th>Protección requerida</th>
        <th>Protección detectada</th>
        <th>¿Pasa?</th>
      </tr>
    """
    for r in rows:
        uid = r["uid"] or ""
        e = emps.get(uid)
        nombre = (e["nombre"] if e else "") or (r["nombre_tag"] or "")
        required = epp_required_from_employee_row(e)
        detected = epp_detected_from_api_result(r["api_result_json"], HUB_MIN_CONF)
        def norm(xs): return sorted(set("lentes" if x == "gafas" else x for x in xs))
        pasa = set(norm(required)).issubset(set(norm(detected))) if required else False
        img = f'<img class="thumb" src="{url_for("image", name=r["image_file"])}"/>' if r["image_file"] else ""
        body += f"""
        <tr>
          <td>{r['ts']}</td>
          <td>{img}</td>
          <td><a href="{url_for('edit_employee', uid=uid)}">{uid}</a></td>
          <td>{nombre}</td>
          <td>{epp_list_to_str(required)}</td>
          <td>{epp_list_to_str(detected)}</td>
          <td>{'✔️' if pasa else '❌'}</td>
        </tr>
        """
    body += "</table></div>"
    return render(body, title="Hub Fichador – Dashboard")

@app.get("/images/<name>")
def image(name):
    return send_from_directory(IMG_DIR, name)

# ===== Integración Sueño (HC Gateway) =====
import requests
from datetime import datetime as _dt
from datetime import timezone as _tz
from datetime import timedelta as _td
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

HC_BASE = os.getenv("HC_BASE", "https://api.hcgateway.shuchir.dev")
HC_USER = os.getenv("HC_USER")
HC_PASS = os.getenv("HC_PASS")

_hc_token = None
_hc_expiry = None

def _hc_login():
    global _hc_token, _hc_expiry
    if not HC_USER or not HC_PASS:
        raise RuntimeError("Faltan HC_USER / HC_PASS en el entorno")
    url = f"{HC_BASE}/api/v2/login"
    r = requests.post(url, json={"username": HC_USER, "password": HC_PASS}, timeout=20)
    r.raise_for_status()
    data = r.json()
    _hc_token = data.get("token")
    expiry_str = data.get("expiry")
    _hc_expiry = None
    if expiry_str:
        try:
            _hc_expiry = _dt.fromisoformat(expiry_str.replace("Z","+00:00"))
        except Exception:
            _hc_expiry = None
    if not _hc_token:
        raise RuntimeError("Login OK pero no vino 'token'")

def _hc_get_token():
    global _hc_token, _hc_expiry
    if _hc_token and _hc_expiry:
        if _dt.now(_tz.utc) + _td(seconds=60) < _hc_expiry:
            return _hc_token
    _hc_login()
    return _hc_token

SLEEP_STAGE_MAP = {0: "siesta/otro", 1: "despierto", 4: "ligero", 5: "profundo", 6: "REM"}

def _dur_minutes(start_iso, end_iso):
    try:
        a = _dt.fromisoformat(start_iso.replace("Z","+00:00"))
        b = _dt.fromisoformat(end_iso.replace("Z","+00:00"))
        return int(max(0, (b - a).total_seconds() // 60))
    except Exception:
        return 0

def _summarize_session(sess):
    start = sess.get("start"); end = sess.get("end")
    total = _dur_minutes(start, end)
    per = {}
    for st in (sess.get("data") or {}).get("stages", []):
        s = st.get("stage")
        mins = _dur_minutes(st.get("startTime"), st.get("endTime"))
        name = SLEEP_STAGE_MAP.get(s, f"etapa_{s}")
        per[name] = per.get(name, 0) + mins
    return {"start": start, "end": end, "total_min": total, "per_stage": per}

def _fmt_local(iso_str, fmt="%Y-%m-%d %H:%M"):
    if not iso_str: return "-"
    try:
        dt = _dt.fromisoformat(iso_str.replace("Z","+00:00"))
        tz = ZoneInfo("America/Argentina/Buenos_Aires") if ZoneInfo else None
        return (dt.astimezone(tz) if tz else dt.astimezone()).strftime(fmt)
    except Exception:
        return iso_str

def _local_midnight_range(days=7, include_today=False):
    """Rango [inicio 00:00, fin 23:59:59] de los últimos 'days' días.
       include_today=False -> termina AYER; True -> incluye HOY."""
    tz = ZoneInfo("America/Argentina/Buenos_Aires") if ZoneInfo else _tz.utc
    now_local = _dt.now(tz)
    if include_today:
        end_local = now_local.replace(hour=23, minute=59, second=59, microsecond=0)
    else:
        end_local = (now_local - _td(days=1)).replace(hour=23, minute=59, second=59, microsecond=0)
    start_local = (end_local - _td(days=days-1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local, end_local, tz

def fetch_sleep_last_days(days=7, include_today=False):
    """Dict por fecha local 'YYYY-MM-DD' -> {'total_min': X, 'per_stage': {...}}."""
    start_local, end_local, tz = _local_midnight_range(days, include_today=include_today)
    def to_utc(dt_local): return dt_local.astimezone(_tz.utc)
    q = {
        "start": {"$gte": to_utc(start_local).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "end":   {"$lte": to_utc(end_local).strftime("%Y-%m-%dT%H:%M:%SZ")}
    }
    tok = _hc_get_token()
    url = f"{HC_BASE}/api/v2/fetch/sleepSession"
    headers = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    r = requests.post(url, headers=headers, json={"queries": q}, timeout=30)
    if r.status_code in (401,403):
        _hc_login()
        headers["Authorization"] = f"Bearer {_hc_token}"
        r = requests.post(url, headers=headers, json={"queries": q}, timeout=30)
    r.raise_for_status()
    data = r.json()

    by_day = {}
    for sess in data:
        sm = _summarize_session(sess)
        try:
            st = _dt.fromisoformat(sm["start"].replace("Z","+00:00")).astimezone(tz)
            dkey = st.strftime("%Y-%m-%d")
        except Exception:
            dkey = start_local.strftime("%Y-%m-%d")
        day = by_day.setdefault(dkey, {"total_min": 0, "per_stage": {}})
        day["total_min"] += sm["total_min"]
        for k, v in sm["per_stage"].items():
            day["per_stage"][k] = day["per_stage"].get(k, 0) + v

    # completar días sin datos
    out = {}
    cur = start_local
    while cur <= end_local:
        k = cur.strftime("%Y-%m-%d")
        out[k] = by_day.get(k, {"total_min": 0, "per_stage": {}})
        cur += _td(days=1)

    return dict(sorted(out.items(), key=lambda kv: kv[0]))

# ===== Empleados =====
@app.get("/empleados")
def employees():
    with db() as con:
        rows = con.execute("SELECT * FROM employees ORDER BY updated_at DESC NULLS LAST").fetchall()
    body = """
    <div class="card"><div class="row">
      <div class="col"><h3>Empleados</h3></div>
      <div><form action="%s" method="get">
        <input type="text" name="uid" placeholder="UID nuevo/editar"/><button type="submit">Abrir</button>
      </form></div></div>
      <table><tr><th>UID</th><th>Nombre</th><th>EPP</th><th>Flags</th><th>Rewrite</th><th>Actualizado</th></tr>
    """ % (url_for("edit_employee"))
    for e in rows:
        epps = []
        if e["casco"]: epps.append("casco")
        if e["lentes"]: epps.append("lentes")
        if e["guantes"]: epps.append("guantes")
        flags = []
        if e["epp_completo"]: flags.append("EPP completo")
        if e["bloqueado"]: flags.append("Bloqueado")
        rw = "pendiente" if e["force_rewrite"] else "-"
        body += f"<tr><td><a href='{url_for('edit_employee', uid=e['uid'])}'>{e['uid']}</a></td><td>{e['nombre'] or ''}</td><td>{', '.join(epps) if epps else '-'}</td><td>{', '.join(flags) if flags else '-'}</td><td>{rw}</td><td>{e['updated_at'] or ''}</td></tr>"
    body += "</table></div>"
    return render(body, title="Hub Fichador – Empleados")

@app.get("/empleados/editar")
def edit_employee():
    uid = (request.args.get("uid") or "").strip()
    if not uid:
        flash(("err","Falta UID"))
        return redirect(url_for("employees"))
    with db() as con:
        e = con.execute("SELECT * FROM employees WHERE uid=?", (uid,)).fetchone()
    nombre = e["nombre"] if e else ""
    flags = {
        "casco": bool(e["casco"]) if e else False,
        "lentes": bool(e["lentes"]) if e else False,
        "guantes": bool(e["guantes"]) if e else False,
        "eppc": bool(e["epp_completo"]) if e else False,
        "bloq": bool(e["bloqueado"]) if e else False,
        "force": bool(e["force_rewrite"]) if e else False
    }

    # ---- Sueño: últimos 7 días (hasta AYER) ----
    try:
        series = fetch_sleep_last_days(days=7, include_today=False)
        rows = ""
        order = ["REM", "profundo", "ligero", "despierto", "siesta/otro"]
        for day, agg in series.items():
            per = agg["per_stage"]
            chips = []
            for k in order:
                if k in per: chips.append(f"<span>{k}: {per[k]} min</span>")
            for k, v in sorted(per.items()):
                if k not in order: chips.append(f"<span>{k}: {v} min</span>")
            chips_html = " ".join(chips) if chips else "<span>-</span>"
            rows += f"<tr><td>{day}</td><td>{agg['total_min']}</td><td class='kv'>{chips_html}</td></tr>"
        if not rows:
            rows = "<tr><td colspan='3'>Sin datos en el período.</td></tr>"
        sleep_html = f"""
        <div class="card">
          <h3>Sueño – Últimos 7 días</h3>
          <table>
            <tr><th>Fecha</th><th>Total (min)</th><th>Etapas (min)</th></tr>
            {rows}
          </table>
        </div>
        """
    except Exception as ex:
        sleep_html = f"<div class='card'><h3>Sueño – Últimos 7 días</h3><p class='err'>No se pudo obtener: {ex}</p></div>"

    # ---- Form empleado ----
    body = """
    <div class="card"><h3>Editar/crear empleado</h3>
    <form action="%s" method="post">
      <input type="hidden" name="uid" value="%s"/>
      <p><b>UID:</b> %s %s</p>
      <label>Nombre</label><input type="text" name="nombre" value="%s"/>
      <p>EPP requerido:</p>
      <label class="chk"><input type="checkbox" name="casco" %s> Casco</label>
      <label class="chk"><input type="checkbox" name="lentes" %s> Lentes</label>
      <label class="chk"><input type="checkbox" name="guantes" %s> Guantes</label>
      <p>Flags:</p>
      <label class="chk"><input type="checkbox" name="epp_completo" %s> EPP Completo</label>
      <label class="chk"><input type="checkbox" name="bloqueado" %s> Personal Bloqueado</label>
      <p>Reescritura:</p>
      <label class="chk"><input type="checkbox" name="force_rewrite" %s> Forzar reescritura al próximo apoyo</label>
      <div style="margin-top:12px"><button type="submit">Guardar</button> <a href="%s" style="margin-left:8px">Volver</a></div>
    </form></div>
    """ % (
        url_for("save_employee"), uid,
        uid, (f"<span class='badge'>rewrite pendiente</span>" if flags["force"] else ""),
        nombre or "",
        "checked" if flags["casco"] else "",
        "checked" if flags["lentes"] else "",
        "checked" if flags["guantes"] else "",
        "checked" if flags["eppc"] else "",
        "checked" if flags["bloq"] else "",
        "checked" if flags["force"] else "",
        url_for("employees")
    )

    body += sleep_html
    return render(body, title=f"Empleado {uid}")

@app.post("/empleados/guardar")
def save_employee():
    uid = (request.form.get("uid") or "").strip()
    nombre = (request.form.get("nombre") or "").strip()
    casco = 1 if request.form.get("casco") else 0
    lentes = 1 if request.form.get("lentes") else 0
    guantes = 1 if request.form.get("guantes") else 0
    eppc = 1 if request.form.get("epp_completo") else 0
    bloq  = 1 if request.form.get("bloqueado") else 0
    force = 1 if request.form.get("force_rewrite") else 0
    if not uid:
        flash(("err","UID requerido"))
        return redirect(url_for("employees"))
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with db() as con:
        con.execute("""INSERT INTO employees(uid,nombre,casco,lentes,guantes,epp_completo,bloqueado,force_rewrite,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(uid) DO UPDATE SET
                         nombre=excluded.nombre, casco=excluded.casco, lentes=excluded.lentes,
                         guantes=excluded.guantes, epp_completo=excluded.epp_completo,
                         bloqueado=excluded.bloqueado, force_rewrite=excluded.force_rewrite,
                         updated_at=excluded.updated_at
                    """, (uid,nombre,casco,lentes,guantes,eppc,bloq,force,now))
    flash(("ok","Empleado guardado"))
    return redirect(url_for("edit_employee", uid=uid))

# ===== APIs para Raspberry =====
@app.post("/should_rewrite")
def should_rewrite():
    uid = (request.form.get("uid") or (request.json.get("uid") if request.is_json else "") or "").strip()
    if not uid:
        return jsonify({"ok": False, "error": "uid missing"}), 400
    with db() as con:
        e = con.execute("SELECT * FROM employees WHERE uid=?", (uid,)).fetchone()
    if not e:
        return jsonify({"ok": True, "rewrite": False})
    if e["force_rewrite"]:
        return jsonify({"ok": True, "rewrite": True, "desired_payload": desired_payload_from_employee(e)})
    return jsonify({"ok": True, "rewrite": False})

@app.post("/rewrite_done")
def rewrite_done():
    uid = (request.form.get("uid") or (request.json.get("uid") if request.is_json else "") or "").strip()
    if not uid:
        return jsonify({"ok": False, "error": "uid missing"}), 400
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with db() as con:
        con.execute("UPDATE employees SET force_rewrite=0, updated_at=? WHERE uid=?", (now, uid))
    return jsonify({"ok": True})

@app.post("/ingreso")
def ingreso():
    f = request.files.get("image")
    if not f:
        return jsonify({"ok": False, "error": "image missing"}), 400
    uid = (request.form.get("uid") or "").strip()
    nombre_tag = (request.form.get("nombre_tag") or "").strip()
    try:
        epp_tag = json.loads(request.form.get("epp_tag") or "[]")
        if not isinstance(epp_tag, list): epp_tag = []
    except Exception:
        epp_tag = []
    api_result = request.form.get("api_result") or ""
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    safe_uid = uid or "nouid"
    fname = f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_uid}.jpg"
    f.save(IMG_DIR / fname)
    with db() as con:
        con.execute("""INSERT INTO records(ts,uid,nombre_tag,epp_tag_json,api_result_json,image_file)
                       VALUES(?,?,?,?,?,?)""",
                    (ts, uid, nombre_tag, json.dumps(epp_tag,ensure_ascii=False), api_result, fname))
    return jsonify({"ok": True, "saved_image": fname})

# ===== Main =====
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8090, debug=False)
