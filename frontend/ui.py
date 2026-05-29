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


def _db_project():
    """Helper dedie aux projets : renvoie engine + classe Project +
    fonction de generation du prochain code. On garde un helper distinct
    pour ne pas casser la signature de _db() utilisee partout ailleurs."""
    import main
    return main.engine, main.Project, main._next_project_code


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


# ----------------------------------------------------------------------
#  PROJETS
# ----------------------------------------------------------------------
def fetch_projects():
    """Liste tous les projets, tries par code croissant."""
    engine, Project, _ = _db_project()
    with Session(engine) as session:
        projects = session.exec(
            select(Project).order_by(Project.code)
        ).all()
        return [
            {"id": p.id, "code": p.code, "description": p.description}
            for p in projects
        ]


def create_project_in_db(description: str):
    """Cree un projet avec code auto-genere. Retourne (ok, msg, code)."""
    engine, Project, next_project_code = _db_project()
    description = (description or "").strip() or None
    with Session(engine) as session:
        try:
            code = next_project_code(session)
        except Exception as e:
            # Cas extreme : ZZZ atteint (HTTPException levee par main)
            return (False, str(e), None)
        project = Project(code=code, description=description)
        session.add(project)
        session.commit()
        session.refresh(project)
        return (True, f"Projet '{code}' créé.", code)


# Note : la sauvegarde des photos de stock se fait via l'endpoint REST
# POST /api/v1/parts/{id}/stock-photo dans main.py, appele directement
# par le JS du navigateur (fetch). On n'a pas besoin d'une version
# Python ici, ce qui evite aussi de dupliquer la logique de chemins.


