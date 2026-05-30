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


def _register_pwa():
    """Injecte les balises PWA dans le <head> : manifest, theme-color,
    icone et enregistrement du service worker. A appeler depuis chaque
    @ui.page pour que l'app soit installable.

    Le service worker n'est actif qu'en HTTPS ou sur localhost (limite
    standard des navigateurs). Sur un Pi accédé via http://192.168.x.y
    depuis un mobile, le SW ne s'enregistrera pas, mais le manifest
    et les meta tags resteront utiles."""
    ui.add_head_html('''
        <link rel="manifest" href="/static/manifest.json">
        <meta name="theme-color" content="#292524">
        <link rel="icon" href="/static/icon-192.png" type="image/png">
        <link rel="apple-touch-icon" href="/static/icon-192.png">
        <script>
        if ('serviceWorker' in navigator) {
            window.addEventListener('load', () => {
                navigator.serviceWorker.register('/static/service-worker.js')
                    .then(reg => console.log('PiStock SW registered:', reg.scope))
                    .catch(err => console.warn('PiStock SW failed:', err));
            });
        }
        </script>
    ''')


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


# ----------------------------------------------------------------------
#  HELPERS DB : BOM (nomenclatures)
# ----------------------------------------------------------------------
# Pattern identique aux autres entites : on accede directement a la
# session SQLModel (pas via HTTP). main.Bom / main.BomLine sont
# importes a la demande pour eviter l'import circulaire.

def fetch_boms(project_code: str | None = None):
    """Liste les BOMs avec compteur de lignes."""
    import main
    engine = main.engine
    with Session(engine) as session:
        query = select(main.Bom).order_by(main.Bom.code)
        if project_code:
            project = session.exec(
                select(main.Project)
                .where(main.Project.code == project_code)
            ).first()
            if project is None:
                return []
            query = query.where(main.Bom.id_project == project.id)
        boms = session.exec(query).all()
        projects_by_id = {
            p.id: p.code
            for p in session.exec(select(main.Project)).all()
        }
        result = []
        for b in boms:
            lines = session.exec(
                select(main.BomLine).where(main.BomLine.id_bom == b.id)
            ).all()
            result.append({
                "id": b.id,
                "code": b.code,
                "description": b.description,
                "id_project": b.id_project,
                "project_code": projects_by_id.get(b.id_project),
                "line_count": len(lines),
            })
        return result


def fetch_bom_detail(bom_id: int):
    """Detail d'une BOM + ses lignes avec noms de pieces."""
    import main
    with Session(main.engine) as session:
        bom = session.get(main.Bom, bom_id)
        if bom is None:
            return None
        lines_rows = session.exec(
            select(main.BomLine)
            .where(main.BomLine.id_bom == bom_id)
            .order_by(main.BomLine.id)
        ).all()
        # Pre-charge les parts
        part_ids = {l.id_parts for l in lines_rows}
        parts_by_id = {
            p.id: p for p in session.exec(
                select(main.Parts).where(main.Parts.id.in_(part_ids))
            ).all()
        } if part_ids else {}
        project_code = None
        if bom.id_project is not None:
            proj = session.get(main.Project, bom.id_project)
            project_code = proj.code if proj else None
        return {
            "id": bom.id,
            "code": bom.code,
            "description": bom.description,
            "id_project": bom.id_project,
            "project_code": project_code,
            "lines": [
                {
                    "id": l.id,
                    "id_parts": l.id_parts,
                    "part_name": (parts_by_id[l.id_parts].part_name
                                   if l.id_parts in parts_by_id else "?"),
                    "quantity": l.quantity,
                }
                for l in lines_rows
            ],
        }


def create_bom_db(description: str, id_project: int | None):
    """Cree une BOM. Retourne (ok, msg, code)."""
    import main
    description = (description or "").strip() or None
    with Session(main.engine) as session:
        if id_project is not None:
            if session.get(main.Project, id_project) is None:
                return (False, "Projet introuvable.", None)
        try:
            code = main._next_bom_code(session)
        except Exception as e:
            return (False, str(e), None)
        bom = main.Bom(code=code, description=description,
                        id_project=id_project)
        session.add(bom)
        session.commit()
        session.refresh(bom)
        return (True, f"BOM '{code}' créée.", code)


