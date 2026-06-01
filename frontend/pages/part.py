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

"""Page detail d'une piece (/part/{id}) : viewer 3D, liste des
revisions PLM et leurs dialogues (suppression, set-main).
"""
import json
from nicegui import ui, app, events
from sqlmodel import Session, select
from i18n import _, set_lang, get_lang, AVAILABLE_LANGS
from app_core import (_apply_user_lang, _register_pwa)
from components.header import render_app_header
from components.admin import _ensure_admin
from db import (fetch_part_detail, fetch_revisions, delete_revision_db, set_revision_main_db)


@ui.page("/part/{part_id}")
def part_page(part_id: int):
    """Page viewer 3D pour une piece donnee, avec liste des
    revisions PLM sous le viewer."""
    _apply_user_lang()
    _register_pwa()
    part = fetch_part_detail(part_id)
    # Titre d'onglet : "PiStock — Vue 3D : <nom de la piece>"
    part_name = part["part_name"] if part else f"#{part_id}"
    ui.page_title(f"{_('PiStock — 3D View')} : {part_name}")

    # Charger model-viewer (web component de Google, Apache 2.0).
    # On charge en LOCAL depuis /static/model-viewer.min.js, servi
    # par le mount FastAPI sur frontend/static/. Cela rend l'app
    # 100% autonome (pas de dependance CDN, fonctionne offline).
    # Si le fichier local est absent, on tombe sur le CDN via un
    # petit script de fallback.
    ui.add_head_html('''
        <script type="module" src="/static/model-viewer.min.js"
                onerror="this.onerror=null;
                         const s=document.createElement('script');
                         s.type='module';
                         s.src='https://unpkg.com/@google/model-viewer/dist/model-viewer.min.js';
                         document.head.appendChild(s);
                         console.warn('model-viewer local manquant, fallback CDN');">
        </script>
    ''')

    render_app_header("PiStock — 3D View", show_home=True)

    with ui.column().classes("w-full max-w-5xl mx-auto p-4 gap-4"):

        # Barre du haut : bouton retour + titre
        with ui.row().classes("items-center gap-3 w-full"):
            ui.button("← Retour à la liste",
                      on_click=lambda: ui.navigate.to("/")) \
                .props("flat color=primary").classes("text-sm")
            if part:
                ui.label(part["part_name"]).classes("text-xl font-medium")
            else:
                ui.label("Pièce introuvable").classes("text-xl text-red-600")

        if part is None:
            ui.label(f"Aucune pièce avec l'id {part_id}.") \
                .classes("text-red-600 p-4")
            return

        if not part["glb_url"]:
            ui.label("Cette pièce n'a pas de modèle 3D associé.") \
                .classes("text-gray-500 p-4 bg-white rounded-lg shadow")
            return

        # --- Viewer 3D (model-viewer) ---------------------------------
        # On utilise ui.element() plutot que ui.html() : NiceGUI 3.x
        # sanitise ui.html() et Vue.js filtre les custom elements
        # qu'il ne connait pas — du coup <model-viewer> dans un
        # ui.html() etait silencieusement supprime. Avec ui.element,
        # NiceGUI sait qu'on veut un noeud brut avec ce nom de tag.
        # On lui attribue un id DOM stable pour pouvoir le cibler en
        # JavaScript lors d'un changement de revision.
        with ui.card().classes("w-full p-0 overflow-hidden"):
            viewer = ui.element("model-viewer")
            viewer.props(
                f'id="pistock-viewer" '
                f'src="{part["glb_url"]}" '
                f'alt="Modèle 3D de {part["part_name"]}" '
                f'camera-controls '
                f'touch-action="pan-y" '
                f'shadow-intensity="1" '
                f'exposure="1" '
                f'auto-rotate '
                f'auto-rotate-delay="3000"'
            )
            viewer.style("width: 100%; height: 600px; display: block; "
                         "background: linear-gradient(135deg, "
                         "#f5f5f7 0%, #e8e8eb 100%);")

        # --- Bloc info revision affichee ------------------------------
        info_card = ui.card().classes("w-full p-3")
        with info_card:
            info_label = ui.label() \
                .classes("text-sm text-gray-600")
        # Mise a jour initiale
        author = part.get("last_author") or "—"
        ts = part.get("last_timestamp") or "—"
        info_label.text = f"Révision affichée — par {author} le {ts}"

        # --- Liste des revisions PLM ----------------------------------
        ui.label("Historique des révisions").classes("text-base font-medium mt-2")
        revisions_container = ui.column().classes("w-full gap-2")

        def change_displayed_revision(glb_url: str, author: str, ts: str,
                                       version: str):
            """Change le modele affiche dans le viewer + met a jour
            l'info en dessous.

            On utilise document.getElementById + .src direct plutot que
            viewer.props() : Vue.js ne synchronise pas correctement les
            attributs d'un custom element inconnu, donc .props() ne se
            propageait pas jusqu'au DOM dans certains cas. La voie
            directe via JavaScript est garantie de marcher."""
            js = (f'const v = document.getElementById("pistock-viewer"); '
                  f'if (v) {{ v.setAttribute("src", {json.dumps(glb_url)}); }}')
            ui.run_javascript(js)
            info_label.text = (f"Révision {version} — par {author} le {ts}")

        def refresh_revisions():
            """Recharge la liste des revisions depuis la base."""
            revisions_container.clear()
            revisions = fetch_revisions(part_id)
            if not revisions:
                with revisions_container:
                    ui.label("Aucune révision pour le moment.") \
                        .classes("text-gray-500 text-sm p-2")
                return
            for r in revisions:
                with revisions_container:
                    render_revision_row(r, refresh_revisions,
                                         change_displayed_revision)

        refresh_revisions()

