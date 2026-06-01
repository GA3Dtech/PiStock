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
Point d'entree FastAPI de PiStock.

Ce fichier est volontairement MINCE : il se contente d'assembler
l'application a partir des modules de domaine.

  - Le schema de la base vit dans model.py (source unique de verite,
    partagee avec init_db.py).
  - Les chemins, le moteur SQL et le logger vivent dans config.py.
  - La logique metier et les endpoints REST sont decoupes par domaine
    dans services/*.py (admin, projects, boms, parts, stock), chacun
    exposant un APIRouter.
  - L'interface NiceGUI est definie dans frontend/ui.py et s'attache au
    MEME FastAPI 'app'.

FACADE DE COMPATIBILITE : on re-exporte ci-dessous les modeles et les
helpers publics (`main.Parts`, `main.engine`, `main._flatten_bom`...) car
l'UI, les plugins (cf. plugins/bom_tree) et la suite de tests y accedent
via `import main`. Conserver ces noms evite de casser ces consommateurs.

    uvicorn main:app --reload --host 0.0.0.0 --port 8000
"""
import os
import sys

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlmodel import SQLModel

# --- Infrastructure partagee (chemins, engine, logger, util fichiers) ---
from config import (  # noqa: F401  (re-export pour main.X)
    engine, logger, BASE_DIR, DATA_DIR, CAD_DIR, IMG_DIR, DB_PATH,
    _delete_file_if_exists,
)

# --- Modeles (source unique : model.py), re-exportes pour main.X ---
from model import (  # noqa: F401
    Parts, PLM, Stock, Project, Bom, BomLine, Admin,
)

# --- Helpers metier re-exportes (facade UI / plugins / tests) ---
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

# --- Routers de domaine ---
from services import admin, projects, boms, stock, parts


app = FastAPI(title="PiStock PLM Receiver")

# L'ordre d'inclusion n'affecte pas le routage : aucune route statique
# n'est masquee par une route a parametre entre domaines (et au sein de
# parts.py, '/parts/full' est bien declaree avant '/parts/{part_id}').
for _module in (admin, projects, boms, stock, parts):
    app.include_router(_module.router)


@app.on_event("startup")
def on_startup():
    SQLModel.metadata.create_all(engine)
    logger.info("Base de donnees initialisee.")


# ----------------------------------------------------------------------
#  FICHIERS STATIQUES + INTERFACE NiceGUI
# ----------------------------------------------------------------------
# 1. Les fichiers uploadés (vignettes .png, modèles .glb...) sont servis
#    sous /uploads/. C'est utilisé à la fois par l'interface NiceGUI
#    (pour afficher les images) et par le viewer 3D (qui charge le .glb
#    via une URL HTTP, pas un chemin disque).
uploads_root = os.path.join(DATA_DIR, "uploads")
app.mount("/uploads", StaticFiles(directory=uploads_root), name="uploads")

# 2. Assets statiques du frontend (model-viewer.min.js, etc.)
#    Permet de servir des libs JS en local plutot que via un CDN
#    -> autonomie complete sans internet, et meilleur controle.
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

# 3. L'interface NiceGUI est définie dans frontend/ui.py et s'attache
#    au MEME FastAPI 'app'. Donc tout tourne sur le meme port :
#    - http://127.0.0.1:8000/       -> dashboard NiceGUI
#    - http://127.0.0.1:8000/api/v1 -> endpoints REST (utilises par la macro)
#    - http://127.0.0.1:8000/uploads/... -> fichiers statiques
FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, "../../frontend"))
if FRONTEND_DIR not in sys.path:
    sys.path.insert(0, FRONTEND_DIR)

try:
    # ui_module enregistre ses pages sur 'app' via @ui.page(...) et
    # appelle ui.run_with(app) pour brancher NiceGUI sur FastAPI.
    import ui as ui_module  # noqa: F401  (l'import suffit a tout enregistrer)
    logger.info("Interface NiceGUI chargee.")
except ImportError as e:
    logger.warning(f"Impossible de charger l'UI NiceGUI : {e}")
