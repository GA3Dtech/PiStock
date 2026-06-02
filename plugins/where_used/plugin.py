# PiStock plugin — Where used
# Copyright (C) 2026 GA3Dtech — AGPLv3
#
# Read-only "where-used" view: pick a part (catalog-like list with
# project / name / status filters) and see, in a side panel, every BOM
# that directly uses it, with the quantity per BOM.
#
# Changes NOTHING in the base app: it only READS the database through
# the public `main` facade (main.engine + the models) and ships its own
# translations.

# Sentinel for the "no project" filter (cannot collide with a real
# 3-letter project code).
UNASSIGNED = "__none__"

T = {
    "en": {
        "title": "Where used", "plugins": "Plugins", "catalog": "Catalog",
        "search_name": "Search by name", "status": "Status",
        "all_statuses": "All statuses", "all_projects": "All projects",
        "no_project": "(No project)", "reset": "Reset",
        "count_parts": "{n} part(s)",
        "none_parts": "No part in the database.",
        "none_match": "No matching part.",
        "select_hint": "Select a part to see where it is used.",
        "used_in": "Used in {n} BOM(s)",
        "not_used": "This part is not used in any BOM.",
    },
    "fr": {
        "title": "Utilisée dans", "plugins": "Plugins", "catalog": "Catalogue",
        "search_name": "Rechercher par nom", "status": "Statut",
        "all_statuses": "Tous les statuts", "all_projects": "Tous les projets",
        "no_project": "(Sans projet)", "reset": "Réinitialiser",
        "count_parts": "{n} pièce(s)",
        "none_parts": "Aucune pièce dans la base.",
        "none_match": "Aucune pièce ne correspond.",
        "select_hint": "Sélectionnez une pièce pour voir où elle est utilisée.",
        "used_in": "Utilisée dans {n} BOM(s)",
        "not_used": "Cette pièce n'est utilisée dans aucune BOM.",
    },
    "de": {
        "title": "Verwendet in", "plugins": "Plugins", "catalog": "Katalog",
        "search_name": "Nach Name suchen", "status": "Status",
        "all_statuses": "Alle Status", "all_projects": "Alle Projekte",
        "no_project": "(Kein Projekt)", "reset": "Zurücksetzen",
        "count_parts": "{n} Teil(e)",
        "none_parts": "Keine Teile in der Datenbank.",
        "none_match": "Kein passendes Teil.",
        "select_hint": "Wählen Sie ein Teil, um zu sehen, wo es verwendet wird.",
        "used_in": "In {n} Stückliste(n) verwendet",
        "not_used": "Dieses Teil wird in keiner Stückliste verwendet.",
    },
}


def _tr(key, **kw):
    try:
        from i18n import get_lang
        lang = get_lang()
    except Exception:
        lang = "en"
    text = T.get(lang, T["en"]).get(key, T["en"].get(key, key))
    return text.format(**kw) if kw else text


# ----------------------------------------------------------------------
#  Data access (via the public `main` facade only)
# ----------------------------------------------------------------------
def _fetch_parts():
    """All parts with their project code and status."""
    import main
    from sqlmodel import Session, select
    with Session(main.engine) as s:
        projects = {p.id: p.code for p in s.exec(select(main.Project)).all()}
        parts = s.exec(select(main.Parts).order_by(main.Parts.part_name)).all()
        return [{"id": p.id, "name": p.part_name, "status": p.status,
                 "id_project": p.id_project,
                 "project_code": projects.get(p.id_project)}
                for p in parts]


def _boms_using(part_id):
    """BOMs that DIRECTLY use this part (a BomLine with id_parts), with
    the quantity per BOM. Sorted by BOM code."""
    import main
    from sqlmodel import Session, select
    with Session(main.engine) as s:
        lines = s.exec(
            select(main.BomLine).where(main.BomLine.id_parts == part_id)
        ).all()
        proj = {}
        out = []
        for line in lines:
            bom = s.get(main.Bom, line.id_bom)
            if bom is None:
                continue
            pcode = None
            if bom.id_project is not None:
                if bom.id_project not in proj:
                    p = s.get(main.Project, bom.id_project)
                    proj[bom.id_project] = p.code if p else None
                pcode = proj[bom.id_project]
            out.append({"code": bom.code, "description": bom.description or "",
                        "project_code": pcode, "quantity": line.quantity})
        out.sort(key=lambda x: x["code"])
        return out


_STATUS_COLORS = {
    "Init": "bg-gray-100 text-gray-600",
    "Revue": "bg-amber-100 text-amber-700",
    "Asset": "bg-green-100 text-green-700",
}