def delete_bom_db(bom_id: int):
    """Supprime une BOM et ses lignes (cascade manuelle)."""
    import main
    with Session(main.engine) as session:
        bom = session.get(main.Bom, bom_id)
        if bom is None:
            return (False, "BOM introuvable.")
        lines = session.exec(
            select(main.BomLine).where(main.BomLine.id_bom == bom_id)
        ).all()
        for line in lines:
            session.delete(line)
        session.delete(bom)
        session.commit()
        return (True, f"BOM '{bom.code}' supprimée.")


def add_bom_line_db(bom_id: int, part_id: int, quantity: int):
    """Ajoute une ligne. Si la part est deja presente, cumule la
    quantite. Retourne (ok, msg)."""
    if quantity is None or quantity <= 0:
        return (False, "La quantité doit être > 0.")
    import main
    with Session(main.engine) as session:
        if session.get(main.Bom, bom_id) is None:
            return (False, "BOM introuvable.")
        if session.get(main.Parts, part_id) is None:
            return (False, "Pièce introuvable.")
        existing = session.exec(
            select(main.BomLine)
            .where(main.BomLine.id_bom == bom_id)
            .where(main.BomLine.id_parts == part_id)
        ).first()
        if existing:
            existing.quantity += int(quantity)
            session.add(existing)
            session.commit()
            return (True, f"Quantité cumulée à {existing.quantity}.")
        session.add(main.BomLine(id_bom=bom_id, id_parts=part_id,
                                  quantity=int(quantity)))
        session.commit()
        return (True, "Ligne ajoutée.")


def update_bom_line_db(line_id: int, quantity: int):
    """Met a jour la quantite. Retourne (ok, msg)."""
    if quantity is None or quantity <= 0:
        return (False, "La quantité doit être > 0.")
    import main
    with Session(main.engine) as session:
        line = session.get(main.BomLine, line_id)
        if line is None:
            return (False, "Ligne introuvable.")
        line.quantity = int(quantity)
        session.add(line)
        session.commit()
        return (True, "Quantité mise à jour.")


def delete_bom_line_db(line_id: int):
    """Supprime une ligne. Retourne (ok, msg)."""
    import main
    with Session(main.engine) as session:
        line = session.get(main.BomLine, line_id)
        if line is None:
            return (False, "Ligne introuvable.")
        session.delete(line)
        session.commit()
        return (True, "Ligne supprimée.")


