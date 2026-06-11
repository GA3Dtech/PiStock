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

"""Projects overview page ("/") — the landing page.

One row per project: code + description on the left, and on the right a
single horizontally-scrollable strip of every part thumbnail of that
project. The goal is a purely visual, at-a-glance map of all projects
and what they contain. The detailed per-part view (status, stock,
revisions) lives in the catalog ("/catalog"); clicking a project here
opens the catalog already filtered on it, and clicking a thumbnail opens
the 3D viewer of that part.
"""
from nicegui import ui
from i18n import _
from app_core import (_apply_user_lang, _register_pwa)
from components.header import render_app_header
from components.admin import (_admin_configured, _open_admin_setup_dialog)
from db import fetch_projects, fetch_parts_full


# ======================================================================
#  PAGE : PROJECTS OVERVIEW  (landing page "/")
# ======================================================================
@ui.page("/")
def projects_overview_page():
    """Landing page: one visual row per project with all its part
    thumbnails on a horizontally-scrollable strip."""
    # Apply the chosen language BEFORE building anything (the first
    # _() calls depend on it).
    _apply_user_lang()
    _register_pwa()
    # First startup: no admin password yet -> setup dialog. Since this
    # is now the landing page, the very first visit triggers it here.
    if not _admin_configured():
        _open_admin_setup_dialog()
    ui.page_title(_("PiStock — Projects"))

    render_app_header("PiStock — Projects")

    with ui.column().classes("w-full max-w-7xl mx-auto p-4 gap-4"):

        # --- Action bar: title on the left, navigation on the right ---
        with ui.row().classes("w-full items-center gap-2"):
            ui.label(_("Projects overview")) \
                .classes("text-lg font-medium text-stone-700")
            ui.element("div").classes("flex-grow")  # spacer
            ui.button(_("Catalog"),
                      on_click=lambda: ui.navigate.to("/catalog")) \
                .props("color=primary").classes("text-base")
            ui.button(_("Plugins"),
                      on_click=lambda: ui.navigate.to("/plugins")) \
                .props("color=primary outline").classes("text-base")

        # --- Project rows ---------------------------------------------
        projects = fetch_projects()
        if not projects:
            ui.label(_("No project yet. Create one from the catalog.")) \
                .classes("text-gray-500 text-center p-8")
            return

        for proj in projects:
            render_project_row(proj)


# ======================================================================
#  RENDERING A PROJECT ROW
# ======================================================================
def render_project_row(proj: dict):
    """One project row: code + description (clickable -> filtered
    catalog) on the left, and the horizontally-scrollable strip of part
    thumbnails on the right."""
    code = proj["code"]

    with ui.card().classes("w-full p-3"):
        with ui.row().classes("w-full items-center gap-4 no-wrap"):

            # --- Left: code + description (-> catalog filtered) -------
            # Fixed width so every project header lines up vertically;
            # flex-shrink-0 keeps it from collapsing when the thumbnail
            # strip is long.
            with ui.column().classes(
                    "gap-1 flex-shrink-0 w-48 cursor-pointer "
                    "hover:opacity-80 transition") as head:
                ui.label(code).classes(
                    "text-lg font-mono font-bold text-blue-700 "
                    "bg-blue-50 px-2 py-0.5 rounded self-start")
                desc = proj["description"] or _("(no description)")
                # Clamp the description to 2 lines (raw CSS: line-clamp
                # is not guaranteed in the Tailwind build).
                ui.label(desc) \
                    .classes("text-sm text-stone-700") \
                    .style("display:-webkit-box;-webkit-line-clamp:2;"
                           "-webkit-box-orient:vertical;overflow:hidden;")
            head.on("click",
                    lambda c=code: ui.navigate.to(f"/catalog?project={c}"))
            head.tooltip(_("Open this project in the catalog"))

            # --- Right: scrollable thumbnail strip --------------------
            parts = fetch_parts_full(project_code=code)
            if not parts:
                ui.label(_("No part in this project yet.")) \
                    .classes("text-sm text-gray-400 italic flex-grow")
                return
            # 'min-w-0' is required for overflow-x-auto to actually clip
            # (and thus scroll) a flex child instead of stretching the
            # parent. 'no-wrap' keeps the thumbnails on a single line.
            with ui.row().classes(
                    "flex-grow min-w-0 items-center gap-2 no-wrap "
                    "overflow-x-auto py-1"):
                for part in parts:
                    render_thumbnail(part)


def render_thumbnail(part: dict):
    """A single clickable part thumbnail in the strip.

    - Native part: click -> the 3D viewer / detail page of the part.
    - Ghost part (referenced from another project): a violet ring marks
      it as external, and clicking jumps to its main project instead.

    Falls back to the (truncated) part name when no thumbnail image is
    available."""
    is_ghost = part.get("is_ghost")
    origin = part.get("origin_project_code")

    cls = ("w-20 h-20 bg-stone-100 rounded-lg flex items-center "
           "justify-center overflow-hidden flex-shrink-0 cursor-pointer "
           "hover:scale-105 transition")
    if is_ghost:
        cls += " ring-2 ring-violet-400"
    with ui.element("div").classes(cls) as box:
        if part["thumbnail_url"]:
            ui.image(part["thumbnail_url"]) \
                .classes("w-full h-full object-contain")
        else:
            ui.label(part["part_name"][:10]) \
                .classes("text-[10px] text-gray-400 text-center px-1 "
                         "break-all")
    if is_ghost:
        # Ghost: jump to the part's own project (if any), like the
        # catalog. Without a main project, fall back to the part page.
        if origin:
            box.on("click", lambda c=origin: ui.navigate.to(f"/catalog?project={c}"))
        else:
            box.on("click", lambda pid=part["id"]: ui.navigate.to(f"/part/{pid}"))
        box.tooltip("👻 " + _("from {code}").format(code=origin or "—"))
    else:
        box.on("click", lambda pid=part["id"]: ui.navigate.to(f"/part/{pid}"))
        box.tooltip(part["part_name"])