def register(app):
    from nicegui import ui

    @ui.page("/plugin/where_used")
    def where_used_page():
        # --- Header ---
        with ui.header().classes("bg-stone-800 text-white shadow"):
            with ui.row().classes("w-full items-center gap-3"):
                ui.label("🔗 " + _tr("title")).classes("text-xl font-medium")
                ui.element("div").classes("flex-grow")
                ui.button("← " + _tr("plugins"),
                          on_click=lambda: ui.navigate.to("/plugins")) \
                    .props("flat color=white").classes("text-sm")
                ui.button("🏠 " + _tr("catalog"),
                          on_click=lambda: ui.navigate.to("/")) \
                    .props("flat color=white").classes("text-sm")

        parts = _fetch_parts()
        state = {"selected": None}

        with ui.column().classes("max-w-6xl mx-auto p-4 w-full gap-3"):
            # --- Filter bar ---
            proj_options = {"": _tr("all_projects")}
            if any(p["id_project"] is None for p in parts):
                proj_options[UNASSIGNED] = _tr("no_project")
            for code in sorted({p["project_code"] for p in parts
                                if p["project_code"]}):
                proj_options[code] = code

            with ui.row().classes("w-full items-end gap-2 flex-wrap"):
                proj_filter = ui.select(options=proj_options, value="") \
                    .props("dense").classes("min-w-[160px]")
                name_in = ui.input(label=_tr("search_name")) \
                    .props("clearable dense").classes("min-w-[200px] flex-grow")
                status_filter = ui.select(
                    options={"": _tr("all_statuses"), "Init": "Init",
                             "Revue": "Revue", "Asset": "Asset"},
                    value="").props("dense").classes("min-w-[140px]")

                def reset_filters():
                    proj_filter.value = ""
                    name_in.value = None
                    status_filter.value = ""
                    refresh_left()
                ui.button(icon="filter_alt_off", on_click=reset_filters) \
                    .props("flat dense").tooltip(_tr("reset"))

            count_label = ui.label("").classes("text-sm text-gray-600")

            # --- Two columns: parts list | BOMs panel ---
            with ui.row().classes("w-full gap-4 items-start"):
                left = ui.column().classes(
                    "flex-1 min-w-[280px] gap-1 max-h-[70vh] overflow-auto")
                right = ui.column().classes(
                    "flex-1 min-w-[280px] gap-2 "
                    "border-l border-gray-200 pl-4")

            # --- Filtering ---
            def filtered():
                code = proj_filter.value or None
                name = (name_in.value or "").strip().lower()
                status = status_filter.value or None
                out = []
                for p in parts:
                    if code == UNASSIGNED and p["id_project"] is not None:
                        continue
                    if code and code != UNASSIGNED and p["project_code"] != code:
                        continue
                    if name and name not in p["name"].lower():
                        continue
                    if status and p["status"] != status:
                        continue
                    out.append(p)
                return out

            def render_right():
                right.clear()
                sel = state["selected"]
                with right:
                    if sel is None:
                        ui.label(_tr("select_hint")) \
                            .classes("text-gray-500 italic p-2")
                        return
                    part = next((p for p in parts if p["id"] == sel), None)
                    if part is None:
                        return
                    boms = _boms_using(sel)
                    ui.label(part["name"]).classes("text-lg font-medium")
                    ui.label(_tr("used_in", n=len(boms))) \
                        .classes("text-sm text-gray-600")
                    if not boms:
                        ui.label(_tr("not_used")) \
                            .classes("text-gray-500 italic p-2")
                        return
                    for b in boms:
                        with ui.card().classes("w-full p-3 gap-0"):
                            with ui.row().classes("items-center gap-2 no-wrap"):
                                ui.label(b["code"]) \
                                    .classes("font-mono font-bold text-blue-700")
                                ui.label(f"×{b['quantity']}") \
                                    .classes("text-sm text-gray-500")
                                ui.element("div").classes("flex-grow")
                                if b["project_code"]:
                                    ui.label(b["project_code"]).classes(
                                        "text-xs bg-stone-100 px-2 py-0.5 "
                                        "rounded font-mono")
                            if b["description"]:
                                ui.label(b["description"]) \
                                    .classes("text-sm text-gray-600 truncate")

            def render_left():
                items = filtered()
                count_label.text = _tr("count_parts", n=len(items))
                left.clear()
                with left:
                    if not parts:
                        ui.label(_tr("none_parts")) \
                            .classes("text-gray-500 text-center p-6")
                        return
                    if not items:
                        ui.label(_tr("none_match")) \
                            .classes("text-gray-500 text-center p-6")
                        return
                    for p in items:
                        selected = (p["id"] == state["selected"])
                        base = ("w-full p-2 rounded cursor-pointer no-wrap "
                                "items-center gap-2 ")
                        base += ("bg-blue-50 ring-1 ring-blue-300"
                                 if selected else "hover:bg-gray-100")
                        with ui.row().classes(base).on(
                                "click", lambda pid=p["id"]: on_select(pid)):
                            ui.label(p["name"]).classes("text-sm flex-grow truncate")
                            if p["project_code"]:
                                ui.label(p["project_code"]).classes(
                                    "text-xs bg-stone-100 px-1.5 rounded font-mono")
                            ui.label(p["status"]).classes(
                                "text-xs px-1.5 rounded "
                                + _STATUS_COLORS.get(p["status"], "bg-gray-100"))

            def refresh_left():
                render_left()

            def on_select(pid):
                state["selected"] = pid
                render_left()   # update highlight
                render_right()

            for w in (proj_filter, name_in, status_filter):
                w.on_value_change(lambda: refresh_left())

            render_left()
            render_right()