def bom_stock_apply(bom_id: int, factor: int, direction: str):
    """Applique 'factor' fois la BOM sur le stock.
    direction='add' : incremente. direction='sub' : decremente, refus
    atomique si stock insuffisant.
    Retourne (ok, msg, shortages_list)."""
    if factor is None or factor <= 0:
        return (False, "Le facteur doit être > 0.", None)
    import main
    with Session(main.engine) as session:
        bom = session.get(main.Bom, bom_id)
        if bom is None:
            return (False, "BOM introuvable.", None)
        lines = session.exec(
            select(main.BomLine).where(main.BomLine.id_bom == bom_id)
        ).all()
        if not lines:
            return (False, "La BOM est vide.", None)

        if direction == "sub":
            # Verification atomique : on calcule TOUS les manques avant
            # de modifier quoi que ce soit.
            shortages = []
            for line in lines:
                stock = session.exec(
                    select(main.Stock)
                    .where(main.Stock.id_parts == line.id_parts)
                ).first()
                current = stock.quantity if stock else 0
                needed = line.quantity * factor
                if current < needed:
                    part = session.get(main.Parts, line.id_parts)
                    shortages.append({
                        "part_name": part.part_name if part else "?",
                        "needed": needed,
                        "available": current,
                        "missing": needed - current,
                    })
            if shortages:
                return (False, "Stock insuffisant.", shortages)

        # Application des changements
        for line in lines:
            stock = main._get_or_create_stock(session, line.id_parts)
            delta = line.quantity * factor
            if direction == "sub":
                delta = -delta
            stock.quantity += delta
            session.add(stock)
        session.commit()
        verb = "ajoutée" if direction == "add" else "retirée"
        return (True, f"BOM {verb} ×{factor}.", None)


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
    _register_pwa()

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

            // ---- Listener pour les BOUTONS CAPTURE CAMERA ----
            // Cible : a[data-pistock-capture="{part_id}"]
            // Au clic : appelle pistockCapturePhoto(part_id) qui ouvre
            // un dialogue avec le live preview de la camera.
            document.addEventListener('click', function(e) {
                const trigger = e.target.closest('[data-pistock-capture]');
                if (!trigger) return;
                e.preventDefault();
                const partId = trigger.dataset.pistockCapture;
                pistockCapturePhoto(parseInt(partId, 10));
            });
        }

        // ===================================================
        //  FONCTION DE CAPTURE PHOTO VIA getUserMedia
        // ===================================================
        // Ouvre un dialogue plein ecran avec un live preview de la
        // camera. L'utilisateur clique "Capturer" -> aperçu de la
        // photo + boutons "Enregistrer" / "Reprendre". L'envoi se
        // fait vers POST /api/v1/parts/{id}/stock-photo (le meme
        // endpoint que pour l'upload fichier), puis reload de la page.
        window.pistockCapturePhoto = async function(partId) {
            // Verification : navigator.mediaDevices n'est dispo que
            // sur les contextes HTTPS (sauf localhost). Sur du HTTP
            // depuis une autre machine, on previent l'utilisateur.
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                alert(
                    "La caméra n'est accessible qu'en HTTPS ou en localhost.\\n\\n" +
                    "Pour un accès depuis une autre machine, configurez " +
                    "HTTPS (certificat auto-signé ou reverse-proxy)."
                );
                return;
            }

            // --- Construction du dialogue en JS pur --------------
            // (pas de NiceGUI ici, on garde tout cote client pour
            // simplifier la gestion du media stream)
            const overlay = document.createElement('div');
            overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.85);' +
                'display:flex;align-items:center;justify-content:center;z-index:9999;' +
                'padding:20px;';
            const dialog = document.createElement('div');
            dialog.style.cssText = 'background:white;border-radius:12px;padding:20px;' +
                'max-width:95vw;max-height:95vh;display:flex;flex-direction:column;' +
                'align-items:center;gap:12px;';
            dialog.innerHTML =
                '<h3 style="margin:0;font-size:18px;font-weight:600;">' +
                'Capture photo — pièce ' + partId + '</h3>' +
                '<div style="position:relative;">' +
                '  <video id="pistock-cam-video" autoplay playsinline muted ' +
                '         style="max-width:80vw;max-height:60vh;border-radius:8px;' +
                '         background:#000;"></video>' +
                '  <img id="pistock-cam-preview" style="display:none;max-width:80vw;' +
                '       max-height:60vh;border-radius:8px;">' +
                '</div>' +
                '<canvas id="pistock-cam-canvas" style="display:none;"></canvas>' +
                '<div id="pistock-cam-status" style="font-size:13px;color:#6b7280;' +
                '     min-height:20px;"></div>' +
                '<div id="pistock-cam-actions" style="display:flex;gap:10px;">' +
                '  <button id="pistock-cam-capture-btn" ' +
                '          style="padding:10px 20px;background:#2563eb;color:white;' +
                '          border:none;border-radius:6px;font-size:14px;cursor:pointer;">' +
                '    📷 Capturer</button>' +
                '  <button id="pistock-cam-retake-btn" style="display:none;' +
                '          padding:10px 20px;background:#6b7280;color:white;border:none;' +
                '          border-radius:6px;font-size:14px;cursor:pointer;">' +
                '    ↻ Reprendre</button>' +
                '  <button id="pistock-cam-save-btn" style="display:none;' +
                '          padding:10px 20px;background:#16a34a;color:white;border:none;' +
                '          border-radius:6px;font-size:14px;cursor:pointer;">' +
                '    💾 Enregistrer</button>' +
                '  <button id="pistock-cam-cancel-btn" ' +
                '          style="padding:10px 20px;background:#dc2626;color:white;' +
                '          border:none;border-radius:6px;font-size:14px;cursor:pointer;">' +
                '    ✕ Annuler</button>' +
                '</div>';
            overlay.appendChild(dialog);
            document.body.appendChild(overlay);

            const video = document.getElementById('pistock-cam-video');
            const canvas = document.getElementById('pistock-cam-canvas');
            const preview = document.getElementById('pistock-cam-preview');
            const status = document.getElementById('pistock-cam-status');
            const captureBtn = document.getElementById('pistock-cam-capture-btn');
            const retakeBtn = document.getElementById('pistock-cam-retake-btn');
            const saveBtn = document.getElementById('pistock-cam-save-btn');
            const cancelBtn = document.getElementById('pistock-cam-cancel-btn');

            let stream = null;
            let capturedBlob = null;

            const cleanup = () => {
                if (stream) {
                    stream.getTracks().forEach(t => t.stop());
                    stream = null;
                }
                overlay.remove();
            };

            // Lance le stream camera. facingMode='environment' = camera
            // arriere sur mobile (la plus utile pour photographier
            // une piece devant soi). Fallback sur 'user' si refuse.
            try {
                status.textContent = "Démarrage de la caméra…";
                stream = await navigator.mediaDevices.getUserMedia({
                    video: {
                        facingMode: { ideal: 'environment' },
                        width: { ideal: 1920 },
                        height: { ideal: 1080 }
                    },
                    audio: false
                });
                video.srcObject = stream;
                status.textContent = "Cadrez la pièce puis cliquez sur « Capturer »";
            } catch (err) {
                status.textContent = "";
                let msg = "Caméra inaccessible : " + (err.message || err.name);
                if (err.name === 'NotAllowedError') {
                    msg = "Accès caméra refusé. Autorisez-le dans les " +
                          "paramètres du navigateur.";
                } else if (err.name === 'NotFoundError') {
                    msg = "Aucune caméra détectée sur cet appareil.";
                }
                alert(msg);
                cleanup();
                return;
            }

            // Clic "Capturer" -> dessine la frame courante du video
            // dans le canvas, convertit en blob JPEG, affiche l'aperçu.
            captureBtn.addEventListener('click', () => {
                canvas.width = video.videoWidth;
                canvas.height = video.videoHeight;
                const ctx = canvas.getContext('2d');
                ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
                canvas.toBlob((blob) => {
                    if (!blob) {
                        alert("Échec de la capture.");
                        return;
                    }
                    capturedBlob = blob;
                    preview.src = URL.createObjectURL(blob);
                    video.style.display = 'none';
                    preview.style.display = 'block';
                    captureBtn.style.display = 'none';
                    retakeBtn.style.display = 'inline-block';
                    saveBtn.style.display = 'inline-block';
                    status.textContent = "Aperçu — Enregistrer ou Reprendre ?";
                }, 'image/jpeg', 0.85);
            });

            // Clic "Reprendre" -> on retourne au live preview
            retakeBtn.addEventListener('click', () => {
                if (preview.src) URL.revokeObjectURL(preview.src);
                preview.src = '';
                capturedBlob = null;
                video.style.display = 'block';
                preview.style.display = 'none';
                captureBtn.style.display = 'inline-block';
                retakeBtn.style.display = 'none';
                saveBtn.style.display = 'none';
                status.textContent = "Cadrez la pièce puis cliquez sur « Capturer »";
            });

            // Clic "Enregistrer" -> POST vers l'endpoint stock-photo
            saveBtn.addEventListener('click', async () => {
                if (!capturedBlob) return;
                status.textContent = "Envoi en cours…";
                saveBtn.disabled = true;
                retakeBtn.disabled = true;
                const formData = new FormData();
                // Le serveur accepte n'importe quel nom de fichier ; on
                // utilise un nom qui indique l'origine (camera) + la date.
                const ts = new Date().toISOString().replace(/[:.]/g, '-');
                formData.append('photo', capturedBlob, 'camera_' + ts + '.jpg');
                try {
                    const response = await fetch(
                        '/api/v1/parts/' + partId + '/stock-photo',
                        { method: 'POST', body: formData }
                    );
                    if (!response.ok) {
                        const err = await response.json().catch(() => ({}));
                        alert("Erreur upload : " + (err.detail || response.status));
                        saveBtn.disabled = false;
                        retakeBtn.disabled = false;
                        status.textContent = "Échec — vous pouvez réessayer";
                        return;
                    }
                    cleanup();
                    window.location.reload();
                } catch (err) {
                    alert("Erreur réseau : " + err.message);
                    saveBtn.disabled = false;
                    retakeBtn.disabled = false;
                    status.textContent = "Échec — vous pouvez réessayer";
                }
            });

            // Clic "Annuler" -> ferme le dialogue, coupe la camera
            cancelBtn.addEventListener('click', cleanup);
            // Echappe = Annuler aussi
            const escHandler = (e) => {
                if (e.key === 'Escape') {
                    cleanup();
                    document.removeEventListener('keydown', escHandler);
                }
            };
            document.addEventListener('keydown', escHandler);
        };
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
            ui.button("BOMs", on_click=lambda: open_boms_dialog()) \
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
        # Photo existante : 📁 (file) ou 📷 (camera) à droite
        ui.html(f'''
            <div class="flex flex-col items-center gap-1 flex-shrink-0">
                <div class="w-20 h-20 bg-stone-100 rounded-lg flex items-center justify-center overflow-hidden">
                    <img src="{part["stock_img_url"]}"
                         alt="Photo stock"
                         class="w-full h-full object-contain">
                </div>
                <div class="flex gap-2 text-xs">
                    <label class="text-blue-600 cursor-pointer hover:underline">
                        📁
                        <input type="file" accept="image/*" style="display:none"
                               data-stock-upload="{part_id}">
                    </label>
                    <a class="text-blue-600 cursor-pointer hover:underline"
                       data-pistock-capture="{part_id}"
                       title="Prendre une photo">📷</a>
                </div>
            </div>
        ''')
    else:
        # Pas de photo : gros bouton pour fichier + petit lien camera
        ui.html(f'''
            <div class="flex flex-col items-center gap-1 flex-shrink-0">
                <label class="cursor-pointer" title="Ajouter une photo de la pièce en stock">
                    <div class="w-20 h-20 border-2 border-dashed border-stone-300 rounded-lg
                                flex flex-col items-center justify-center gap-0
                                text-stone-500 transition
                                hover:border-blue-500 hover:text-blue-500 hover:bg-blue-50">
                        <span class="text-2xl leading-none">📁</span>
                        <span class="text-xs mt-1">Fichier</span>
                    </div>
                    <input type="file" accept="image/*" style="display:none"
                           data-stock-upload="{part_id}">
                </label>
                <a class="text-xs text-blue-600 cursor-pointer hover:underline"
                   data-pistock-capture="{part_id}"
                   title="Prendre une photo avec la caméra">📷 Caméra</a>
            </div>
        ''')


