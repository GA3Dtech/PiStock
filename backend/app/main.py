# PiStock — PLM/inventory tool for FreeCAD-based workshops
# Copyright (C) 2026 GA3Dtech
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
FastAPI entry point for PiStock.

This file is intentionally THIN: it merely assembles the application
from the domain modules.

  - The database schema lives in model.py (single source of truth,
    shared with init_db.py).
  - Paths, the SQL engine and the logger live in config.py.
  - The business logic and REST endpoints are split by domain in
    services/*.py (admin, projects, boms, parts, stock), each one
    exposing an APIRouter.
  - The NiceGUI interface is defined in frontend/ui.py and attaches to
    the SAME FastAPI 'app'.

COMPATIBILITY FACADE: below we re-export the models and the public
helpers (`main.Parts`, `main.engine`, `main._flatten_bom`...) because
the UI, the plugins (see plugins/bom_tree) and the test suite access
them via `import main`. Keeping these names avoids breaking those
consumers.

    uvicorn main:app --reload --host 0.0.0.0 --port 8000
"""
import os
import sys

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlmodel import SQLModel

# --- Shared infrastructure (paths, engine, logger, file utils) ---
from config import (  # noqa: F401  (re-export for main.X)
    engine, logger, BASE_DIR, DATA_DIR, CAD_DIR, IMG_DIR, DB_PATH,
    _delete_file_if_exists,
)

# --- Models (single source: model.py), re-exported for main.X ---
from model import (  # noqa: F401
    Parts, PLM, Stock, Project, Bom, BomLine, Admin, PartRef,
)

# --- Business helpers re-exported (UI / plugins / tests facade) ---
from services.codes import (  # noqa: F401
    PROJECT_CODE_MAX, _code_to_int, _int_to_code, _next_project_code,
    BOM_CODE_MAX, _next_bom_code,
    PLM_VERSION_MAX, _version_to_int, _int_to_version, _next_version_for_part,
    _get_current_plm,
)
from services.admin import (  # noqa: F401
    PBKDF2_ITER, _new_salt, _hash_password, _verify_password,
    _get_admin, _check_admin_password, _require_admin,
)
from services.stock import _get_or_create_stock  # noqa: F401
from services.boms import _flatten_bom, _would_create_cycle  # noqa: F401
from services.parts import VALID_STATUSES, _check_not_locked  # noqa: F401

# --- Whole-UI access gate (separate from the admin password) ---
import access  # noqa: F401

# --- Domain routers ---
from services import admin, projects, boms, stock, parts


app = FastAPI(title="PiStock PLM Receiver")

# The inclusion order does not affect routing: no static route is
# shadowed by a parameterized route across domains (and within
# parts.py, '/parts/full' is indeed declared before '/parts/{part_id}').
for _module in (admin, projects, boms, stock, parts):
    app.include_router(_module.router)


# ----------------------------------------------------------------------
#  ACCESS GATE — password-protect the whole web interface
# ----------------------------------------------------------------------
# Cookie-based gate, deliberately INDEPENDENT of NiceGUI's session
# storage (which is not reliably readable from a Starlette middleware
# when NiceGUI is attached via run_with — reading it there caused a
# redirect loop). A signed cookie (see access.py) proves the visitor
# entered the access password.
#
# The middleware lets through, without a cookie:
#   - /api/v1/*  : the REST API used by the FreeCAD macro (header auth);
#   - /_nicegui* : NiceGUI internal assets/websocket;
#   - /static*, /uploads* : static assets and uploaded files;
#   - /login     : the plain HTML unlock/setup page below.
# Everything else requires the cookie. The front door is separate from
# the admin password, which still guards destructive actions.
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402
from fastapi import Form, Request                          # noqa: E402
from fastapi.responses import HTMLResponse, RedirectResponse  # noqa: E402

_GATE_ALLOW_PREFIXES = ("/api/", "/_nicegui", "/static", "/uploads")
_COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year


class _AccessGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        if path == "/login" or path.startswith(_GATE_ALLOW_PREFIXES):
            return await call_next(request)
        if access.check_token(request.cookies.get(access.COOKIE_NAME)):
            return await call_next(request)
        return RedirectResponse("/login", status_code=302)


app.add_middleware(_AccessGateMiddleware)


# --- /login : plain HTML page (no NiceGUI dependency) -----------------
_LOGIN_LABELS = {
    "en": {"title": "PiStock — Access",
           "login": "Enter the access password.",
           "setup": "Set an access password to protect this instance.",
           "pw": "Access password", "confirm": "Confirm password",
           "btn_login": "Unlock", "btn_create": "Create",
           "bad": "Invalid password.",
           "short": "Password must be at least 6 characters.",
           "mismatch": "The two entries do not match."},
    "fr": {"title": "PiStock — Accès",
           "login": "Saisissez le mot de passe d'accès.",
           "setup": "Définissez un mot de passe d'accès pour protéger cette instance.",
           "pw": "Mot de passe d'accès", "confirm": "Confirmer le mot de passe",
           "btn_login": "Déverrouiller", "btn_create": "Créer",
           "bad": "Mot de passe invalide.",
           "short": "Le mot de passe doit faire au moins 6 caractères.",
           "mismatch": "Les deux saisies ne correspondent pas."},
    "de": {"title": "PiStock — Zugang",
           "login": "Geben Sie das Zugangspasswort ein.",
           "setup": "Legen Sie ein Zugangspasswort fest, um diese Instanz zu schützen.",
           "pw": "Zugangspasswort", "confirm": "Passwort bestätigen",
           "btn_login": "Entsperren", "btn_create": "Erstellen",
           "bad": "Ungültiges Passwort.",
           "short": "Das Passwort muss mindestens 6 Zeichen lang sein.",
           "mismatch": "Die beiden Eingaben stimmen nicht überein."},
}


def _pick_lang(accept_language: str) -> str:
    a = (accept_language or "").lower()
    for code in ("fr", "de"):
        if code in a:
            return code
    return "en"


def _login_html(mode: str, lang: str, error: str = "") -> str:
    t = _LOGIN_LABELS.get(lang, _LOGIN_LABELS["en"])
    intro = t["login"] if mode == "login" else t["setup"]
    btn = t["btn_login"] if mode == "login" else t["btn_create"]
    confirm_field = "" if mode == "login" else (
        f'<input type="password" name="confirm" placeholder="{t["confirm"]}" required>')
    return f"""<!doctype html><html lang="{lang}"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{t['title']}</title><style>
body{{font-family:system-ui,-apple-system,sans-serif;background:#292524;margin:0;
min-height:100vh;display:flex;align-items:center;justify-content:center}}
.card{{background:#fff;padding:28px;border-radius:12px;width:320px;
box-shadow:0 10px 30px rgba(0,0,0,.35)}}
h1{{margin:0 0 4px;font-size:20px}}p{{color:#666;font-size:13px;margin:0 0 14px}}
input{{width:100%;padding:10px;margin:6px 0;border:1px solid #ccc;border-radius:8px;
box-sizing:border-box;font-size:14px}}
button{{width:100%;padding:10px;margin-top:10px;border:0;border-radius:8px;
background:#2563eb;color:#fff;font-size:15px;cursor:pointer}}
.err{{color:#dc2626;font-size:13px;min-height:18px}}</style></head>
<body><form class="card" method="post" action="/login">
<h1>📦 PiStock</h1><p>{intro}</p>
<div class="err">{error}</div>
<input type="password" name="password" placeholder="{t['pw']}" autofocus required>
{confirm_field}
<button type="submit">{btn}</button>
</form></body></html>"""


def _access_cookie_response():
    resp = RedirectResponse("/", status_code=302)
    resp.set_cookie(access.COOKIE_NAME, access.make_token(),
                    httponly=True, samesite="lax", max_age=_COOKIE_MAX_AGE)
    return resp


@app.get("/login", include_in_schema=False)
def login_get(request: Request):
    lang = _pick_lang(request.headers.get("accept-language", ""))
    mode = "login" if access.is_configured() else "setup"
    return HTMLResponse(_login_html(mode, lang))


@app.post("/login", include_in_schema=False)
def login_post(request: Request,
               password: str = Form(...), confirm: str = Form(default="")):
    lang = _pick_lang(request.headers.get("accept-language", ""))
    t = _LOGIN_LABELS.get(lang, _LOGIN_LABELS["en"])
    if access.is_configured():
        if access.verify(password):
            return _access_cookie_response()
        return HTMLResponse(_login_html("login", lang, t["bad"]), status_code=401)
    # First run: create the access password
    if len(password) < access.MIN_LEN:
        return HTMLResponse(_login_html("setup", lang, t["short"]), status_code=400)
    if password != confirm:
        return HTMLResponse(_login_html("setup", lang, t["mismatch"]), status_code=400)
    ok, _msg = access.setup(password)
    if not ok:
        return HTMLResponse(_login_html("setup", lang, t["bad"]), status_code=400)
    return _access_cookie_response()


def _ensure_missing_columns(engine):
    """Additive in-place migration run at startup.

    SQLite's create_all() creates missing TABLES but never alters an
    already-existing one, so a column added to the schema (e.g.
    parts.info) would be invisible on a pre-existing database and every
    SELECT on that table would fail with "no such column". This walks
    the current SQLModel metadata and issues 'ALTER TABLE ADD COLUMN'
    for any column missing from an existing table — mirroring the
    db_admin restore migration, but applied to the live DB so a running
    instance picks up new columns on the next start, with no restore.

    Additive only: never drops or alters existing columns. Returns the
    list of 'table.column' additions applied."""
    applied = []
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        for tname, table in SQLModel.metadata.tables.items():
            cur.execute(f'PRAGMA table_info("{tname}")')
            existing = {r[1] for r in cur.fetchall()}
            if not existing:
                continue  # table absent -> create_all already made it whole
            for col in table.columns:
                if col.name in existing:
                    continue
                typ = col.type.compile(dialect=engine.dialect)
                sql = f'ALTER TABLE "{tname}" ADD COLUMN "{col.name}" {typ}'
                d = col.default
                if d is not None and getattr(d, "is_scalar", False):
                    val = d.arg
                    if isinstance(val, bool):
                        lit = "1" if val else "0"
                    elif isinstance(val, (int, float)):
                        lit = str(val)
                    else:
                        lit = "'" + str(val).replace("'", "''") + "'"
                    sql += f" DEFAULT {lit}"
                cur.execute(sql)
                applied.append(f"{tname}.{col.name}")
        raw.commit()
    finally:
        raw.close()
    return applied


@app.on_event("startup")
def on_startup():
    SQLModel.metadata.create_all(engine)
    applied = _ensure_missing_columns(engine)
    if applied:
        logger.info(f"Schema migre (colonnes ajoutees : {', '.join(applied)}).")
    logger.info("Base de donnees initialisee.")


# ----------------------------------------------------------------------
#  STATIC FILES + NiceGUI INTERFACE
# ----------------------------------------------------------------------
# 1. Uploaded files (.png thumbnails, .glb models...) are served under
#    /uploads/. This is used both by the NiceGUI interface (to display
#    images) and by the 3D viewer (which loads the .glb via an HTTP URL,
#    not a disk path).
uploads_root = os.path.join(DATA_DIR, "uploads")
app.mount("/uploads", StaticFiles(directory=uploads_root), name="uploads")

# 2. Frontend static assets (model-viewer.min.js, etc.)
#    Allows serving JS libs locally rather than via a CDN
#    -> full autonomy without internet, and better control.
FRONTEND_STATIC = os.path.abspath(
    os.path.join(BASE_DIR, "../../frontend/static")
)
if os.path.isdir(FRONTEND_STATIC):
    app.mount("/static", StaticFiles(directory=FRONTEND_STATIC),
              name="frontend_static")
    logger.info(f"Static frontend assets servis depuis {FRONTEND_STATIC}")
else:
    logger.warning(f"Dossier static frontend introuvable : {FRONTEND_STATIC}. "
                    f"Le viewer 3D essaiera de charger depuis CDN.")

# 3. The NiceGUI interface is defined in frontend/ui.py and attaches to
#    the SAME FastAPI 'app'. So everything runs on the same port:
#    - http://127.0.0.1:8000/       -> NiceGUI dashboard
#    - http://127.0.0.1:8000/api/v1 -> REST endpoints (used by the macro)
#    - http://127.0.0.1:8000/uploads/... -> static files
FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, "../../frontend"))
if FRONTEND_DIR not in sys.path:
    sys.path.insert(0, FRONTEND_DIR)

try:
    # ui_module registers its pages on 'app' via @ui.page(...) and
    # calls ui.run_with(app) to wire NiceGUI into FastAPI.
    import ui as ui_module  # noqa: F401  (the import alone registers everything)
    logger.info("Interface NiceGUI chargee.")
except ImportError as e:
    logger.warning(f"Impossible de charger l'UI NiceGUI : {e}")
