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
import json
import shutil
from datetime import datetime, timezone
from nicegui import ui, app, events
from sqlmodel import Session, select

# Module de traduction : voir i18n.py + locales/. Pour ajouter une
# chaine traduisible : wrapper avec _("..."), puis ajouter la paire
# msgid/msgstr dans locales/{lang}/LC_MESSAGES/messages.po.
from i18n import _, set_lang, get_lang, AVAILABLE_LANGS

# IMPORT TARDIF de main : on évite l'import circulaire en repoussant
# la résolution à l'intérieur des fonctions. Au moment où main.py
# exécute "import ui", main est encore en train d'être chargé et ses
# symboles (engine, Parts...) n'existent pas tous. En important au
# moment où la fonction tourne, on est sûr que main est complet.
def _db():
    """Helper qui renvoie tous les symboles dont on a besoin depuis main."""
    import main
    return main.engine, main.Parts, main.PLM, main.Stock, main.DATA_DIR


# ----------------------------------------------------------------------
#  CONFORMITE AGPLv3 : lien vers le code source
# ----------------------------------------------------------------------
# L'AGPLv3 exige que les utilisateurs accedant a l'application via le
# reseau puissent obtenir le code source. On expose un lien visible
# dans le header de chaque page pour s'acquitter de cette obligation.
SOURCE_CODE_URL = "https://github.com/GA3Dtech/PiStock"


def _apply_user_lang():
    """Lit la langue choisie par l'utilisateur (stockee dans le
    storage cote serveur, lie a un cookie de session) et l'applique
    globalement pour la requete en cours. A appeler en TOUT DEBUT de
    chaque @ui.page."""
    try:
        # On utilise app.storage.user et NON app.storage.browser :
        # browser est un cookie signe dont la valeur est posee dans
        # les headers HTTP, donc en lecture seule en dehors de la
        # construction initiale de la reponse. user est cote serveur,
        # modifiable de partout (event handlers compris).
        lang = app.storage.user.get("lang", "en")
    except Exception:
        lang = "en"
    set_lang(lang)


def render_app_header(title_key: str):
    """En-tete commun aux pages : titre a gauche, selecteur de langue
    et lien vers le code source a droite (obligation AGPLv3).

    'title_key' est un msgid qui sera traduit via _()."""
    with ui.header().classes("bg-stone-800 text-white shadow"):
        with ui.row().classes("w-full items-center no-wrap gap-3"):
            ui.label(_(title_key)).classes("text-xl font-medium")
            ui.element("div").classes("flex-grow")  # spacer

            # --- Selecteur de langue --------------------------------
            # Toggle EN/FR. Au changement : on stocke la preference
            # cote navigateur et on recharge la page pour appliquer.
            current = get_lang()
            lang_options = {code: code.upper()
                             for code, _label in AVAILABLE_LANGS}

            def on_lang_change(e):
                new_lang = e.value
                # app.storage.user (cote serveur) au lieu de
                # app.storage.browser (cookie signe, read-only hors
                # construction de reponse HTTP).
                app.storage.user["lang"] = new_lang
                # Reload pour reconstruire toute la page dans la
                # nouvelle langue. Plus simple et fiable qu'un rebuild
                # incremental qui demanderait de tracker tous les
                # widgets contenant du texte.
                ui.navigate.reload()

            ui.toggle(lang_options, value=current,
                       on_change=on_lang_change) \
                .props("color=white dense").classes("text-sm")

            ui.link(_("Source code (AGPLv3)"),
                    SOURCE_CODE_URL,
                    new_tab=True) \
                .classes("text-stone-300 hover:text-white "
                          "text-sm no-underline")


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
            # IMPORTANT : on utilise le helper main._get_current_plm
            # pour rester coherent avec le reste du backend. Sinon
            # le dashboard afficherait la plus recente meme quand
            # l'utilisateur a marque une autre revision comme "principale".
            latest_plm = main._get_current_plm(session, p.id)
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
    """Detail d'une piece pour la page viewer 3D. Renvoie la revision
    "courante" (is_main si marquee, sinon la plus recente)."""
    engine, Parts, PLM, _, _ = _db()
    import main
    with Session(engine) as session:
        p = session.get(Parts, part_id)
        if p is None:
            return None
        # Utilise le helper centralise dans main pour rester coherent
        # avec le reste du backend.
        latest_plm = main._get_current_plm(session, p.id)
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


