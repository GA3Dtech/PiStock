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
def fetch_parts_full(project_code: str | None = None):
    """Liste enrichie : pour chaque piece, derniere revision PLM,
    infos de stock, projet associe, statut, verrou. Filtre optionnel."""
    engine, Parts, PLM, Stock, _ = _db()
    import main
    Project_cls = main.Project
    with Session(engine) as session:
        query = select(Parts).order_by(Parts.part_name)
        if project_code:
            project = session.exec(
                select(Project_cls).where(Project_cls.code == project_code)
            ).first()
            if project is None:
                return []
            query = query.where(Parts.id_project == project.id)
        parts = session.exec(query).all()

        # Pre-charge des codes projets pour eviter une requete par piece
        projects_by_id = {
            p.id: p.code
            for p in session.exec(select(Project_cls)).all()
        }

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
                "id_project": p.id_project,
                "project_code": projects_by_id.get(p.id_project),
                "status": p.status,
                "locked": p.locked,
                "version": latest_plm.version if latest_plm else None,
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


def fetch_last_used_project_id():
    """Renvoie l'id du dernier projet utilise (par une piece), ou None."""
    engine, Parts_cls, _, _, _ = _db()
    with Session(engine) as session:
        part = session.exec(
            select(Parts_cls)
            .where(Parts_cls.id_project.is_not(None))
            .order_by(Parts_cls.id.desc())
            .limit(1)
        ).first()
        return part.id_project if part else None


def assign_project_to_part(part_id: int, project_id: int | None):
    """Assigne (ou dissocie si None) un projet. Retourne (ok, msg)."""
    engine, Parts_cls, _, _, _ = _db()
    import main
    Project_cls = main.Project
    with Session(engine) as session:
        part = session.get(Parts_cls, part_id)
        if part is None:
            return (False, "Pièce introuvable.")
        if part.locked:
            return (False, f"Pièce '{part.part_name}' verrouillée.")
        if project_id is not None:
            if session.get(Project_cls, project_id) is None:
                return (False, f"Projet introuvable.")
        part.id_project = project_id
        session.add(part)
        session.commit()
        return (True, "Projet assigné.")


def set_part_status_db(part_id: int, new_status: str):
    """Change le statut Init/Revue/Asset. Retourne (ok, msg)."""
    if new_status not in ("Init", "Revue", "Asset"):
        return (False, "Statut invalide.")
    engine, Parts_cls, _, _, _ = _db()
    with Session(engine) as session:
        part = session.get(Parts_cls, part_id)
        if part is None:
            return (False, "Pièce introuvable.")
        if part.locked:
            return (False, f"Pièce '{part.part_name}' verrouillée.")
        part.status = new_status
        session.add(part)
        session.commit()
        return (True, f"Statut → {new_status}.")


def toggle_part_lock_db(part_id: int):
    """Inverse le verrou. Retourne (ok, msg, new_locked)."""
    engine, Parts_cls, _, _, _ = _db()
    with Session(engine) as session:
        part = session.get(Parts_cls, part_id)
        if part is None:
            return (False, "Pièce introuvable.", None)
        part.locked = not part.locked
        session.add(part)
        session.commit()
        return (True,
                "Pièce verrouillée." if part.locked else "Pièce déverrouillée.",
                part.locked)


# ----------------------------------------------------------------------
#  STOCK : helpers DB
# ----------------------------------------------------------------------
def fetch_stock(part_id: int):
    """Renvoie les infos stock courantes. Si pas de ligne, valeurs
    par defaut (quantity=0, le reste a None)."""
    engine, _, _, Stock_cls, _ = _db()
    with Session(engine) as session:
        row = session.exec(
            select(Stock_cls).where(Stock_cls.id_parts == part_id)
        ).first()
        if row is None:
            return {"quantity": 0, "location": None, "supply": None,
                    "doc_url": None}
        return {
            "quantity": row.quantity,
            "location": row.location,
            "supply": row.supply,
            "doc_url": (f"/{row.path_2_doc}" if row.path_2_doc else None),
        }


