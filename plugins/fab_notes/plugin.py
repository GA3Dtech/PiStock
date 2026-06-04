# PiStock plugin — Manufacturing notes ("Note de Fabrication")
# Copyright (C) 2026 GA3Dtech — AGPLv3
#
# A board of manufacturing notes, each ATTACHED TO A PART. A note has a
# bold title, a text body, optional images (photos of a sketch / a setup)
# and hashtags. Unlike the whiteboard, the board is not organised by
# free-form "boards" but by the part each note belongs to, so it can be
# filtered exactly like the parts list: by project, by part-name
# substring, by status and by hashtag/text.
#
# Fully independent of the core: it owns its table (prefix
# plugin_fab_notes_*) and stores images under uploads/fab_notes/. It only
# READS parts/projects through the public `main` facade (main.engine,
# main.Parts, main.Project, main._get_current_plm) and never writes them.
# It ships its own translations, so it changes nothing in the base app.

import os
import json
import datetime

from sqlmodel import SQLModel, Field, Session, select


# Sentinels for the filter selects (cannot collide with a real 3-letter
# project code, nor with a real part id).
ALL = "__all__"
NO_PROJECT = "__none__"


# ----------------------------------------------------------------------
#  Own table (created on load via checkfirst — see _ensure_table)
# ----------------------------------------------------------------------
class FabNote(SQLModel, table=True):
    __tablename__ = "plugin_fab_notes_note"
    id: int | None = Field(default=None, primary_key=True)
    # The part this note documents. Kept even if the part is later
    # deleted (the note then shows up as "orphan" rather than vanishing).
    id_parts: int = Field(default=0, index=True)
    title: str = Field(default="")
    note: str = Field(default="")
    hashtags: str = Field(default="")          # free text, e.g. "#setup #jig"
    # One or more relative image paths, stored as a JSON list of strings.
    image_path: str | None = Field(default=None)
    created_at: str = Field(default="")
    updated_at: str = Field(default="")


def _ensure_table():
    import main
    FabNote.__table__.create(main.engine, checkfirst=True)


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}


def _is_image(path):
    return os.path.splitext(path or "")[1].lower() in _IMAGE_EXTS


def _file_icon(name):
    """An emoji hinting at the file type, for non-image attachments."""
    ext = os.path.splitext(name or "")[1].lower()
    return {
        ".pdf": "📄",
        ".stl": "🧊", ".step": "🧊", ".stp": "🧊", ".3mf": "🧊",
        ".obj": "🧊", ".igs": "🧊", ".iges": "🧊", ".dxf": "🧊",
        ".gcode": "🖨️", ".gco": "🖨️", ".nc": "🖨️", ".ngc": "🖨️",
        ".zip": "🗜️", ".7z": "🗜️", ".rar": "🗜️",
        ".txt": "📝", ".md": "📝", ".csv": "📝", ".json": "📝",
    }.get(ext, "📎")


def _parse_files(value):
    """Read the attachments column into a list of {path, name}. Accepts a
    JSON list of objects (new format), a JSON list of bare path strings,
    or a single bare path (legacy). 'name' is the original file name shown
    to the user; 'path' is the stored relative path."""
    if not value:
        return []
    value = value.strip()
    items = []
    if value.startswith("["):
        try:
            data = json.loads(value)
        except Exception:
            return []
        for it in data:
            if isinstance(it, dict) and it.get("path"):
                items.append({"path": str(it["path"]),
                              "name": str(it.get("name")
                                          or os.path.basename(it["path"]))})
            elif isinstance(it, str) and it:
                items.append({"path": it, "name": os.path.basename(it)})
    else:
        items.append({"path": value, "name": os.path.basename(value)})
    return items


def _serialize_files(items):
    """Pack a list of {path, name} into the attachments column (JSON, or
    None when empty)."""
    out = [{"path": it["path"],
            "name": it.get("name") or os.path.basename(it["path"])}
           for it in (items or []) if it and it.get("path")]
    return json.dumps(out) if out else None