def fetch_revisions(part_id: int):
    """Liste toutes les revisions PLM d'une piece, plus recente en
    premier. Chaque entree a 'is_current' = True pour celle qui est
    affichee par defaut (is_main ou plus recente par timestamp)."""
    engine, _, PLM, _, _ = _db()
    import main
    with Session(engine) as session:
        revisions = session.exec(
            select(PLM).where(PLM.id_parts == part_id)
            .order_by(PLM.timestamp.desc())
        ).all()
        current = main._get_current_plm(session, part_id)
        current_id = current.id if current else None
        return [
            {
                "id": r.id,
                "version": r.version,
                "timestamp": r.timestamp.isoformat(),
                "author": r.author,
                "is_main": r.is_main,
                "is_current": (r.id == current_id),
                "glb_url": (f"/{r.path_2_3dglb}" if r.path_2_3dglb else None),
                "thumbnail_url": (f"/{r.path_2_thumbnail}"
                                   if r.path_2_thumbnail else None),
            }
            for r in revisions
        ]


def delete_revision_db(plm_id: int):
    """Supprime une revision (ligne + fichiers disque). Verifie le
    verrou de la piece parente. Retourne (ok, msg)."""
    engine, Parts_cls, PLM_cls, _, DATA_DIR = _db()
    with Session(engine) as session:
        plm = session.get(PLM_cls, plm_id)
        if plm is None:
            return (False, "Révision introuvable.")
        part = session.get(Parts_cls, plm.id_parts)
        if part is not None and part.locked:
            return (False,
                    f"Pièce '{part.part_name}' verrouillée — "
                    f"déverrouillez avant de supprimer.")

        # Suppression des fichiers (best-effort, ignore les erreurs)
        for rel_path in (plm.path_2_cadfile, plm.path_2_thumbnail,
                          plm.path_2_3dglb):
            if not rel_path:
                continue
            abs_path = os.path.join(DATA_DIR, rel_path)
            try:
                if os.path.isfile(abs_path):
                    os.remove(abs_path)
            except OSError:
                pass

        session.delete(plm)
        session.commit()
        return (True, f"Révision '{plm.version}' supprimée.")


def set_revision_main_db(plm_id: int):
    """Marque cette revision principale (et demarque les autres).
    Verifie le verrou. Retourne (ok, msg)."""
    engine, Parts_cls, PLM_cls, _, _ = _db()
    with Session(engine) as session:
        plm = session.get(PLM_cls, plm_id)
        if plm is None:
            return (False, "Révision introuvable.")
        part = session.get(Parts_cls, plm.id_parts)
        if part is not None and part.locked:
            return (False,
                    f"Pièce '{part.part_name}' verrouillée — "
                    f"déverrouillez avant de modifier.")
        # Demarque toutes les autres de la meme piece
        others = session.exec(
            select(PLM_cls)
            .where(PLM_cls.id_parts == plm.id_parts)
            .where(PLM_cls.id != plm_id)
            .where(PLM_cls.is_main == True)  # noqa: E712
        ).all()
        for o in others:
            o.is_main = False
            session.add(o)
        plm.is_main = True
        session.add(plm)
        session.commit()
        return (True, f"Version '{plm.version}' définie comme principale.")


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
    # Applique la langue choisie par l'utilisateur AVANT de construire
    # quoi que ce soit (les premiers appels a _() en dependent).
    _apply_user_lang()

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
    render_app_header("PiStock — Catalog")

    # Conteneur principal centre, largeur max
    with ui.column().classes("w-full max-w-5xl mx-auto p-4 gap-4"):

        # Barre d'actions : filtre projet a gauche, boutons a droite
        with ui.row().classes("w-full items-center gap-2"):
            ui.label(_("Project:")).classes("text-sm text-gray-600")
            # Le select est rempli dynamiquement (peut etre vide si
            # aucun projet existe encore). Initialise vide ici, peuple
            # par refresh_project_filter().
            project_filter = ui.select(
                options={"": _("All projects")},
                value="",
                on_change=lambda _: refresh_list()
            ).classes("min-w-[200px]")

            # Pousse les boutons a droite
            ui.element("div").classes("flex-grow")

            ui.button(_("Project"), on_click=lambda: open_projects_dialog()) \
                .props("color=primary outline").classes("text-base")
            ui.button(_("+ New part"), on_click=lambda: open_new_part_dialog()) \
                .props("color=primary").classes("text-base")

        def refresh_project_filter():
            """Recharge les options du dropdown de filtre projet."""
            options = {"": _("All projects")}
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
    """Page viewer 3D pour une piece donnee, avec liste des
    revisions PLM sous le viewer."""
    _apply_user_lang()
    part = fetch_part_detail(part_id)

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

    render_app_header("PiStock — 3D View")

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
