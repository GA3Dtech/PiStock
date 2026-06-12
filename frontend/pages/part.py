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

"""Part detail page (/part/{id}): 3D viewer, list of PLM revisions
and their dialogs (deletion, set-main).
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
    """3D viewer page for a given part, with the list of PLM
    revisions below the viewer."""
    _apply_user_lang()
    _register_pwa()
    part = fetch_part_detail(part_id)
    # Tab title: "PiStock — 3D View: <part name>"
    part_name = part["part_name"] if part else f"#{part_id}"
    ui.page_title(f"{_('PiStock — 3D View')} : {part_name}")

    # Load model-viewer (Google web component, Apache 2.0).
    # We load it LOCALLY from /static/model-viewer.min.js, served
    # by the FastAPI mount on frontend/static/. This makes the app
    # 100% self-contained (no CDN dependency, works offline).
    # If the local file is missing, we fall back to the CDN via a
    # small fallback script.
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

        # Top bar: back button + title
        with ui.row().classes("items-center gap-3 w-full"):
            # Browser-style back: returns wherever the user came from
            # (catalog, a filtered project, the projects overview…),
            # instead of always forcing the catalog.
            ui.button(_("← Back"),
                      on_click=lambda: ui.navigate.back()) \
                .props("flat color=primary").classes("text-sm")
            if part:
                ui.label(part["part_name"]).classes("text-xl font-medium")
            else:
                ui.label(_("Part not found")).classes("text-xl text-red-600")

        if part is None:
            ui.label(_("No part with id {part_id}.").format(part_id=part_id)) \
                .classes("text-red-600 p-4")
            return

        if not part["glb_url"]:
            ui.label(_("This part has no associated 3D model.")) \
                .classes("text-gray-500 p-4 bg-white rounded-lg shadow")
            return

        # --- 3D Viewer (model-viewer) ---------------------------------
        # We use ui.element() rather than ui.html(): NiceGUI 3.x
        # sanitizes ui.html() and Vue.js filters out custom elements
        # it does not know — so <model-viewer> inside a ui.html() was
        # being silently removed. With ui.element, NiceGUI knows we
        # want a raw node with this tag name.
        # We give it a stable DOM id so it can be targeted in
        # JavaScript when switching revisions.
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

        # --- Displayed revision info block ----------------------------
        info_card = ui.card().classes("w-full p-3")
        with info_card:
            info_label = ui.label() \
                .classes("text-sm text-gray-600")
        # Initial update
        author = part.get("last_author") or "—"
        ts = part.get("last_timestamp") or "—"
        info_label.text = _("Displayed revision — by {author} on {ts}").format(author=author, ts=ts)

        # --- List of PLM revisions ------------------------------------
        ui.label(_("Revision history")).classes("text-base font-medium mt-2")
        revisions_container = ui.column().classes("w-full gap-2")

        def change_displayed_revision(glb_url: str, author: str, ts: str,
                                       version: str):
            """Change the model displayed in the viewer + update the
            info below it.

            We use document.getElementById + direct .src rather than
            viewer.props(): Vue.js does not correctly synchronize the
            attributes of an unknown custom element, so .props() was not
            propagating to the DOM in some cases. The direct route via
            JavaScript is guaranteed to work."""
            js = (f'const v = document.getElementById("pistock-viewer"); '
                  f'if (v) {{ v.setAttribute("src", {json.dumps(glb_url)}); }}')
            ui.run_javascript(js)
            info_label.text = _("Revision {version} — by {author} on {ts}").format(version=version, author=author, ts=ts)

        def refresh_revisions():
            """Reload the list of revisions from the database."""
            revisions_container.clear()
            revisions = fetch_revisions(part_id)
            if not revisions:
                with revisions_container:
                    ui.label(_("No revision yet.")) \
                        .classes("text-gray-500 text-sm p-2")
                return
            for r in revisions:
                with revisions_container:
                    render_revision_row(r, refresh_revisions,
                                         change_displayed_revision)

        refresh_revisions()

# --- Render a revision row (helper) -----------------------------------
def render_revision_row(rev: dict, on_change, on_view):
    """A row in the list of revisions.
    'on_change': called after set-main / delete to refresh.
    'on_view'(glb_url, author, ts, version): called on row click."""
    is_current = rev["is_current"]
    is_main_flag = rev["is_main"]

    # Special border to highlight the one being displayed
    extra = " border-2 border-blue-500" if is_current else ""

    with ui.card().classes(f"w-full p-3 cursor-pointer hover:bg-blue-50 "
                            f"transition" + extra) as card:
        with ui.row().classes("items-center gap-3 no-wrap w-full"):
            # Version badge
            ui.label(rev["version"]) \
                .classes("text-sm font-mono font-bold "
                          "text-blue-700 bg-blue-50 "
                          "px-2 py-1 rounded flex-shrink-0")

            # Thumbnail
            if rev["thumbnail_url"]:
                ui.image(rev["thumbnail_url"]) \
                    .classes("w-12 h-12 object-contain bg-stone-50 "
                              "rounded flex-shrink-0")

            # Info
            with ui.column().classes("gap-0 flex-grow"):
                # Author + ts
                ui.label(f"{rev['author'] or '—'}") \
                    .classes("text-sm font-medium")
                ui.label(rev["timestamp"][:19].replace("T", " ")) \
                    .classes("text-xs text-gray-500")

            # "main" / "current" badges
            if is_main_flag:
                ui.label(_("★ main")) \
                    .classes("text-xs text-amber-700 bg-amber-100 "
                              "px-2 py-0.5 rounded font-medium")
            elif is_current:
                ui.label(_("displayed")) \
                    .classes("text-xs text-blue-700 bg-blue-100 "
                              "px-2 py-0.5 rounded")

            # "set as main" button
            # Not shown if it is already the main one (nothing to do)
            def make_set_main(plm_id=rev["id"]):
                def handler():
                    ok, msg = set_revision_main_db(plm_id)
                    ui.notify(msg, type="positive" if ok else "negative")
                    if ok:
                        on_change()
                return handler

            star_btn = ui.button(icon="star", on_click=make_set_main()) \
                .props("flat round dense color=amber") \
                .tooltip(_("Set as main"))
            star_btn.classes("flex-shrink-0")
            if is_main_flag:
                star_btn.set_visibility(False)

            # Delete button (with confirmation)
            def make_delete(plm_id=rev["id"], version=rev["version"]):
                def handler():
                    confirm_delete_revision(plm_id, version, on_change)
                return handler
            ui.button(icon="delete", on_click=make_delete()) \
                .props("flat round dense color=negative") \
                .classes("flex-shrink-0") \
                .tooltip(_("Delete this revision"))

    # Click on the body of the card (avoiding the buttons) =
    # display this revision in the viewer.
    def on_card_click(_, r=rev):
        if r["glb_url"]:
            on_view(r["glb_url"], r["author"] or "—",
                     r["timestamp"], r["version"])
    card.on("click", on_card_click)


def confirm_delete_revision(plm_id: int, version: str, on_change):
    return _ensure_admin(lambda: _confirm_delete_revision_inner(plm_id, version, on_change))

def _confirm_delete_revision_inner(plm_id: int, version: str, on_change):
    """Small confirmation dialog before destructive deletion."""
    with ui.dialog() as dialog, ui.card():
        ui.label(_("Delete revision « {version} » ?").format(version=version)) \
            .classes("text-base font-medium")
        ui.label(_("This action is irreversible: the .FCStd, "
                  ".glb and .png files will be erased from disk.")) \
            .classes("text-sm text-gray-600 max-w-[400px]")
        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button(_("Cancel"), on_click=dialog.close).props("flat")
            def confirm():
                ok, msg = delete_revision_db(plm_id)
                ui.notify(msg, type="positive" if ok else "negative")
                dialog.close()
                if ok:
                    on_change()
            ui.button(_("Delete"), on_click=confirm) \
                .props("color=negative")
    dialog.open()


# ======================================================================
#  DIALOG: ASSIGN A PROJECT TO A PART
# ======================================================================
# Global function called from render_part_row. Builds a dialog on the
# fly (a new one on each click) that lists the projects, highlights the
# current project and the "last used" one, and also allows creating a
# project on the fly.
# ======================================================================
#  DIALOG: PART OPTIONS (entry point for deletion, etc.)
# ======================================================================
