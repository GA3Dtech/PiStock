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

"""Chargement dynamique des plugins (scan de plugins/) et page index
(/plugins) affichant la grille des plugins charges.
"""
import json
import importlib.util as _importlib_util
from pathlib import Path
from nicegui import ui, app
from i18n import _, set_lang, get_lang, AVAILABLE_LANGS
from app_core import (_apply_user_lang, _register_pwa)
from components.header import render_app_header


# Dossier 'plugins/' a la racine du projet (au meme niveau que
# frontend/ et backend/). Resolu depuis ce fichier pour etre agnostique
# du cwd. Ce module vit dans frontend/pages/, d'ou les trois '.parent'
# (pages -> frontend -> racine du depot).
PLUGINS_DIR = Path(__file__).resolve().parent.parent.parent / "plugins"

# Liste globale des manifests des plugins charges avec succes. Utilisee
# par la page /plugins pour afficher la grille de cartes.
PLUGINS_LIST: list[dict] = []


def _load_plugins(fastapi_app):
    """Scanne PLUGINS_DIR et charge chaque plugin valide. Erreurs
    individuelles loggees mais non bloquantes (un plugin foireux ne
    doit pas empecher le reste du systeme de demarrer)."""
    global PLUGINS_LIST
    PLUGINS_LIST = []
    if not PLUGINS_DIR.is_dir():
        print(f"ℹ️  Pas de dossier plugins/ a {PLUGINS_DIR}, aucun "
              f"plugin charge.")
        return
    for plugin_dir in sorted(PLUGINS_DIR.iterdir()):
        # On ignore les fichiers, les dossiers caches (_*, .*), et
        # les __pycache__ Python.
        if not plugin_dir.is_dir():
            continue
        if plugin_dir.name.startswith(("_", ".")):
            continue
        manifest_path = plugin_dir / "manifest.json"
        plugin_py = plugin_dir / "plugin.py"
        if not manifest_path.is_file() or not plugin_py.is_file():
            print(f"⚠️  {plugin_dir.name} : manifest.json ou plugin.py "
                  f"manquant, plugin ignore.")
            continue
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            # Validation minimale : id, name, version obligatoires
            for key in ("id", "name", "version"):
                if not manifest.get(key):
                    raise ValueError(f"champ '{key}' manquant dans manifest")
            # Charge plugin.py via un nom unique pour eviter les
            # collisions avec d'eventuels autres modules.
            mod_name = f"pistock_plugin_{manifest['id']}"
            spec = _importlib_util.spec_from_file_location(
                mod_name, plugin_py)
            module = _importlib_util.module_from_spec(spec)
            spec.loader.exec_module(module)
            # Le plugin doit exposer register(app). C'est la qu'il
            # enregistre ses routes et pages.
            if not hasattr(module, "register"):
                raise ValueError("plugin.py doit definir register(app)")
            module.register(fastapi_app)
            PLUGINS_LIST.append(manifest)
            print(f"✔️  Plugin charge : {manifest['name']} "
                  f"(v{manifest['version']}) [{manifest['id']}]")
        except Exception as e:
            print(f"⚠️  Plugin '{plugin_dir.name}' non charge : {e}")
            import traceback
            traceback.print_exc()


@ui.page("/plugins")
def plugins_index_page():
    """Page d'index des plugins : une grille de cartes cliquables.
    Chaque carte renvoie vers /plugin/<id>. Si aucun plugin n'est
    installe, on affiche un message d'aide."""
    _apply_user_lang()
    _register_pwa()
    ui.page_title("PiStock — Plugins")
    render_app_header("Plugins", show_home=True)

    with ui.column().classes("max-w-5xl mx-auto p-4 w-full gap-4"):
        if not PLUGINS_LIST:
            with ui.card().classes("w-full p-8 text-center"):
                ui.label("🧩").classes("text-5xl mb-2")
                ui.label("Aucun plugin installé") \
                    .classes("text-lg font-medium")
                ui.label("Glissez un plugin dans le dossier 'plugins/' "
                         "à la racine du projet, puis redémarrez le "
                         "serveur.").classes("text-sm text-gray-500 max-w-md mx-auto")
            return

        ui.label(f"{len(PLUGINS_LIST)} plugin(s) installé(s)") \
            .classes("text-sm text-gray-500")

        with ui.row().classes("gap-4 flex-wrap justify-start"):
            for plugin in PLUGINS_LIST:
                # Card cliquable : navigate vers la page du plugin
                pid = plugin["id"]
                def make_navigator(target=pid):
                    return lambda: ui.navigate.to(f"/plugin/{target}")
                with ui.card().classes(
                        "w-56 p-4 cursor-pointer hover:shadow-lg "
                        "transition") \
                        .on("click", make_navigator()):
                    ui.label(plugin.get("icon", "🧩")) \
                        .classes("text-5xl text-center w-full")
                    ui.label(plugin["name"]) \
                        .classes("text-base font-bold text-center w-full mt-2")
                    desc = plugin.get("description", "")
                    if desc:
                        ui.label(desc) \
                            .classes("text-xs text-gray-600 text-center")
                    with ui.row().classes(
                            "w-full justify-between mt-2 text-xs "
                            "text-gray-400"):
                        ui.label(f"v{plugin['version']}")
                        if plugin.get("author"):
                            ui.label(f"par {plugin['author']}")
