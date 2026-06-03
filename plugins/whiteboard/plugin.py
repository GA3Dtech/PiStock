# PiStock plugin — Whiteboard (sticky notes)
# Copyright (C) 2026 GA3Dtech — AGPLv3
#
# A central, dead-simple sticky-note whiteboard. Each note has a bold
# title, a text body, optional hashtags and an optional attached image
# (e.g. a photo of a hand sketch). Full-text search filters notes by
# their content or hashtags. Several named boards are supported.
#
# Fully independent of the core: it owns its table (prefix
# plugin_whiteboard_*) and stores images under uploads/whiteboard/. It
# only uses the public `main` facade (main.engine, main.DATA_DIR) and
# ships its own translations, so it changes nothing in the base app.

import os
import datetime

from sqlmodel import SQLModel, Field, Session, select


# ----------------------------------------------------------------------
#  Own table (created on load via checkfirst — see _ensure_table)
# ----------------------------------------------------------------------
class WhiteboardPostit(SQLModel, table=True):
    __tablename__ = "plugin_whiteboard_postit"
    id: int | None = Field(default=None, primary_key=True)
    board: str = Field(default="General", index=True)
    title: str = Field(default="")
    note: str = Field(default="")
    hashtags: str = Field(default="")          # free text, e.g. "#idea #urgent"
    image_path: str | None = Field(default=None)   # relative to DATA_DIR
    created_at: str = Field(default="")
    updated_at: str = Field(default="")


def _ensure_table():
    import main
    WhiteboardPostit.__table__.create(main.engine, checkfirst=True)


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


