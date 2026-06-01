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
Point d'entree de l'interface NiceGUI de PiStock.

Ce fichier est volontairement MINCE. L'UI a ete decoupee en :
  - app_core.py          -> helpers transverses (langue, PWA, lien source)
  - components/header.py -> en-tete commun
  - components/admin.py  -> session + dialogues admin
  - db.py                -> couche d'acces a la base de donnees
  - pages/dashboard.py   -> page catalogue "/"
  - pages/part.py        -> page detail/viewer "/part/{id}"
  - pages/plugins.py     -> chargement des plugins + page "/plugins"

S'attache au MEME FastAPI 'app' que les endpoints REST (defini dans
backend/app/main.py) : les pages @ui.page sont servies a la racine du
meme serveur. L'acces a la base se fait directement via les modeles
SQLModel (pas de HTTP interne) — cf. db.py.

Pages :
  /              -> catalogue (liste des pieces)
  /part/{id}     -> viewer 3D d'une piece
  /plugins       -> index des plugins
"""
from nicegui import ui

# Importer les modules de pages suffit a enregistrer leurs @ui.page
# aupres de NiceGUI. L'ordre n'a pas d'importance.
import pages.dashboard  # noqa: F401  (enregistre "/")
import pages.part       # noqa: F401  (enregistre "/part/{id}")
import pages.plugins    # noqa: F401  (enregistre "/plugins")
from pages.plugins import _load_plugins


# ======================================================================
#  DEMARRAGE
# ======================================================================
# Branche NiceGUI sur le FastAPI 'app' defini dans main.py. Nos pages
# @ui.page sont alors accessibles a la racine du meme serveur.
# 'storage_secret' est obligatoire des qu'on utilise ui.storage.user ;
# on le fournit par precaution meme si on ne s'en sert pas ici.
import main as _main_module

# Chargement des plugins AVANT ui.run_with : les @ui.page declarees
# dans les plugins ne sont prises en compte que si elles sont
# enregistrees avant le demarrage du serveur.
_load_plugins(_main_module.app)

ui.run_with(_main_module.app,
            title="PiStock",
            favicon="📦",
            storage_secret="pistock-dev-secret-change-me")
