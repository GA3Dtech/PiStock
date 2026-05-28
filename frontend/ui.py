"""
Interface NiceGUI pour PiStock.

S'attache au MEME FastAPI 'app' que les endpoints REST définis dans
main.py. Du coup, ce fichier accède directement à la base via les
modèles SQLModel importés depuis main (pas de HTTP interne).

Pages :
  /         -> dashboard : liste des pièces
  /part/{id} -> viewer 3D pour une pièce donnée
"""
import os
import shutil
from datetime import datetime, timezone
from nicegui import ui, events
from sqlmodel import Session, select

# IMPORT TARDIF de main : on évite l'import circulaire en repoussant
# la résolution à l'intérieur des fonctions. Au moment où main.py
# exécute "import ui", main est encore en train d'être chargé et ses
# symboles (engine, Parts...) n'existent pas tous. En important au
# moment où la fonction tourne, on est sûr que main est complet.
def _db():
    """Helper qui renvoie tous les symboles dont on a besoin depuis main."""
    import main
    return main.engine, main.Parts, main.PLM, main.Stock, main.DATA_DIR


# ======================================================================
#  ACCES BASE DE DONNEES
# ======================================================================
def fetch_parts_full():
    """Liste enrichie : pour chaque piece, derniere revision PLM
    + ligne de stock si elle existe."""
    engine, Parts, PLM, Stock, _ = _db()
    with Session(engine) as session:
        parts = session.exec(select(Parts).order_by(Parts.part_name)).all()
        result = []
        for p in parts:
            latest_plm = session.exec(
                select(PLM).where(PLM.id_parts == p.id)
                .order_by(PLM.timestamp.desc())
            ).first()
            stock_row = session.exec(
                select(Stock).where(Stock.id_parts == p.id)
            ).first()
            result.append({
                "id": p.id,
                "part_name": p.part_name,
                "thumbnail_url": (f"/{latest_plm.path_2_thumbnail}"
                                   if latest_plm and latest_plm.path_2_thumbnail
                                   else None),
                "glb_url": (f"/{latest_plm.path_2_3dglb}"
                             if latest_plm and latest_plm.path_2_3dglb
                             else None),
                "stock_img_url": (f"/{stock_row.path_2_img}"
                                   if stock_row and stock_row.path_2_img
                                   else None),
                "quantity": stock_row.quantity if stock_row else None,
                "location": stock_row.location if stock_row else None,
            })
        return result


def fetch_part_detail(part_id: int):
    """Detail d'une piece pour la page viewer 3D."""
    engine, Parts, PLM, _, _ = _db()
    with Session(engine) as session:
        p = session.get(Parts, part_id)
        if p is None:
            return None
        latest_plm = session.exec(
            select(PLM).where(PLM.id_parts == p.id)
            .order_by(PLM.timestamp.desc())
        ).first()
        return {
            "id": p.id,
            "part_name": p.part_name,
            "glb_url": (f"/{latest_plm.path_2_3dglb}"
                         if latest_plm and latest_plm.path_2_3dglb
                         else None),
            "last_author": latest_plm.author if latest_plm else None,
            "last_timestamp": (latest_plm.timestamp.isoformat()
                                if latest_plm else None),
        }


def create_part_in_db(part_name: str):
    """Cree une piece manuellement (sans CAO). Retourne (ok, message, id)."""
    engine, Parts, _, _, _ = _db()
    part_name = part_name.strip()
    if not part_name:
        return (False, "Le nom de la pièce est obligatoire.", None)
    with Session(engine) as session:
        existing = session.exec(
            select(Parts).where(Parts.part_name == part_name)
        ).first()
        if existing:
            return (False,
                    f"Une pièce nommée '{part_name}' existe déjà "
                    f"(id={existing.id}).",
                    None)
        part = Parts(part_name=part_name)
        session.add(part)
        session.commit()
        session.refresh(part)
        return (True, f"Pièce '{part_name}' créée (id={part.id}).", part.id)


def save_stock_photo(part_id: int, source_path: str, original_filename: str):
    """Copie le fichier upload vers data-pistock/uploads/img/ et met
    a jour (ou cree) la ligne stock. Retourne (ok, message)."""
    engine, Parts, _, Stock, DATA_DIR = _db()
    with Session(engine) as session:
        part = session.get(Parts, part_id)
        if part is None:
            return (False, f"Aucune pièce avec l'id {part_id}.")

        ts_tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        _, ext = os.path.splitext(original_filename or "")
        if not ext:
            ext = ".jpg"
        stamped_name = f"stock_{part_id}_{ts_tag}{ext}"
        dest_dir = os.path.join(DATA_DIR, "uploads", "img")
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, stamped_name)
        shutil.copyfile(source_path, dest_path)
        rel_path = f"uploads/img/{stamped_name}"

        stock_row = session.exec(
            select(Stock).where(Stock.id_parts == part_id)
        ).first()
        if stock_row is None:
            stock_row = Stock(id_parts=part_id, path_2_img=rel_path)
            session.add(stock_row)
        else:
            stock_row.path_2_img = rel_path
            session.add(stock_row)
        session.commit()
        return (True, "Photo de stock enregistrée.")