# ======================================================================
#  PAGE : VIEWER 3D
# ======================================================================
@ui.page("/part/{part_id}")
def part_page(part_id: int):
    """Page viewer 3D pour une piece donnee, avec liste des
    revisions PLM sous le viewer."""
    _apply_user_lang()
    _register_pwa()
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
#  DIALOGUE : LISTE DES BOMs (+ création + actions stock)
# ======================================================================
def open_boms_dialog():
    """Dialogue principal des BOMs : liste, création, et actions de
    stock (ajouter/retirer N fois). Cliquer sur une ligne ouvre le
    sous-dialogue d'édition des lignes de la BOM."""

    with ui.dialog() as dialog, ui.card().classes("min-w-[760px] max-w-[900px]"):
        ui.label("BOMs (nomenclatures)").classes("text-lg font-medium")

        list_container = ui.column() \
            .classes("w-full gap-2 max-h-[420px] overflow-y-auto")

        # --- Formulaire de création (masqué par défaut) ---------------
        with ui.column().classes("w-full gap-2 mt-2") as creation_form:
            ui.label("Nouvelle BOM").classes("text-sm font-medium")
            desc_input = ui.textarea(
                placeholder="Description (optionnelle)") \
                .classes("w-full").props("autogrow rows=2")
            # Sélecteur projet (optionnel) : permet de rattacher la
            # BOM à un projet existant directement à la création.
            project_select = ui.select(
                options={0: "(Sans projet)"},  # peuplé dans render()
                value=0, label="Projet (optionnel)"
            ).classes("w-full")
            err_label = ui.label("") \
                .classes("text-red-600 text-sm min-h-[1.2em]")
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button(_("Cancel"),
                          on_click=lambda: hide_creation()) \
                    .props("flat")
                ui.button(_("Create"),
                          on_click=lambda: confirm_create()) \
                    .props("color=primary")
        creation_form.set_visibility(False)

        # --- Pied : "+ Nouvelle BOM" et "Fermer" ---------------------
        with ui.row().classes("w-full justify-between gap-2 mt-2"):
            add_btn = ui.button("+ Nouvelle BOM",
                                 on_click=lambda: show_creation()) \
                .props("color=primary outline")
            ui.button(_("Close"), on_click=dialog.close).props("flat")

        def show_creation():
            desc_input.value = ""
            project_select.value = 0
            err_label.text = ""
            # Recharge la liste des projets dans le selecteur
            options = {0: "(Sans projet)"}
            for proj in fetch_projects():
                options[proj["id"]] = f"{proj['code']} — {(proj['description'] or '')[:30]}"
            project_select.options = options
            project_select.update()
            creation_form.set_visibility(True)
            add_btn.set_visibility(False)

        def hide_creation():
            creation_form.set_visibility(False)
            add_btn.set_visibility(True)

        def confirm_create():
            id_proj = project_select.value or None
            if id_proj == 0:
                id_proj = None
            ok, msg, code = create_bom_db(desc_input.value or "", id_proj)
            if not ok:
                err_label.text = msg
                return
            ui.notify(msg, type="positive")
            hide_creation()
            render_boms_list()

        def render_boms_list():
            list_container.clear()
            boms = fetch_boms()
            if not boms:
                with list_container:
                    ui.label("Aucune BOM. Cliquez sur « + Nouvelle BOM »"
                             " pour en créer une.") \
                        .classes("text-gray-500 text-sm text-center p-4")
                return
            for bom in boms:
                with list_container:
                    render_bom_row(bom)

        def render_bom_row(bom):
            with ui.card().classes("w-full p-3"):
                with ui.row().classes("items-center gap-3 w-full no-wrap"):
                    # Code
                    ui.label(bom["code"]) \
                        .classes("text-sm font-mono font-bold "
                                  "text-blue-700 bg-blue-50 "
                                  "px-2 py-1 rounded flex-shrink-0")
                    # Description + projet
                    with ui.column().classes("gap-0 flex-grow"):
                        desc = bom["description"] or "(sans description)"
                        ui.label(desc).classes("text-sm font-medium")
                        meta = f"{bom['line_count']} ligne(s)"
                        if bom["project_code"]:
                            meta += f" • projet {bom['project_code']}"
                        ui.label(meta).classes("text-xs text-gray-500")

                    # Bouton "Éditer"
                    def make_edit(bid=bom["id"]):
                        def handler():
                            dialog.close()
                            open_bom_edit_dialog(bid)
                        return handler
                    ui.button(icon="edit", on_click=make_edit()) \
                        .props("flat round dense color=primary") \
                        .tooltip("Éditer les lignes")

                    # Stock +/- (avec mini-prompt pour le facteur)
                    def make_stock_apply(bid=bom["id"],
                                          direction="add"):
                        def handler():
                            open_bom_stock_dialog(bid, direction,
                                                   on_done=render_boms_list)
                        return handler
                    ui.button(icon="add", on_click=make_stock_apply(
                                bid=bom["id"], direction="add")) \
                        .props("flat round dense color=positive") \
                        .tooltip("Ajouter au stock")
                    ui.button(icon="remove", on_click=make_stock_apply(
                                bid=bom["id"], direction="sub")) \
                        .props("flat round dense color=warning") \
                        .tooltip("Retirer du stock")

                    # Suppression (avec confirmation)
                    def make_delete(bid=bom["id"], code=bom["code"]):
                        def handler():
                            confirm_delete_bom(bid, code,
                                                on_done=render_boms_list)
                        return handler
                    ui.button(icon="delete", on_click=make_delete()) \
                        .props("flat round dense color=negative") \
                        .tooltip("Supprimer cette BOM")

        render_boms_list()
        dialog.open()