def save_stock(part_id: int, quantity: int,
                location: str | None, supply: str | None):
    """Sauve les infos stock. Cree la ligne si elle n'existe pas.
    Le verrou ne s'applique pas : stock = info operationnelle."""
    if quantity is None or quantity < 0:
        return (False, "La quantité doit être un entier positif ou nul.")
    location = (location or "").strip() or None
    supply = (supply or "").strip() or None

    engine, Parts_cls, _, Stock_cls, _ = _db()
    with Session(engine) as session:
        if session.get(Parts_cls, part_id) is None:
            return (False, "Pièce introuvable.")
        row = session.exec(
            select(Stock_cls).where(Stock_cls.id_parts == part_id)
        ).first()
        if row is None:
            row = Stock_cls(id_parts=part_id)
            session.add(row)
        row.quantity = int(quantity)
        row.location = location
        row.supply = supply
        session.add(row)
        session.commit()
        return (True, "Stock mis à jour.")


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
        // Garde-fou : n'installe les listeners qu'une seule fois
        if (!window._stockUploadInstalled) {
            window._stockUploadInstalled = true;

            // ---- Listener pour les PHOTOS de stock ----
            // Cible : input[data-stock-upload="{part_id}"]
            // Endpoint : POST /api/v1/parts/{id}/stock-photo
            document.addEventListener('change', async function(e) {
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
                    window.location.reload();
                } catch (err) {
                    alert("Erreur : " + err.message);
                }
            });

            // ---- Listener pour les FICHES COMPOSANT (doc) ----
            // Cible : input[data-stock-doc="{part_id}"]
            // Endpoint : POST /api/v1/parts/{id}/stock-doc
            document.addEventListener('change', async function(e) {
                if (!e.target || !e.target.matches('input[data-stock-doc]')) {
                    return;
                }
                const partId = e.target.dataset.stockDoc;
                const file = e.target.files[0];
                if (!file) return;
                const formData = new FormData();
                formData.append("doc", file);
                try {
                    const response = await fetch(
                        `/api/v1/parts/${partId}/stock-doc`,
                        { method: "POST", body: formData }
                    );
                    if (!response.ok) {
                        const err = await response.json().catch(() => ({}));
                        alert("Erreur upload fiche : " + (err.detail || response.status));
                        return;
                    }
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

        # Barre d'actions : filtre projet a gauche, boutons a droite
        with ui.row().classes("w-full items-center gap-2"):
            ui.label("Projet :").classes("text-sm text-gray-600")
            # Le select est rempli dynamiquement (peut etre vide si
            # aucun projet existe encore). Initialise vide ici, peuple
            # par refresh_project_filter().
            project_filter = ui.select(
                options={"": "Tous les projets"},
                value="",
                on_change=lambda _: refresh_list()
            ).classes("min-w-[200px]")

            # Pousse les boutons a droite
            ui.element("div").classes("flex-grow")

            ui.button("Projet", on_click=lambda: open_projects_dialog()) \
                .props("color=primary outline").classes("text-base")
            ui.button("+ Nouvelle pièce", on_click=lambda: open_new_part_dialog()) \
                .props("color=primary").classes("text-base")

        def refresh_project_filter():
            """Recharge les options du dropdown de filtre projet."""
            options = {"": "Tous les projets"}
            for proj in fetch_projects():
                options[proj["code"]] = f"{proj['code']} — {proj['description'] or '(sans description)'}"
            # Conserver la valeur actuelle si elle est encore valide
            current = project_filter.value
            project_filter.options = options
            if current not in options:
                project_filter.value = ""
            project_filter.update()

        refresh_project_filter()

        # Conteneur de la liste, rempli puis re-rempli par refresh_list()
        list_container = ui.column().classes("w-full gap-3")

        def refresh_list():
            """Vide puis re-rempli la liste depuis la base, en
            appliquant le filtre projet s'il est selectionne."""
            list_container.clear()
            code = project_filter.value or None
            parts = fetch_parts_full(project_code=code)

            if not parts:
                msg = ("Aucune pièce dans la base pour l'instant. "
                       "Cliquez sur « + Nouvelle pièce » ou exportez-en "
                       "une depuis FreeCAD.")
                if code:
                    msg = f"Aucune pièce pour le projet '{code}'."
                with list_container:
                    ui.label(msg) \
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
                # Le dropdown de filtre doit aussi connaitre le nouveau projet
                refresh_project_filter()

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
    action qui modifie la base (upload photo, changement de projet,
    de statut, de verrou), pour rafraichir la liste."""

    part_id = part["id"]
    locked = part["locked"]

    # Couleurs du badge statut selon la valeur
    status_colors = {
        "Init":  "bg-gray-100 text-gray-700",
        "Revue": "bg-amber-100 text-amber-800",
        "Asset": "bg-green-100 text-green-800",
    }
    status_cls = status_colors.get(part["status"], status_colors["Init"])

    with ui.card().classes("w-full p-4"):
        with ui.row().classes("w-full items-center gap-3 no-wrap"):

            # --- Verrou (icone cadenas, cliquable) ------------------
            # Toggle au clic. Visuellement distinct selon l'etat.
            lock_icon = "lock" if locked else "lock_open"
            lock_color = "text-red-600" if locked else "text-gray-400"

            def make_toggle_lock(pid=part_id):
                def handler():
                    ok, msg, _ = toggle_part_lock_db(pid)
                    if ok:
                        ui.notify(msg, type="info")
                        on_change()
                    else:
                        ui.notify(msg, type="negative")
                return handler

            ui.button(icon=lock_icon, on_click=make_toggle_lock()) \
                .props(f"flat round dense") \
                .classes(f"{lock_color} flex-shrink-0") \
                .tooltip("Verrouillée — cliquer pour déverrouiller"
                          if locked else "Cliquer pour verrouiller")

            # --- Nom + version (a cote) -----------------------------
            with ui.column().classes("gap-0 flex-grow"):
                with ui.row().classes("items-baseline gap-2 no-wrap"):
                    ui.label(part["part_name"]) \
                        .classes("text-base font-medium")
                    if part["version"]:
                        ui.label(part["version"]) \
                            .classes("text-xs font-mono text-gray-500")

                # --- Pastille projet (cliquable -> dialogue assign) -
                with ui.row().classes("items-center gap-1 no-wrap mt-1"):
                    proj_code = part["project_code"]
                    if proj_code:
                        proj_label = ui.label(proj_code) \
                            .classes("text-xs font-mono font-bold "
                                      "text-blue-700 bg-blue-50 "
                                      "px-2 py-0.5 rounded "
                                      "cursor-pointer hover:bg-blue-100")
                    else:
                        proj_label = ui.label("aucun projet") \
                            .classes("text-xs italic text-gray-400 "
                                      "px-2 py-0.5 rounded border "
                                      "border-dashed border-gray-300 "
                                      "cursor-pointer hover:border-blue-400 "
                                      "hover:text-blue-500")
                    if not locked:
                        proj_label.on("click",
                                       lambda p=part: open_assign_project_dialog(p, on_change))
                        proj_label.tooltip("Cliquer pour changer de projet")
                    else:
                        proj_label.classes("opacity-60")
                        proj_label.tooltip("Pièce verrouillée")

                    # --- Badge statut (cliquable -> cycle) ----------
                    status_label = ui.label(part["status"]) \
                        .classes(f"text-xs font-semibold {status_cls} "
                                  f"px-2 py-0.5 rounded")
                    if not locked:
                        status_label.classes("cursor-pointer hover:brightness-95")
                        # Cycle : Init -> Revue -> Asset -> Init
                        next_status = {"Init": "Revue",
                                        "Revue": "Asset",
                                        "Asset": "Init"}
                        def make_cycle(pid=part_id, current=part["status"]):
                            def handler():
                                ok, msg = set_part_status_db(
                                    pid, next_status[current])
                                if ok:
                                    ui.notify(msg, type="info")
                                    on_change()
                                else:
                                    ui.notify(msg, type="negative")
                            return handler
                        status_label.on("click", make_cycle())
                        status_label.tooltip(
                            f"Cliquer → {next_status[part['status']]}")
                    else:
                        status_label.classes("opacity-60")

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

            # --- Bouton stock (icone "inventory", a droite) --------
            # Ouvre un dialogue d'edition (quantite, location, supply,
            # fiche composant). Le verrou ne s'applique pas au stock.
            def make_open_stock(p=part):
                return lambda: open_stock_dialog(p, on_change)
            ui.button(icon="inventory_2",
                       on_click=make_open_stock()) \
                .props("flat round dense color=primary") \
                .classes("flex-shrink-0") \
                .tooltip("Gérer le stock")


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
#  DIALOGUE : ASSIGNER UN PROJET A UNE PIECE
# ======================================================================
# Fonction globale appelee depuis render_part_row. Construit un
# dialogue a la volee (un nouveau a chaque clic) qui liste les
# projets, met en evidence le projet actuel et le "dernier utilise",
# et permet aussi de creer un projet a la volee.
def open_assign_project_dialog(part: dict, on_change):
    projects = fetch_projects()
    last_used_id = fetch_last_used_project_id()
    current_id = part["id_project"]
    part_id = part["id"]
    part_name = part["part_name"]

    # Construit le dialogue. On le ferme et le detruit apres usage
    # pour eviter d'accumuler des dialogues a chaque ouverture.
    with ui.dialog() as dialog, ui.card().classes("min-w-[440px] max-w-[600px]"):
        ui.label(f"Assigner un projet à « {part_name} »") \
            .classes("text-lg font-medium")

        list_container = ui.column() \
            .classes("w-full gap-2 max-h-[360px] overflow-y-auto")

        # Formulaire de creation de projet, masque par defaut
        with ui.column().classes("w-full gap-2 mt-2") as creation_form:
            ui.label("Nouveau projet").classes("text-sm font-medium")
            desc_input = ui.textarea(
                placeholder="Description (optionnelle)") \
                .classes("w-full").props("autogrow rows=2")
            err_label = ui.label("") \
                .classes("text-red-600 text-sm min-h-[1.2em]")
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Annuler",
                          on_click=lambda: hide_creation()) \
                    .props("flat")
                ui.button("Créer et assigner",
                          on_click=lambda: confirm_create_and_assign()) \
                    .props("color=primary")
        creation_form.set_visibility(False)

        # Pied : "+ Nouveau projet" / Dissocier / Fermer
        with ui.row().classes("w-full justify-between gap-2 mt-2"):
            add_btn = ui.button("+ Nouveau projet",
                                 on_click=lambda: show_creation()) \
                .props("color=primary outline")
            with ui.row().classes("gap-2"):
                if current_id is not None:
                    ui.button("Dissocier",
                              on_click=lambda: do_assign(None)) \
                        .props("flat color=negative")
                ui.button("Fermer", on_click=dialog.close).props("flat")

        def render_options():
            list_container.clear()
            if not projects:
                with list_container:
                    ui.label("Aucun projet pour l'instant. "
                             "Créez-en un avec « + Nouveau projet ».") \
                        .classes("text-gray-500 text-sm text-center p-4")
                return
            for proj in projects:
                with list_container:
                    is_current = (proj["id"] == current_id)
                    is_last = (proj["id"] == last_used_id and not is_current)
                    # Bordure speciale si projet courant ou dernier utilise
                    extra = ""
                    if is_current:
                        extra = " border-2 border-blue-500"
                    elif is_last:
                        extra = " border-2 border-dashed border-amber-400"
                    with ui.card().classes(f"w-full p-3 cursor-pointer "
                                            f"hover:bg-blue-50 transition"
                                            + extra) as card:
                        with ui.row().classes("items-start gap-3 no-wrap"):
                            ui.label(proj["code"]) \
                                .classes("text-base font-mono font-bold "
                                          "text-blue-700 bg-blue-50 "
                                          "px-2 py-1 rounded flex-shrink-0")
                            with ui.column().classes("gap-0 flex-grow"):
                                desc = proj["description"] or "(aucune description)"
                                ui.label(desc) \
                                    .classes("text-sm text-stone-700 "
                                              "whitespace-pre-wrap")
                                if is_current:
                                    ui.label("Projet actuel") \
                                        .classes("text-xs text-blue-600 font-medium")
                                elif is_last:
                                    ui.label("Dernier utilisé") \
                                        .classes("text-xs text-amber-600")
                    # Clic sur la carte = assigner
                    card.on("click", lambda pid=proj["id"]: do_assign(pid))

        def do_assign(project_id):
            ok, msg = assign_project_to_part(part_id, project_id)
            if ok:
                ui.notify(msg, type="positive")
                dialog.close()
                on_change()
            else:
                ui.notify(msg, type="negative")

        def show_creation():
            desc_input.value = ""
            err_label.text = ""
            creation_form.set_visibility(True)
            add_btn.set_visibility(False)

        def hide_creation():
            creation_form.set_visibility(False)
            add_btn.set_visibility(True)

        def confirm_create_and_assign():
            # Cree le projet puis l'assigne immediatement a la piece
            ok, msg, code = create_project_in_db(desc_input.value or "")
            if not ok:
                err_label.text = msg
                return
            # Le projet vient d'etre cree : on retrouve son id en
            # cherchant par code (unique).
            import main
            with Session(main.engine) as s:
                proj = s.exec(
                    select(main.Project).where(main.Project.code == code)
                ).first()
                new_id = proj.id if proj else None
            if new_id is None:
                err_label.text = "Projet créé mais introuvable, abandon."
                return
            ok2, msg2 = assign_project_to_part(part_id, new_id)
            if ok2:
                ui.notify(f"Projet {code} créé et assigné.",
                          type="positive")
                dialog.close()
                on_change()
            else:
                ui.notify(msg2, type="negative")

        render_options()
        dialog.open()


# ======================================================================
#  DIALOGUE : EDITION DU STOCK D'UNE PIECE
# ======================================================================
# Ouvre un dialogue avec : quantite (number), location (input), supply
# (textarea), et un bouton d'upload de fiche composant. La fiche
# uploadee va dans /data-pistock/uploads/doc/ via l'endpoint REST
# /api/v1/parts/{id}/stock-doc (cf. JS listener "data-stock-doc").
def open_stock_dialog(part: dict, on_change):
    part_id = part["id"]
    part_name = part["part_name"]
    # Etat courant lu depuis la base (le 'part' passe peut etre stale
    # si le user a modifie le stock dans un autre onglet).
    stock = fetch_stock(part_id)

    with ui.dialog() as dialog, ui.card().classes("min-w-[480px] max-w-[600px]"):
        ui.label(f"Stock — « {part_name} »") \
            .classes("text-lg font-medium")

        # --- Champs editables -----------------------------------------
        qty_input = ui.number(label="Quantité",
                               value=stock["quantity"] or 0,
                               min=0, step=1, format="%d") \
            .classes("w-full")
        loc_input = ui.input(label="Location",
                              value=stock["location"] or "",
                              placeholder="ex: Tiroir A3, étagère 2") \
            .classes("w-full")
        supply_input = ui.textarea(
                label="Supply",
                value=stock["supply"] or "",
                placeholder="URL d'approvisionnement, fournisseur, "
                            "notes...") \
            .classes("w-full").props("autogrow rows=3")

        # --- Fiche composant -----------------------------------------
        # Si une fiche existe deja, on affiche un lien pour la
        # consulter. Le bouton "Choisir un fichier" ouvre le file
        # picker et l'upload se declenche automatiquement via le
        # listener JS global (data-stock-doc).
        with ui.column().classes("w-full mt-2"):
            ui.label("Fiche composant").classes("text-sm text-gray-600")
            doc_url = stock["doc_url"]
            if doc_url:
                # Lien vers la fiche actuelle (extrait juste le nom
                # affiche en retirant le repertoire et le prefixe).
                doc_name = doc_url.split("/")[-1]
                # On retire le suffixe _YYYYMMDD_HHMMSS pour l'affichage
                import re
                display_name = re.sub(r"_\d{8}_\d{6}", "", doc_name)
                with ui.row().classes("items-center gap-2"):
                    ui.html(
                        f'<a href="{doc_url}" target="_blank" '
                        f'class="text-blue-600 hover:underline text-sm">'
                        f'📄 {display_name}</a>'
                    )
                replace_label_text = "Remplacer la fiche"
            else:
                ui.label("(aucune fiche enregistrée)") \
                    .classes("text-sm text-gray-400 italic")
                replace_label_text = "Choisir un fichier"

            # Bouton d'upload : meme approche que pour les photos de
            # stock (HTML <label> + input cache, intercepte par le
            # listener JS global).
            ui.html(f'''
                <label class="inline-flex items-center gap-2 cursor-pointer
                              text-blue-600 hover:underline text-sm mt-1">
                    <span>📎 {replace_label_text}</span>
                    <input type="file"
                           accept=".pdf,.doc,.docx,.txt,.md,image/*"
                           style="display:none"
                           data-stock-doc="{part_id}">
                </label>
            ''')

        # --- Boutons OK / Annuler ------------------------------------
        with ui.row().classes("w-full justify-end gap-2 mt-3"):
            ui.button("Annuler", on_click=dialog.close).props("flat")
            ui.button("Enregistrer",
                      on_click=lambda: confirm_save()) \
                .props("color=primary")

        def confirm_save():
            ok, msg = save_stock(
                part_id,
                int(qty_input.value or 0),
                loc_input.value,
                supply_input.value
            )
            if ok:
                ui.notify(msg, type="positive")
                dialog.close()
                on_change()
            else:
                ui.notify(msg, type="negative")

        dialog.open()


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