# ======================================================================
#  PAGE : DASHBOARD
# ======================================================================
@ui.page("/")
def dashboard_page():
    """Page principale : liste des pieces sous forme de cartes."""

    # En-tete sombre, comme dans la version HTML
    with ui.header().classes("bg-stone-800 text-white shadow"):
        ui.label("📦 PiStock — Catalogue").classes("text-xl font-medium")

    # Conteneur principal centre, largeur max
    with ui.column().classes("w-full max-w-5xl mx-auto p-4 gap-4"):

        # Barre d'actions : bouton "Nouvelle piece" a droite
        with ui.row().classes("w-full justify-end"):
            ui.button("+ Nouvelle pièce", on_click=lambda: open_new_part_dialog()) \
                .props("color=primary").classes("text-base")

        # Conteneur de la liste, rempli puis re-rempli par refresh_list()
        list_container = ui.column().classes("w-full gap-3")

        def refresh_list():
            """Vide puis re-rempli la liste depuis la base."""
            list_container.clear()
            parts = fetch_parts_full()

            if not parts:
                with list_container:
                    ui.label("Aucune pièce dans la base pour l'instant. "
                             "Cliquez sur « + Nouvelle pièce » ou exportez-en "
                             "une depuis FreeCAD.") \
                        .classes("text-gray-500 text-center p-8")
                return

            for part in parts:
                with list_container:
                    render_part_row(part, refresh_list)

        # Premier remplissage
        refresh_list()

        # --- Dialogue "Nouvelle piece" --------------------------------
        # Construit une fois, ouvert a la demande. NiceGUI permet de
        # creer le dialogue ici et de l'afficher avec .open().
        with ui.dialog() as new_part_dialog, ui.card().classes("min-w-[360px]"):
            ui.label("Nouvelle pièce").classes("text-lg font-medium")
            name_input = ui.input("Nom de la pièce", placeholder="ex: bracket-v2") \
                .classes("w-full")
            error_label = ui.label("").classes("text-red-600 text-sm min-h-[1.2em]")
            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button("Annuler", on_click=new_part_dialog.close) \
                    .props("flat")
                ui.button("Créer",
                          on_click=lambda: confirm_create_part()) \
                    .props("color=primary")

            def confirm_create_part():
                ok, msg, _new_id = create_part_in_db(name_input.value or "")
                if not ok:
                    error_label.text = msg
                    return
                error_label.text = ""
                ui.notify(msg, type="positive")
                new_part_dialog.close()
                refresh_list()

            # Touche Entree dans le champ -> valide
            name_input.on("keydown.enter", lambda _: confirm_create_part())

        def open_new_part_dialog():
            name_input.value = ""
            error_label.text = ""
            new_part_dialog.open()


# ======================================================================
#  RENDU D'UNE LIGNE
# ======================================================================
def render_part_row(part: dict, on_change):
    """Rendu d'une ligne de piece. 'on_change' est appele apres une
    action qui modifie la base (upload photo), pour rafraichir la liste."""

    # Carte avec ombre + bordure, comme dans la version HTML
    with ui.card().classes("w-full p-4"):
        with ui.row().classes("w-full items-center gap-4 no-wrap"):

            # --- Nom (colonne large) -------------------------------
            ui.label(part["part_name"]) \
                .classes("text-base font-medium flex-grow")

            # --- Vignette CAO (cliquable -> viewer 3D) -------------
            with ui.element("div").classes(
                    "w-20 h-20 bg-stone-100 rounded-lg flex items-center "
                    "justify-center overflow-hidden flex-shrink-0"):
                if part["thumbnail_url"]:
                    img = ui.image(part["thumbnail_url"]) \
                        .classes("w-full h-full object-contain")
                    if part["glb_url"]:
                        img.classes("cursor-pointer hover:scale-105 transition")
                        img.on("click",
                               lambda p=part: ui.navigate.to(f"/part/{p['id']}"))
                        img.tooltip("Cliquer pour voir en 3D")
                else:
                    ui.label("Pas de vignette") \
                        .classes("text-xs text-gray-400 text-center")

            # --- Photo de stock + bouton ajout/remplacement --------
            render_stock_photo_cell(part, on_change)

            # --- Quantite ------------------------------------------
            qty = part["quantity"]
            qty_text = "—" if qty is None else str(qty)
            qty_color = "text-gray-300" if qty is None else "text-stone-800"
            ui.label(qty_text) \
                .classes(f"text-lg {qty_color} w-16 text-center flex-shrink-0")

            # --- Location ------------------------------------------
            loc = part["location"]
            loc_text = loc if loc else "—"
            loc_color = "text-gray-300" if not loc else "text-stone-700"
            ui.label(loc_text) \
                .classes(f"text-sm {loc_color} w-32 flex-shrink-0")