def confirm_delete_bom(bom_id: int, code: str, on_done):
    """Dialogue de confirmation pour la suppression d'une BOM."""
    with ui.dialog() as d, ui.card():
        ui.label(f"Supprimer la BOM « {code} » ?") \
            .classes("text-base font-medium")
        ui.label("Cette action supprime aussi toutes ses lignes. "
                  "Le stock des pièces n'est PAS modifié.") \
            .classes("text-sm text-gray-600 max-w-[400px]")
        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button(_("Cancel"), on_click=d.close).props("flat")
            def confirm():
                ok, msg = delete_bom_db(bom_id)
                ui.notify(msg, type="positive" if ok else "negative")
                d.close()
                if ok:
                    on_done()
            ui.button(_("Delete"), on_click=confirm) \
                .props("color=negative")
    d.open()


def open_bom_stock_dialog(bom_id: int, direction: str, on_done):
    """Mini-dialogue qui demande le facteur (combien de fois appliquer
    la BOM) puis applique. direction='add' ou 'sub'."""
    is_add = (direction == "add")
    title = "Ajouter au stock" if is_add else "Retirer du stock"
    verb_color = "positive" if is_add else "warning"

    detail = fetch_bom_detail(bom_id)
    if detail is None:
        ui.notify("BOM introuvable.", type="negative")
        return
    if not detail["lines"]:
        ui.notify("Cette BOM est vide.", type="warning")
        return

    with ui.dialog() as d, ui.card().classes("min-w-[440px]"):
        ui.label(f"{title} — BOM {detail['code']}") \
            .classes("text-lg font-medium")
        ui.label("Combien de fois ?").classes("text-sm text-gray-600")
        factor_input = ui.number(value=1, min=1, step=1, format="%d") \
            .classes("w-full")
        # Récap des changements à venir
        ui.label("Conséquences sur le stock :").classes("text-sm font-medium mt-2")
        recap = ui.column().classes("gap-1")
        def refresh_recap():
            recap.clear()
            f = int(factor_input.value or 1)
            sign = "+" if is_add else "−"
            with recap:
                for line in detail["lines"]:
                    delta = line["quantity"] * f
                    ui.label(f"  {sign}{delta} × {line['part_name']}") \
                        .classes("text-xs font-mono text-gray-700")
        factor_input.on("update:model-value", lambda _: refresh_recap())
        refresh_recap()

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button(_("Cancel"), on_click=d.close).props("flat")
            def confirm():
                f = int(factor_input.value or 1)
                ok, msg, shortages = bom_stock_apply(bom_id, f, direction)
                if not ok and shortages:
                    # Construit un message detaille des manques
                    lines = [f"  • {s['part_name']} : besoin {s['needed']}, "
                             f"dispo {s['available']} (manque {s['missing']})"
                             for s in shortages]
                    full_msg = f"{msg}\n" + "\n".join(lines)
                    ui.notify(full_msg, type="negative",
                               multi_line=True,
                               position="center", timeout=8000)
                    return
                ui.notify(msg, type="positive" if ok else "negative")
                if ok:
                    d.close()
                    on_done()
            ui.button(_("Save"), on_click=confirm).props(f"color={verb_color}")
    d.open()