# --- Rendu d'une ligne de revision (helper) ---------------------------
def render_revision_row(rev: dict, on_change, on_view):
    """Une ligne dans la liste des revisions.
    'on_change' : appele apres set-main / delete pour rafraichir.
    'on_view'(glb_url, author, ts, version) : appele au clic ligne."""
    is_current = rev["is_current"]
    is_main_flag = rev["is_main"]

    # Bordure speciale pour mettre en evidence celle affichee
    extra = " border-2 border-blue-500" if is_current else ""

    with ui.card().classes(f"w-full p-3 cursor-pointer hover:bg-blue-50 "
                            f"transition" + extra) as card:
        with ui.row().classes("items-center gap-3 no-wrap w-full"):
            # Pastille version
            ui.label(rev["version"]) \
                .classes("text-sm font-mono font-bold "
                          "text-blue-700 bg-blue-50 "
                          "px-2 py-1 rounded flex-shrink-0")

            # Vignette
            if rev["thumbnail_url"]:
                ui.image(rev["thumbnail_url"]) \
                    .classes("w-12 h-12 object-contain bg-stone-50 "
                              "rounded flex-shrink-0")

            # Infos
            with ui.column().classes("gap-0 flex-grow"):
                # Author + ts
                ui.label(f"{rev['author'] or '—'}") \
                    .classes("text-sm font-medium")
                ui.label(rev["timestamp"][:19].replace("T", " ")) \
                    .classes("text-xs text-gray-500")

            # Badges "principale" / "courante"
            if is_main_flag:
                ui.label("★ principale") \
                    .classes("text-xs text-amber-700 bg-amber-100 "
                              "px-2 py-0.5 rounded font-medium")
            elif is_current:
                ui.label("affichée") \
                    .classes("text-xs text-blue-700 bg-blue-100 "
                              "px-2 py-0.5 rounded")

            # Bouton "définir principale"
            # Pas affiche si c'est deja la principale (rien a faire)
            def make_set_main(plm_id=rev["id"]):
                def handler():
                    ok, msg = set_revision_main_db(plm_id)
                    ui.notify(msg, type="positive" if ok else "negative")
                    if ok:
                        on_change()
                return handler

            star_btn = ui.button(icon="star", on_click=make_set_main()) \
                .props("flat round dense color=amber") \
                .tooltip("Définir comme principale")
            star_btn.classes("flex-shrink-0")
            if is_main_flag:
                star_btn.set_visibility(False)

            # Bouton suppression (avec confirmation)
            def make_delete(plm_id=rev["id"], version=rev["version"]):
                def handler():
                    confirm_delete_revision(plm_id, version, on_change)
                return handler
            ui.button(icon="delete", on_click=make_delete()) \
                .props("flat round dense color=negative") \
                .classes("flex-shrink-0") \
                .tooltip("Supprimer cette révision")

    # Clic sur le corps de la carte (en evitant les boutons) =
    # afficher cette revision dans le viewer.
    def on_card_click(_, r=rev):
        if r["glb_url"]:
            on_view(r["glb_url"], r["author"] or "—",
                     r["timestamp"], r["version"])
    card.on("click", on_card_click)


def confirm_delete_revision(plm_id: int, version: str, on_change):
    return _ensure_admin(lambda: _confirm_delete_revision_inner(plm_id, version, on_change))

def _confirm_delete_revision_inner(plm_id: int, version: str, on_change):
    """Petit dialogue de confirmation avant suppression destructive."""
    with ui.dialog() as dialog, ui.card():
        ui.label(f"Supprimer la révision « {version} » ?") \
            .classes("text-base font-medium")
        ui.label("Cette action est irréversible : les fichiers .FCStd, "
                  ".glb et .png seront effacés du disque.") \
            .classes("text-sm text-gray-600 max-w-[400px]")
        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Annuler", on_click=dialog.close).props("flat")
            def confirm():
                ok, msg = delete_revision_db(plm_id)
                ui.notify(msg, type="positive" if ok else "negative")
                dialog.close()
                if ok:
                    on_change()
            ui.button("Supprimer", on_click=confirm) \
                .props("color=negative")
    dialog.open()


# ======================================================================
#  DIALOGUE : ASSIGNER UN PROJET A UNE PIECE
# ======================================================================
# Fonction globale appelee depuis render_part_row. Construit un
# dialogue a la volee (un nouveau a chaque clic) qui liste les
# projets, met en evidence le projet actuel et le "dernier utilise",
# et permet aussi de creer un projet a la volee.
# ======================================================================
#  DIALOGUE : OPTIONS D'UNE PIECE (point d'entree pour suppression etc.)
# ======================================================================