def render_stock_photo_cell(part: dict, on_change):
    """Cellule de la photo de stock : image + lien "Remplacer", ou
    bouton "Ajouter" si pas encore de photo. Utilise ui.upload pour
    declencher l'upload silencieusement."""

    part_id = part["id"]

    def handle_upload(e: events.UploadEventArguments):
        # NiceGUI nous donne un objet fichier-like. On le copie vers
        # un fichier temporaire avant de le passer a save_stock_photo
        # (qui attend un chemin disque).
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            shutil.copyfileobj(e.content, tmp)
            tmp_path = tmp.name
        try:
            ok, msg = save_stock_photo(part_id, tmp_path, e.name)
            if ok:
                ui.notify(msg, type="positive")
                on_change()
            else:
                ui.notify(msg, type="negative")
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    with ui.column().classes("items-center gap-1 flex-shrink-0"):
        if part["stock_img_url"]:
            # Photo existante : on l'affiche + petit bouton "Remplacer"
            with ui.element("div").classes(
                    "w-20 h-20 bg-stone-100 rounded-lg flex items-center "
                    "justify-center overflow-hidden"):
                ui.image(part["stock_img_url"]) \
                    .classes("w-full h-full object-contain")
            # ui.upload masque le bouton natif et expose un trigger
            up = ui.upload(on_upload=handle_upload, auto_upload=True,
                            max_files=1) \
                .props('accept="image/*"').classes("hidden")
            ui.button("Remplacer", on_click=lambda: up.run_method("pickFiles")) \
                .props("flat dense color=primary").classes("text-xs")
        else:
            # Pas de photo : gros bouton d'ajout style "dashed"
            up = ui.upload(on_upload=handle_upload, auto_upload=True,
                            max_files=1) \
                .props('accept="image/*"').classes("hidden")
            with ui.button(on_click=lambda: up.run_method("pickFiles")) \
                    .props("flat") \
                    .classes("w-20 h-20 border-2 border-dashed "
                              "border-stone-300 rounded-lg hover:border-blue-500 "
                              "hover:text-blue-500 text-stone-500"):
                with ui.column().classes("items-center gap-0"):
                    ui.label("📷").classes("text-xl")
                    ui.label("Ajouter").classes("text-xs")


# ======================================================================
#  PAGE : VIEWER 3D
# ======================================================================
@ui.page("/part/{part_id}")
def part_page(part_id: int):
    """Page viewer 3D pour une piece donnee."""
    part = fetch_part_detail(part_id)

    with ui.header().classes("bg-stone-800 text-white shadow"):
        ui.label("📦 PiStock — Vue 3D").classes("text-xl font-medium")

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

        # --- Scene 3D ---------------------------------------------
        # ui.scene() integre Three.js. Le .glb est charge via URL HTTP
        # (notre mount /uploads/ le sert).
        # On encadre dans une carte pour rester cohérent visuellement.
        with ui.card().classes("w-full p-0 overflow-hidden"):
            with ui.scene(width=1100, height=600,
                           background_color="#f5f5f7") as scene:
                # Charge le glTF. Les .glb de FreeCAD sont souvent en
                # millimetres avec Y-up ou Z-up selon l'export ; on
                # garde l'orientation par defaut. Si la piece apparait
                # trop grande/petite, ajuster .scale() ici.
                scene.gltf(part["glb_url"])
                # Place la camera un peu en arriere pour voir l'objet.
                # L'utilisateur peut ensuite zoomer/orbiter a la souris.
                scene.move_camera(x=2, y=2, z=2,
                                   look_at_x=0, look_at_y=0, look_at_z=0)

        # Bloc d'infos sous le viewer
        author = part.get("last_author") or "—"
        ts = part.get("last_timestamp") or "—"
        with ui.card().classes("w-full"):
            ui.label(f"Dernière révision par {author} le {ts}") \
                .classes("text-sm text-gray-600")


# ======================================================================
#  DEMARRAGE
# ======================================================================
# Branche NiceGUI sur le FastAPI 'app' defini dans main.py. Nos pages
# @ui.page sont alors accessibles a la racine du meme serveur.
# 'storage_secret' est obligatoire des qu'on utilise ui.storage.user ;
# on le fournit par precaution meme si on ne s'en sert pas ici.
import main as _main_module
ui.run_with(_main_module.app,
            storage_secret="pistock-dev-secret-change-me")