# ======================================================================
#  DIALOGUE : ÉDITION DES LIGNES D'UNE BOM
# ======================================================================
def open_bom_edit_dialog(bom_id: int):
    """Dialogue d'édition des lignes d'une BOM : ajouter, modifier
    quantité (inline), supprimer."""
    detail = fetch_bom_detail(bom_id)
    if detail is None:
        ui.notify("BOM introuvable.", type="negative")
        return

    # Charger toutes les pieces pour le selecteur d'ajout
    parts = fetch_parts_full()

    with ui.dialog() as dialog, ui.card().classes("min-w-[640px] max-w-[800px]"):
        # En-tête : code + description
        header_text = f"BOM {detail['code']}"
        if detail["description"]:
            header_text += f" — {detail['description']}"
        ui.label(header_text).classes("text-lg font-medium")

        # Liste des lignes
        lines_container = ui.column().classes("w-full gap-1")

        def render_lines():
            """Recharge les données et redessine les lignes."""
            nonlocal detail
            detail = fetch_bom_detail(bom_id)
            lines_container.clear()
            if not detail["lines"]:
                with lines_container:
                    ui.label("Aucune ligne. Ajoutez une pièce ci-dessous.") \
                        .classes("text-gray-500 text-sm text-center p-3")
                return
            for line in detail["lines"]:
                with lines_container:
                    render_line_row(line)

        def render_line_row(line):
            with ui.row().classes("w-full items-center gap-3 no-wrap "
                                    "border-b border-gray-200 py-2"):
                ui.label(line["part_name"]) \
                    .classes("text-sm flex-grow")
                # Quantité éditable
                qty_input = ui.number(value=line["quantity"],
                                       min=1, step=1, format="%d") \
                    .classes("w-24")
                # Sauvegarde sur perte de focus (.on('blur'))
                def make_save(lid=line["id"], inp=qty_input):
                    def handler():
                        ok, msg = update_bom_line_db(lid,
                                                       int(inp.value or 1))
                        if not ok:
                            ui.notify(msg, type="negative")
                            render_lines()
                    return handler
                qty_input.on("blur", make_save())

                # Bouton suppression
                def make_del(lid=line["id"]):
                    def handler():
                        ok, msg = delete_bom_line_db(lid)
                        ui.notify(msg, type="positive" if ok else "negative")
                        if ok:
                            render_lines()
                    return handler
                ui.button(icon="delete", on_click=make_del()) \
                    .props("flat round dense color=negative")

        # --- Formulaire d'ajout en bas -------------------------------
        with ui.row().classes("w-full items-end gap-2 mt-3 "
                                "border-t border-gray-200 pt-3"):
            # Sélecteur de pièce (par id)
            part_options = {p["id"]: p["part_name"] for p in parts}
            part_select = ui.select(options=part_options,
                                     label="Pièce", with_input=True) \
                .classes("flex-grow")
            qty_add = ui.number(label="Qté", value=1, min=1, step=1,
                                 format="%d").classes("w-24")
            def add_line():
                pid = part_select.value
                qty = int(qty_add.value or 1)
                if pid is None:
                    ui.notify("Sélectionnez une pièce.", type="warning")
                    return
                ok, msg = add_bom_line_db(bom_id, int(pid), qty)
                ui.notify(msg, type="positive" if ok else "negative")
                if ok:
                    part_select.value = None
                    qty_add.value = 1
                    render_lines()
            ui.button("+ Ajouter", on_click=add_line) \
                .props("color=primary")

        with ui.row().classes("w-full justify-end mt-3"):
            ui.button(_("Close"), on_click=dialog.close).props("flat")

        render_lines()
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