# ======================================================================
#  PAGE : DASHBOARD
# ======================================================================
@ui.page("/")
def dashboard_page():
    """Page principale : liste des pieces sous forme de cartes."""

    # JavaScript injecte au <head> de la page. Comme NiceGUI 3.x
    # sanitise le contenu de ui.html() et RETIRE les attributs 'on*'
    # (onchange, onclick...), on ne peut pas mettre onchange="..."
    # inline. A la place : event delegation. Un seul listener attache
    # au document detecte tous les change sur les inputs portant
    # data-stock-upload="{part_id}" et fait l'upload.
    ui.add_head_html('''
        <script>
        // Garde-fou : n'installe le listener qu'une seule fois
        if (!window._stockUploadInstalled) {
            window._stockUploadInstalled = true;
            document.addEventListener('change', async function(e) {
                // Filtre : on ne traite que nos inputs marques
                if (!e.target || !e.target.matches('input[data-stock-upload]')) {
                    return;
                }
                const partId = e.target.dataset.stockUpload;
                const file = e.target.files[0];
                if (!file) return;

                const formData = new FormData();
                formData.append("photo", file);
                try {
                    const response = await fetch(
                        `/api/v1/parts/${partId}/stock-photo`,
                        { method: "POST", body: formData }
                    );
                    if (!response.ok) {
                        const err = await response.json().catch(() => ({}));
                        alert("Erreur upload : " + (err.detail || response.status));
                        return;
                    }
                    // Rafraichit la page pour faire apparaitre la nouvelle photo.
                    window.location.reload();
                } catch (err) {
                    alert("Erreur : " + err.message);
                }
            });
        }
        </script>
    ''')

    # En-tete sombre, comme dans la version HTML
    with ui.header().classes("bg-stone-800 text-white shadow"):
        ui.label("📦 PiStock — Catalogue").classes("text-xl font-medium")

    # Conteneur principal centre, largeur max
    with ui.column().classes("w-full max-w-5xl mx-auto p-4 gap-4"):

        # Barre d'actions : boutons "Projet" et "Nouvelle piece" a droite.
        # 'Projet' ouvre un dialogue de gestion des projets ; "Nouvelle
        # piece" cree une piece directement.
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Projet", on_click=lambda: open_projects_dialog()) \
                .props("color=primary outline").classes("text-base")
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

        # --- Dialogue "Projets" ---------------------------------------
        # Liste les projets existants + formulaire de creation inline
        # (revelable). Le code (AAA, AAB...) est genere par le serveur,
        # l'utilisateur saisit juste la description.
        with ui.dialog() as projects_dialog, \
                ui.card().classes("min-w-[480px] max-w-[600px]"):
            ui.label("Projets").classes("text-lg font-medium")

            # Conteneur scrollable pour la liste des projets.
            # Vide puis rempli par refresh_projects_list().
            projects_list_container = ui.column() \
                .classes("w-full gap-2 max-h-[400px] overflow-y-auto")

            # Formulaire de creation, masque par defaut.
            with ui.column().classes("w-full gap-2 mt-2") as creation_form:
                ui.label("Nouveau projet").classes("text-sm font-medium")
                desc_input = ui.textarea(
                    placeholder="Description (optionnelle)") \
                    .classes("w-full").props("autogrow rows=3")
                proj_error = ui.label("") \
                    .classes("text-red-600 text-sm min-h-[1.2em]")
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Annuler",
                              on_click=lambda: hide_creation_form()) \
                        .props("flat")
                    ui.button("Créer",
                              on_click=lambda: confirm_create_project()) \
                        .props("color=primary")
            creation_form.set_visibility(False)

            # Boutons du pied : "+ Nouveau projet" + "Fermer"
            with ui.row().classes("w-full justify-between gap-2 mt-2") \
                    as footer_row:
                add_btn = ui.button("+ Nouveau projet",
                                     on_click=lambda: show_creation_form()) \
                    .props("color=primary outline")
                ui.button("Fermer", on_click=projects_dialog.close) \
                    .props("flat")

            def refresh_projects_list():
                """Vide puis re-rempli la liste depuis la base."""
                projects_list_container.clear()
                projects = fetch_projects()
                if not projects:
                    with projects_list_container:
                        ui.label("Aucun projet pour l'instant. "
                                 "Cliquez sur « + Nouveau projet » "
                                 "pour en créer un.") \
                            .classes("text-gray-500 text-sm text-center p-4")
                    return
                for proj in projects:
                    with projects_list_container:
                        with ui.card().classes("w-full p-3"):
                            with ui.row().classes("items-start gap-3 no-wrap"):
                                # Code en grosse pastille
                                ui.label(proj["code"]) \
                                    .classes("text-lg font-mono font-bold "
                                              "text-blue-700 bg-blue-50 "
                                              "px-2 py-1 rounded "
                                              "flex-shrink-0")
                                # Description (ou italique si vide)
                                desc = proj["description"]
                                if desc:
                                    ui.label(desc) \
                                        .classes("text-sm text-stone-700 "
                                                  "whitespace-pre-wrap "
                                                  "flex-grow")
                                else:
                                    ui.label("(aucune description)") \
                                        .classes("text-sm text-gray-400 "
                                                  "italic flex-grow")

            def show_creation_form():
                desc_input.value = ""
                proj_error.text = ""
                creation_form.set_visibility(True)
                add_btn.set_visibility(False)

            def hide_creation_form():
                creation_form.set_visibility(False)
                add_btn.set_visibility(True)

            def confirm_create_project():
                ok, msg, code = create_project_in_db(desc_input.value or "")
                if not ok:
                    proj_error.text = msg
                    return
                proj_error.text = ""
                ui.notify(msg, type="positive")
                hide_creation_form()
                refresh_projects_list()

        def open_projects_dialog():
            # On rafraichit a chaque ouverture (au cas ou un autre
            # onglet/utilisateur aurait ajoute des projets entre-temps).
            hide_creation_form_silently()
            refresh_projects_list()
            projects_dialog.open()

        def hide_creation_form_silently():
            """Reset l'etat du formulaire sans notification."""
            creation_form.set_visibility(False)
            add_btn.set_visibility(True)


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
    """Cellule de la photo de stock : image + bouton "Remplacer", ou
    gros bouton dashed "Ajouter" si pas encore de photo.

    APPROCHE : on utilise du HTML pur via ui.html() avec un <label>
    qui contient un <input type="file"> cache. Cliquer sur le label
    declenche le file picker natif (comportement HTML standard, marche
    partout). L'upload est ensuite poste via fetch() vers l'endpoint
    REST /api/v1/parts/{id}/stock-photo. Cette approche est plus fiable
    que ui.upload + pickFiles et permet un controle stylistique total.
    Le JS 'uploadStockPhoto' est defini dans le <head> de la page."""

    part_id = part["id"]
    # 'on_change' n'est plus utilise ici : le rafraichissement se
    # fait cote navigateur via window.location.reload() apres l'upload.
    # On garde le parametre pour compatibilite avec l'appel existant.
    _ = on_change

    if part["stock_img_url"]:
        # Photo existante : on l'affiche, avec un petit lien "Remplacer"
        # en dessous (label sur un input file cache).
        # data-stock-upload="{id}" : detecte par le listener global.
        ui.html(f'''
            <div class="flex flex-col items-center gap-1 flex-shrink-0">
                <div class="w-20 h-20 bg-stone-100 rounded-lg flex items-center justify-center overflow-hidden">
                    <img src="{part["stock_img_url"]}"
                         alt="Photo stock"
                         class="w-full h-full object-contain">
                </div>
                <label class="text-xs text-blue-600 cursor-pointer hover:underline">
                    Remplacer
                    <input type="file" accept="image/*" style="display:none"
                           data-stock-upload="{part_id}">
                </label>
            </div>
        ''')
    else:
        # Pas de photo : gros bouton dashed avec emoji et "Ajouter"
        ui.html(f'''
            <label class="cursor-pointer flex-shrink-0" title="Ajouter une photo de la pièce en stock">
                <div class="w-20 h-20 border-2 border-dashed border-stone-300 rounded-lg
                            flex flex-col items-center justify-center gap-0
                            text-stone-500 transition
                            hover:border-blue-500 hover:text-blue-500 hover:bg-blue-50">
                    <span class="text-2xl leading-none">📷</span>
                    <span class="text-xs mt-1">Ajouter</span>
                </div>
                <input type="file" accept="image/*" style="display:none"
                       data-stock-upload="{part_id}">
            </label>
        ''')


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