# ----------------------------------------------------------------------
#  Translations (en/fr/de), self-contained
# ----------------------------------------------------------------------
T = {
    "en": {
        "title": "Manufacturing notes", "plugins": "Plugins", "catalog": "Catalog",
        "project": "Project", "all_projects": "All projects",
        "no_project": "(No project)", "status": "Status", "all_status": "All status",
        "part": "Part", "part_name": "Part name", "search": "Search (text or #hashtag)",
        "new_note": "+ New note", "empty": "No manufacturing note yet.",
        "no_match": "No note matches the filters.",
        "note_title": "Title", "note_body": "Note",
        "hashtags": "Hashtags (e.g. #setup #jig)",
        "pick_part": "Attach to part", "pick_part_first": "Pick a part first.",
        "filter_part": "Filter parts by project",
        "add_files": "Add files (images, PDF, gcode, STL…)", "remove_image": "Remove",
        "files": "Attachments",
        "save": "Save", "cancel": "Cancel", "delete": "Delete", "edit": "Edit",
        "confirm_delete": "Delete this note?", "open_part": "Open part",
        "orphan": "⚠ part removed", "no_part": "—",
    },
    "fr": {
        "title": "Note de Fabrication", "plugins": "Plugins", "catalog": "Catalogue",
        "project": "Projet", "all_projects": "Tous les projets",
        "no_project": "(Aucun projet)", "status": "Statut", "all_status": "Tous les statuts",
        "part": "Pièce", "part_name": "Nom de pièce", "search": "Rechercher (texte ou #hashtag)",
        "new_note": "+ Nouvelle note", "empty": "Aucune note de fabrication.",
        "no_match": "Aucune note ne correspond aux filtres.",
        "note_title": "Titre", "note_body": "Note",
        "hashtags": "Hashtags (ex. #montage #gabarit)",
        "pick_part": "Associer à la pièce", "pick_part_first": "Choisissez d'abord une pièce.",
        "filter_part": "Filtrer les pièces par projet",
        "add_files": "Ajouter des fichiers (images, PDF, gcode, STL…)", "remove_image": "Retirer",
        "files": "Pièces jointes",
        "save": "Enregistrer", "cancel": "Annuler", "delete": "Supprimer", "edit": "Éditer",
        "confirm_delete": "Supprimer cette note ?", "open_part": "Ouvrir la pièce",
        "orphan": "⚠ pièce supprimée", "no_part": "—",
    },
    "de": {
        "title": "Fertigungsnotiz", "plugins": "Plugins", "catalog": "Katalog",
        "project": "Projekt", "all_projects": "Alle Projekte",
        "no_project": "(Kein Projekt)", "status": "Status", "all_status": "Alle Status",
        "part": "Teil", "part_name": "Teilename", "search": "Suchen (Text oder #Hashtag)",
        "new_note": "+ Neue Notiz", "empty": "Noch keine Fertigungsnotiz.",
        "no_match": "Keine Notiz passt zu den Filtern.",
        "note_title": "Titel", "note_body": "Notiz",
        "hashtags": "Hashtags (z.B. #aufbau #vorrichtung)",
        "pick_part": "Mit Teil verknüpfen", "pick_part_first": "Zuerst ein Teil wählen.",
        "filter_part": "Teile nach Projekt filtern",
        "add_files": "Dateien hinzufügen (Bilder, PDF, gcode, STL…)", "remove_image": "Entfernen",
        "files": "Anhänge",
        "save": "Speichern", "cancel": "Abbrechen", "delete": "Löschen", "edit": "Bearbeiten",
        "confirm_delete": "Diese Notiz löschen?", "open_part": "Teil öffnen",
        "orphan": "⚠ Teil entfernt", "no_part": "—",
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
#  Read-only access to the core (parts / projects), via the facade
# ----------------------------------------------------------------------
def _parts_index():
    """Map part id -> {name, status, project_id, project_code, thumb_url}.
    Read directly from the core so the plugin stays decoupled."""
    import main
    with Session(main.engine) as s:
        projects = {p.id: p for p in s.exec(select(main.Project)).all()}
        idx = {}
        for p in s.exec(select(main.Parts).order_by(main.Parts.part_name)).all():
            proj = projects.get(p.id_project) if p.id_project else None
            plm = main._get_current_plm(s, p.id)
            idx[p.id] = {
                "id": p.id,
                "name": p.part_name,
                "status": p.status,
                "project_id": p.id_project,
                "project_code": proj.code if proj else None,
                "thumb_url": (f"/{plm.path_2_thumbnail}"
                              if plm and plm.path_2_thumbnail else None),
            }
        return idx


def _projects():
    import main
    with Session(main.engine) as s:
        out = []
        for p in s.exec(select(main.Project).order_by(main.Project.code)).all():
            desc = (p.description or "").strip().splitlines()
            out.append({"code": p.code,
                        "label": (f"{p.code} — {desc[0]}" if desc else p.code)})
        return out


# ----------------------------------------------------------------------
#  Data access (own notes)
# ----------------------------------------------------------------------
def _fetch(project_code=ALL, status=ALL, name_query="", search=""):
    """Return notes joined with their part meta, filtered like the parts
    list: by project, status, part-name substring and free text/hashtag."""
    import main
    idx = _parts_index()
    name_q = (name_query or "").strip().lower()
    term = (search or "").strip().lower().lstrip("#").strip()
    with Session(main.engine) as s:
        rows = s.exec(
            select(FabNote).order_by(FabNote.updated_at.desc())
        ).all()
    out = []
    for r in rows:
        meta = idx.get(r.id_parts)
        # Project filter
        if project_code == NO_PROJECT:
            if not meta or meta["project_id"] is not None:
                continue
        elif project_code != ALL:
            if not meta or meta["project_code"] != project_code:
                continue
        # Status filter
        if status != ALL:
            if not meta or meta["status"] != status:
                continue
        # Part-name substring
        if name_q:
            if not meta or name_q not in meta["name"].lower():
                continue
        # Free text / hashtag search across the note content
        if term:
            hay = f"{r.title}\n{r.note}\n{r.hashtags}".lower()
            if term not in hay:
                continue
        out.append({
            "id": r.id, "part_id": r.id_parts,
            "title": r.title, "note": r.note, "hashtags": r.hashtags,
            "files": _parse_files(r.image_path), "meta": meta,
        })
    return out


def _save(part_id, title, note, hashtags, files, note_id=None):
    import main
    with Session(main.engine) as s:
        if note_id is not None:
            row = s.get(FabNote, note_id)
            if row is None:
                return
        else:
            row = FabNote(created_at=_now())
            s.add(row)
        row.id_parts = int(part_id)
        row.title = (title or "").strip()
        row.note = (note or "").strip()
        row.hashtags = (hashtags or "").strip()
        row.image_path = _serialize_files(files)
        row.updated_at = _now()
        s.commit()


def _delete(note_id):
    import main
    with Session(main.engine) as s:
        row = s.get(FabNote, note_id)
        if row is not None:
            s.delete(row)
            s.commit()


def _img_dir():
    import main
    d = os.path.join(main.DATA_DIR, "uploads", "fab_notes")
    os.makedirs(d, exist_ok=True)
    return d


def _save_file(filename, content_bytes):
    """Store any uploaded file under uploads/fab_notes/ with a collision-
    free stored name, and return {path, name} (name = original filename,
    kept for display/download)."""
    base, ext = os.path.splitext(filename or "file")
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in base) or "file"
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    stored = f"{safe}_{stamp}{ext}"
    with open(os.path.join(_img_dir(), stored), "wb") as f:
        f.write(content_bytes)
    return {"path": f"uploads/fab_notes/{stored}", "name": filename or stored}


def _hashtag_chips(text):
    """Split a hashtags string into clean tokens (with a leading #)."""
    raw = (text or "").replace(",", " ").split()
    out = []
    for t in raw:
        t = t.strip().lstrip("#")
        if t:
            out.append("#" + t)
    return out


_STATUS_CLASS = {
    "Init": "bg-stone-200 text-stone-700",
    "Revue": "bg-amber-200 text-amber-900",
    "Asset": "bg-green-200 text-green-900",
}


def _part_options(parts, project_filter):
    """Build the {part_id: label} map for the editor's part selector,
    optionally narrowed to one project (or to parts without a project)."""
    out = {}
    for m in parts:
        if project_filter == NO_PROJECT and m["project_id"] is not None:
            continue
        if project_filter not in (ALL, NO_PROJECT) \
                and m["project_code"] != project_filter:
            continue
        code = m["project_code"] or "—"
        out[m["id"]] = f"[{code}] {m['name']}"
    return out


# ======================================================================
#  PAGE
# ======================================================================
def register(app):
    from nicegui import ui

    _ensure_table()

    @ui.page("/plugin/fab_notes")
    def fab_notes_page():
        with ui.header().classes("bg-stone-800 text-white shadow"):
            with ui.row().classes("w-full items-center gap-3"):
                ui.label("🛠️ " + _tr("title")).classes("text-xl font-medium")
                ui.element("div").classes("flex-grow")
                ui.button("← " + _tr("plugins"),
                          on_click=lambda: ui.navigate.to("/plugins")) \
                    .props("flat color=white").classes("text-sm")
                ui.button("🏠 " + _tr("catalog"),
                          on_click=lambda: ui.navigate.to("/")) \
                    .props("flat color=white").classes("text-sm")

        with ui.column().classes("max-w-6xl mx-auto p-4 w-full gap-3"):
            # --- Filter bar (project / status / part-name / search) ----
            proj_opts = {ALL: _tr("all_projects"), NO_PROJECT: _tr("no_project")}
            for p in _projects():
                proj_opts[p["code"]] = p["label"]
            status_opts = {ALL: _tr("all_status"),
                           "Init": "Init", "Revue": "Revue", "Asset": "Asset"}

            with ui.row().classes("w-full items-end gap-2 flex-wrap"):
                proj_sel = ui.select(options=proj_opts, value=ALL,
                                     label=_tr("project")).classes("min-w-[170px]")
                status_sel = ui.select(options=status_opts, value=ALL,
                                       label=_tr("status")).classes("min-w-[140px]")
                name_in = ui.input(label=_tr("part_name")) \
                    .props("clearable dense").classes("min-w-[160px]")
                search_in = ui.input(label=_tr("search")) \
                    .props("clearable dense").classes("min-w-[200px] flex-grow")
                ui.element("div").classes("flex-grow")
                ui.button(_tr("new_note"), on_click=lambda: _open_editor()) \
                    .props("color=primary")

            grid = ui.column().classes("w-full")

            def render():
                notes = _fetch(proj_sel.value, status_sel.value,
                               name_in.value or "", search_in.value or "")
                grid.clear()
                with grid:
                    if not notes:
                        any_filter = any([
                            proj_sel.value != ALL, status_sel.value != ALL,
                            (name_in.value or "").strip(),
                            (search_in.value or "").strip()])
                        ui.label(_tr("no_match") if any_filter else _tr("empty")) \
                            .classes("text-gray-500 italic p-6")
                        return
                    with ui.row().classes("w-full gap-3 items-start flex-wrap"):
                        for n in notes:
                            _render_note(n)

            def _render_note(n):
                meta = n["meta"]
                with ui.card().classes(
                        "w-72 p-3 gap-1 bg-sky-50 border border-sky-200 shadow-sm"):
                    # Header: thumbnail + part name + status + actions
                    with ui.row().classes("w-full items-start no-wrap gap-2"):
                        if meta and meta["thumb_url"]:
                            ui.image(meta["thumb_url"]) \
                                .classes("w-10 h-10 object-contain bg-white rounded "
                                         "cursor-pointer flex-shrink-0") \
                                .on("click", lambda m=meta:
                                    ui.navigate.to(f"/part/{m['id']}"))
                        with ui.column().classes("gap-0 flex-grow min-w-0"):
                            pname = meta["name"] if meta else _tr("orphan")
                            lbl = ui.label(pname).classes(
                                "font-semibold text-sm break-words leading-tight")
                            if meta:
                                lbl.classes("text-sky-800 cursor-pointer")
                                lbl.on("click", lambda m=meta:
                                       ui.navigate.to(f"/part/{m['id']}"))
                                lbl.tooltip(_tr("open_part"))
                            with ui.row().classes("gap-1 items-center"):
                                if meta and meta["project_code"]:
                                    ui.label(meta["project_code"]).classes(
                                        "text-[10px] bg-sky-200 text-sky-900 "
                                        "px-1 rounded")
                                if meta:
                                    ui.label(meta["status"]).classes(
                                        "text-[10px] px-1 rounded " +
                                        _STATUS_CLASS.get(meta["status"],
                                                          "bg-stone-200"))
                        ui.button(icon="edit",
                                  on_click=lambda nn=n: _open_editor(nn)) \
                            .props("flat round dense size=sm")
                        ui.button(icon="delete",
                                  on_click=lambda nn=n: _confirm_delete(nn)) \
                            .props("flat round dense size=sm color=negative")

                    if n["title"]:
                        ui.label(n["title"]).classes(
                            "font-bold text-base break-words")

                    imgs = [f["path"] for f in n["files"] if _is_image(f["path"])]
                    docs = [f for f in n["files"] if not _is_image(f["path"])]
                    if len(imgs) == 1:
                        ui.image("/" + imgs[0]) \
                            .classes("w-full rounded max-h-48 object-contain "
                                     "bg-white cursor-pointer") \
                            .on("click", lambda p=imgs[0]: _open_image(p))
                    elif imgs:
                        with ui.row().classes("w-full gap-1 flex-wrap"):
                            for p in imgs:
                                ui.image("/" + p) \
                                    .classes("w-[72px] h-[72px] object-cover rounded "
                                             "bg-white cursor-pointer") \
                                    .on("click", lambda pp=p: _open_image(pp))
                    if docs:
                        with ui.column().classes("w-full gap-1"):
                            for f in docs:
                                ui.link(f"{_file_icon(f['name'])} {f['name']}",
                                        "/" + f["path"]) \
                                    .props('target=_blank') \
                                    .classes("text-sm text-sky-700 hover:underline "
                                             "break-all bg-sky-100 px-2 py-0.5 rounded "
                                             "w-full")

                    if n["note"]:
                        ui.label(n["note"]).classes(
                            "text-sm whitespace-pre-wrap break-words")
                    chips = _hashtag_chips(n["hashtags"])
                    if chips:
                        with ui.row().classes("gap-1 flex-wrap"):
                            for c in chips:
                                ui.label(c).classes(
                                    "text-xs bg-sky-200 text-sky-900 px-1.5 "
                                    "rounded cursor-pointer") \
                                    .on("click",
                                        lambda t=c: (search_in.set_value(t), render()))

            def _open_image(rel):
                zoom = {"k": 1.0}
                with ui.dialog().props("maximized") as dlg, \
                        ui.card().classes("p-0 bg-black w-screen h-screen"):
                    with ui.row().classes("w-full items-center gap-1 p-2 "
                                          "bg-stone-800 text-white"):
                        ui.button(icon="zoom_out",
                                  on_click=lambda: _apply(zoom["k"] - 0.25)) \
                            .props("flat round color=white")
                        pct = ui.label("100%").classes("w-14 text-center")
                        ui.button(icon="zoom_in",
                                  on_click=lambda: _apply(zoom["k"] + 0.25)) \
                            .props("flat round color=white")
                        ui.button(icon="fit_screen",
                                  on_click=lambda: _apply(1.0)) \
                            .props("flat round color=white")
                        ui.element("div").classes("flex-grow")
                        ui.button(icon="close", on_click=dlg.close) \
                            .props("flat round color=white")
                    pan = ui.element("div").classes(
                        "w-full overflow-auto flex items-center justify-center") \
                        .style("height: calc(100vh - 64px)")
                    with pan:
                        img = ui.image("/" + rel).classes("max-w-none") \
                            .style("transition: transform .05s linear;")

                    def _apply(k):
                        k = max(0.25, min(k, 8.0))
                        zoom["k"] = k
                        img.style(f"transform: scale({k}); "
                                  "transform-origin: center center;")
                        pct.set_text(f"{int(round(k * 100))}%")

                    def _wheel(e):
                        dy = (e.args or {}).get("deltaY", 0)
                        _apply(zoom["k"] * (0.9 if dy > 0 else 1.1))
                    pan.on("wheel.prevent", _wheel, ["deltaY"])
                    _apply(1.0)
                dlg.open()

            def _confirm_delete(n):
                with ui.dialog() as dlg, ui.card():
                    ui.label(_tr("confirm_delete")).classes("font-medium")
                    if n["title"]:
                        ui.label(n["title"]).classes("text-sm text-gray-600")
                    with ui.row().classes("w-full justify-end gap-2 mt-2"):
                        ui.button(_tr("cancel"), on_click=dlg.close).props("flat")

                        def do():
                            _delete(n["id"]); dlg.close(); render()
                        ui.button(_tr("delete"), on_click=do).props("color=negative")
                dlg.open()

            def _open_editor(existing=None):
                parts = list(_parts_index().values())
                pending = {"files": list(existing["files"]) if existing else []}
                cur_part = existing["part_id"] if existing else None

                with ui.dialog() as dlg, ui.card().classes("min-w-[460px] gap-2"):
                    ui.label(_tr("edit") if existing else _tr("new_note")) \
                        .classes("text-lg font-medium")

                    # Part picker, with a project filter to narrow it down
                    pf_opts = {ALL: _tr("all_projects"), NO_PROJECT: _tr("no_project")}
                    for p in _projects():
                        pf_opts[p["code"]] = p["label"]
                    proj_filter = ui.select(options=pf_opts, value=ALL,
                                            label=_tr("filter_part")).classes("w-full")
                    part_sel = ui.select(
                        options=_part_options(parts, ALL), value=cur_part,
                        label=_tr("pick_part"), with_input=True).classes("w-full")

                    def refilter():
                        part_sel.options = _part_options(parts, proj_filter.value)
                        if part_sel.value not in part_sel.options:
                            part_sel.value = None
                        part_sel.update()
                    proj_filter.on_value_change(lambda: refilter())

                    title_in = ui.input(_tr("note_title"),
                                        value=existing["title"] if existing else "") \
                        .classes("w-full")
                    note_in = ui.textarea(_tr("note_body"),
                                          value=existing["note"] if existing else "") \
                        .classes("w-full").props("autogrow rows=4")
                    tags_in = ui.input(_tr("hashtags"),
                                       value=existing["hashtags"] if existing else "") \
                        .classes("w-full")

                    files_box = ui.row().classes("items-start gap-2 flex-wrap")

                    def _remove(ff):
                        pending["files"] = [x for x in pending["files"]
                                            if x["path"] != ff["path"]]
                        render_files()

                    def render_files():
                        files_box.clear()
                        with files_box:
                            for f in pending["files"]:
                                if _is_image(f["path"]):
                                    with ui.column().classes("items-center gap-0"):
                                        ui.image("/" + f["path"]).classes(
                                            "w-24 h-24 object-cover rounded bg-white")
                                        ui.button(_tr("remove_image"),
                                                  on_click=lambda ff=f: _remove(ff)) \
                                            .props("flat dense size=sm color=negative")
                                else:
                                    with ui.row().classes(
                                            "items-center gap-1 bg-stone-100 "
                                            "rounded px-2 py-1"):
                                        ui.label(f"{_file_icon(f['name'])} "
                                                 f"{f['name']}").classes(
                                            "text-sm break-all max-w-[220px]")
                                        ui.button(icon="close",
                                                  on_click=lambda ff=f: _remove(ff)) \
                                            .props("flat round dense size=sm "
                                                   "color=negative")
                    render_files()

                    async def on_upload(e):
                        data = await e.file.read()
                        pending["files"].append(_save_file(e.file.name, data))
                        render_files()
                    ui.upload(label=_tr("add_files"), auto_upload=True,
                              multiple=True, on_upload=on_upload).classes("w-full")

                    with ui.row().classes("w-full justify-end gap-2 mt-2"):
                        ui.button(_tr("cancel"), on_click=dlg.close).props("flat")

                        def save():
                            if not part_sel.value:
                                ui.notify(_tr("pick_part_first"), type="warning")
                                return
                            _save(part_sel.value, title_in.value, note_in.value,
                                  tags_in.value, pending["files"],
                                  note_id=existing["id"] if existing else None)
                            dlg.close()
                            render()
                        ui.button(_tr("save"), on_click=save).props("color=primary")
                dlg.open()

            proj_sel.on_value_change(lambda: render())
            status_sel.on_value_change(lambda: render())
            name_in.on_value_change(lambda: render())
            search_in.on_value_change(lambda: render())
            render()