# ----------------------------------------------------------------------
#  Translations (en/fr/de), self-contained
# ----------------------------------------------------------------------
T = {
    "en": {
        "title": "Whiteboard", "plugins": "Plugins", "catalog": "Catalog",
        "board": "Board", "search": "Search (text or #hashtag)",
        "new_note": "+ New note", "empty": "No note yet — click « + New note ».",
        "no_match": "No note matches the search.",
        "note_title": "Title", "note_body": "Note",
        "hashtags": "Hashtags (e.g. #idea #urgent)",
        "image": "Image", "add_image": "Add / replace image",
        "remove_image": "Remove image",
        "save": "Save", "cancel": "Cancel", "delete": "Delete", "edit": "Edit",
        "confirm_delete": "Delete this note?",
        "default_board": "General",
    },
    "fr": {
        "title": "Tableau blanc", "plugins": "Plugins", "catalog": "Catalogue",
        "board": "Tableau", "search": "Rechercher (texte ou #hashtag)",
        "new_note": "+ Nouvelle note", "empty": "Aucune note — cliquez « + Nouvelle note ».",
        "no_match": "Aucune note ne correspond à la recherche.",
        "note_title": "Titre", "note_body": "Note",
        "hashtags": "Hashtags (ex. #idee #urgent)",
        "image": "Image", "add_image": "Ajouter / remplacer l'image",
        "remove_image": "Retirer l'image",
        "save": "Enregistrer", "cancel": "Annuler", "delete": "Supprimer", "edit": "Éditer",
        "confirm_delete": "Supprimer cette note ?",
        "default_board": "Général",
    },
    "de": {
        "title": "Whiteboard", "plugins": "Plugins", "catalog": "Katalog",
        "board": "Tafel", "search": "Suchen (Text oder #Hashtag)",
        "new_note": "+ Neue Notiz", "empty": "Noch keine Notiz — « + Neue Notiz » klicken.",
        "no_match": "Keine Notiz passt zur Suche.",
        "note_title": "Titel", "note_body": "Notiz",
        "hashtags": "Hashtags (z.B. #idee #dringend)",
        "image": "Bild", "add_image": "Bild hinzufügen / ersetzen",
        "remove_image": "Bild entfernen",
        "save": "Speichern", "cancel": "Abbrechen", "delete": "Löschen", "edit": "Bearbeiten",
        "confirm_delete": "Diese Notiz löschen?",
        "default_board": "Allgemein",
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
#  Data access
# ----------------------------------------------------------------------
def _boards():
    import main
    with Session(main.engine) as s:
        rows = s.exec(select(WhiteboardPostit.board).distinct()).all()
    boards = sorted({b for b in rows if b})
    return boards or [_tr("default_board")]


def _fetch(board, query=""):
    """Notes of a board, optionally filtered by a search term (substring
    in title / note / hashtags, case-insensitive; a leading # is ignored)."""
    import main
    term = (query or "").strip().lower().lstrip("#").strip()
    with Session(main.engine) as s:
        rows = s.exec(
            select(WhiteboardPostit)
            .where(WhiteboardPostit.board == board)
            .order_by(WhiteboardPostit.updated_at.desc())
        ).all()
        out = []
        for r in rows:
            if term:
                hay = f"{r.title}\n{r.note}\n{r.hashtags}".lower()
                if term not in hay:
                    continue
            out.append({"id": r.id, "title": r.title, "note": r.note,
                        "hashtags": r.hashtags, "image_path": r.image_path})
        return out


def _save(board, title, note, hashtags, image_path, note_id=None):
    import main
    with Session(main.engine) as s:
        if note_id is not None:
            row = s.get(WhiteboardPostit, note_id)
            if row is None:
                return
        else:
            row = WhiteboardPostit(created_at=_now())
            s.add(row)
        row.board = board or _tr("default_board")
        row.title = (title or "").strip()
        row.note = (note or "").strip()
        row.hashtags = (hashtags or "").strip()
        row.image_path = image_path
        row.updated_at = _now()
        s.commit()


def _delete(note_id):
    import main
    with Session(main.engine) as s:
        row = s.get(WhiteboardPostit, note_id)
        if row is not None:
            s.delete(row)
            s.commit()


def _img_dir():
    import main
    d = os.path.join(main.DATA_DIR, "uploads", "whiteboard")
    os.makedirs(d, exist_ok=True)
    return d


def _save_image(filename, content_bytes):
    base, ext = os.path.splitext(filename or "image.jpg")
    if not ext:
        ext = ".jpg"
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in base) or "image"
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    name = f"{safe}_{stamp}{ext}"
    with open(os.path.join(_img_dir(), name), "wb") as f:
        f.write(content_bytes)
    return f"uploads/whiteboard/{name}"


def _hashtag_chips(text):
    """Split a hashtags string into clean tokens (with a leading #)."""
    raw = (text or "").replace(",", " ").split()
    out = []
    for t in raw:
        t = t.strip().lstrip("#")
        if t:
            out.append("#" + t)
    return out


# ======================================================================
#  PAGE
# ======================================================================
def register(app):
    from nicegui import ui

    _ensure_table()

    @ui.page("/plugin/whiteboard")
    def whiteboard_page():
        with ui.header().classes("bg-stone-800 text-white shadow"):
            with ui.row().classes("w-full items-center gap-3"):
                ui.label("📌 " + _tr("title")).classes("text-xl font-medium")
                ui.element("div").classes("flex-grow")
                ui.button("← " + _tr("plugins"),
                          on_click=lambda: ui.navigate.to("/plugins")) \
                    .props("flat color=white").classes("text-sm")
                ui.button("🏠 " + _tr("catalog"),
                          on_click=lambda: ui.navigate.to("/")) \
                    .props("flat color=white").classes("text-sm")

        state = {"board": _boards()[0]}

        with ui.column().classes("max-w-6xl mx-auto p-4 w-full gap-3"):
            with ui.row().classes("w-full items-end gap-2 flex-wrap"):
                board_sel = ui.select(
                    options=_boards(), value=state["board"],
                    label=_tr("board"), with_input=True,
                    new_value_mode="add-unique").classes("min-w-[160px]")
                search_in = ui.input(label=_tr("search")) \
                    .props("clearable dense").classes("min-w-[220px] flex-grow")
                ui.element("div").classes("flex-grow")
                ui.button(_tr("new_note"),
                          on_click=lambda: _open_editor()) \
                    .props("color=primary")

            grid = ui.column().classes("w-full")

            def render():
                board = board_sel.value or state["board"]
                state["board"] = board
                notes = _fetch(board, search_in.value or "")
                grid.clear()
                with grid:
                    if not notes:
                        msg = _tr("no_match") if (search_in.value or "").strip() \
                            else _tr("empty")
                        ui.label(msg).classes("text-gray-500 italic p-6")
                        return
                    with ui.row().classes("w-full gap-3 items-start flex-wrap"):
                        for n in notes:
                            _render_note(n)

            def _render_note(n):
                with ui.card().classes(
                        "w-64 p-3 gap-1 bg-yellow-50 border border-yellow-200 "
                        "shadow-sm"):
                    with ui.row().classes("w-full items-start no-wrap gap-1"):
                        ui.label(n["title"] or "—") \
                            .classes("font-bold text-base flex-grow break-words")
                        ui.button(icon="edit",
                                  on_click=lambda nn=n: _open_editor(nn)) \
                            .props("flat round dense size=sm")
                        ui.button(icon="delete",
                                  on_click=lambda nn=n: _confirm_delete(nn)) \
                            .props("flat round dense size=sm color=negative")
                    if n["image_path"]:
                        ui.image("/" + n["image_path"]) \
                            .classes("w-full rounded max-h-48 object-contain "
                                     "bg-white cursor-pointer") \
                            .on("click", lambda p=n["image_path"]: _open_image(p))
                    if n["note"]:
                        ui.label(n["note"]) \
                            .classes("text-sm whitespace-pre-wrap break-words")
                    chips = _hashtag_chips(n["hashtags"])
                    if chips:
                        with ui.row().classes("gap-1 flex-wrap"):
                            for c in chips:
                                ui.label(c).classes(
                                    "text-xs bg-yellow-200 text-yellow-900 "
                                    "px-1.5 rounded cursor-pointer") \
                                    .on("click",
                                        lambda t=c: (search_in.set_value(t), render()))

            def _open_image(rel):
                with ui.dialog() as dlg, ui.card().classes("p-1"):
                    ui.image("/" + rel).classes("max-w-[90vw] max-h-[85vh]") \
                        .on("click", dlg.close)
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
                pending = {"image": existing["image_path"] if existing else None}
                with ui.dialog() as dlg, ui.card().classes("min-w-[420px] gap-2"):
                    ui.label(_tr("edit") if existing else _tr("new_note")) \
                        .classes("text-lg font-medium")
                    title_in = ui.input(_tr("note_title"),
                                        value=existing["title"] if existing else "") \
                        .classes("w-full")
                    note_in = ui.textarea(_tr("note_body"),
                                          value=existing["note"] if existing else "") \
                        .classes("w-full").props("autogrow rows=4")
                    tags_in = ui.input(_tr("hashtags"),
                                       value=existing["hashtags"] if existing else "") \
                        .classes("w-full")

                    img_box = ui.row().classes("items-center gap-2")

                    def render_img():
                        img_box.clear()
                        with img_box:
                            if pending["image"]:
                                ui.image("/" + pending["image"]) \
                                    .classes("w-24 h-24 object-cover rounded bg-white")

                                def remove():
                                    pending["image"] = None
                                    render_img()
                                ui.button(_tr("remove_image"), on_click=remove) \
                                    .props("flat dense color=negative")
                    render_img()

                    def on_upload(e):
                        data = e.content.read()
                        pending["image"] = _save_image(e.name, data)
                        render_img()
                    ui.upload(label=_tr("add_image"), auto_upload=True,
                              max_files=1, on_upload=on_upload) \
                        .props('accept="image/*"').classes("w-full")

                    with ui.row().classes("w-full justify-end gap-2 mt-2"):
                        ui.button(_tr("cancel"), on_click=dlg.close).props("flat")

                        def save():
                            _save(board_sel.value or state["board"],
                                  title_in.value, note_in.value, tags_in.value,
                                  pending["image"],
                                  note_id=existing["id"] if existing else None)
                            dlg.close()
                            # a brand-new board may now exist
                            board_sel.options = _boards()
                            board_sel.update()
                            render()
                        ui.button(_tr("save"), on_click=save).props("color=primary")
                dlg.open()

            board_sel.on_value_change(lambda: render())
            search_in.on_value_change(lambda: render())
            render()
