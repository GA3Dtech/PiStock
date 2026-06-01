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

"""Couche d'acces a la base de donnees pour l'UI.

Toutes les fonctions accedent directement aux modeles SQLModel via
main (import paresseux pour eviter le cycle a l'import) plutot que
par HTTP interne. Les operations destructives verifient la session
admin (_session_admin_active) en defense en profondeur.
"""
import os
from sqlmodel import Session, select
from components.admin import _session_admin_active


def _db():
    """Helper qui renvoie tous les symboles dont on a besoin depuis main."""
    import main
    return main.engine, main.Parts, main.PLM, main.Stock, main.DATA_DIR
def delete_project_db(project_id: int):
    """Supprime un projet (UI in-process). Refuse (renvoie blocking)
    si des pieces ou des BOMs y sont encore rattachees. Necessite que
    la session soit admin (defense en profondeur).

    Retourne (ok: bool, msg: str, blocking: dict|None).
    Si blocking n'est pas None, il contient {"parts":[...], "boms":[...]}.
    """
    if not _session_admin_active():
        return False, "Session admin requise.", None
    import main
    with Session(main.engine) as session:
        project = session.get(main.Project, project_id)
        if project is None:
            return False, "Projet introuvable.", None
        parts_left = session.exec(
            select(main.Parts).where(main.Parts.id_project == project_id)
        ).all()
        boms_left = session.exec(
            select(main.Bom).where(main.Bom.id_project == project_id)
        ).all()
        if parts_left or boms_left:
            return False, (
                f"Impossible : {len(parts_left)} pièce(s) et "
                f"{len(boms_left)} BOM(s) rattachées au projet "
                f"« {project.code} »."
            ), {
                "parts": [
                    {"id": p.id, "part_name": p.part_name}
                    for p in parts_left
                ],
                "boms": [
                    {"id": b.id, "code": b.code,
                     "description": b.description}
                    for b in boms_left
                ],
            }
        code = project.code
        session.delete(project); session.commit()
        return True, f"Projet « {code} » supprimé.", None
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
    """Inverse le verrou. Retourne (ok, msg, new_locked).
    Le DEVERROUILLAGE exige une session admin ; verrouiller reste libre."""
    engine, Parts_cls, _, _, _ = _db()
    with Session(engine) as session:
        part = session.get(Parts_cls, part_id)
        if part is None:
            return (False, "Pièce introuvable.", None)
        # Le DEVERROUILLAGE exige une session admin ; verrouiller reste libre.
        if part.locked and not _session_admin_active():
            return False, "Session admin requise pour déverrouiller.", part.locked
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
    # Garde admin (defense en profondeur — l'UI gate aussi).
    if not _session_admin_active():
        return False, "Session admin requise."
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
    """Detail d'une BOM + ses lignes. Chaque ligne a 'line_type' =
    'part' ou 'subbom' ; selon le cas, soit part_name est rempli,
    soit subbom_code + subbom_description."""
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
        # Pre-charge parts + sous-BOMs referencees
        part_ids = {l.id_parts for l in lines_rows
                     if l.id_parts is not None}
        subbom_ids = {l.id_subbom for l in lines_rows
                       if l.id_subbom is not None}
        parts_by_id = {
            p.id: p for p in session.exec(
                select(main.Parts).where(main.Parts.id.in_(part_ids))
            ).all()
        } if part_ids else {}
        subboms_by_id = {
            b.id: b for b in session.exec(
                select(main.Bom).where(main.Bom.id.in_(subbom_ids))
            ).all()
        } if subbom_ids else {}
        project_code = None
        if bom.id_project is not None:
            proj = session.get(main.Project, bom.id_project)
            project_code = proj.code if proj else None

        result_lines = []
        for l in lines_rows:
            entry = {"id": l.id, "quantity": l.quantity}
            if l.id_parts is not None:
                entry["line_type"] = "part"
                entry["id_parts"] = l.id_parts
                entry["part_name"] = (parts_by_id[l.id_parts].part_name
                                       if l.id_parts in parts_by_id else "?")
                entry["id_subbom"] = None
                entry["subbom_code"] = None
                entry["subbom_description"] = None
            elif l.id_subbom is not None:
                sub = subboms_by_id.get(l.id_subbom)
                entry["line_type"] = "subbom"
                entry["id_parts"] = None
                entry["part_name"] = None
                entry["id_subbom"] = l.id_subbom
                entry["subbom_code"] = sub.code if sub else "?"
                entry["subbom_description"] = sub.description if sub else None
            else:
                continue  # ligne corrompue, on saute
            result_lines.append(entry)

        return {
            "id": bom.id,
            "code": bom.code,
            "description": bom.description,
            "id_project": bom.id_project,
            "project_code": project_code,
            "lines": result_lines,
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
    if not _session_admin_active():
        return False, "Session admin requise."
    """Supprime une BOM et ses lignes (necessite session admin)."""
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


def delete_part_db(part_id: int):
    if not _session_admin_active():
        return False, "Session admin requise.", None
    """Supprime DEFINITIVEMENT une piece (necessite session admin).

    Supprime aussi en cascade
    sur PLM, Stock et fichiers physiques. Refus si la piece est
    referencee dans une BOM.

    Retourne (ok, msg, blocking_boms) ou blocking_boms est :
    - None si suppression OK ou erreur generique
    - liste de {id, code, description} si la piece est dans des BOMs
    """
    import main
    with Session(main.engine) as session:
        part = session.get(main.Parts, part_id)
        if part is None:
            return (False, "Pièce introuvable.", None)

        # Verifie si referencee dans une BOM (id_parts, pas id_subbom)
        blocking = session.exec(
            select(main.Bom).join(
                main.BomLine, main.BomLine.id_bom == main.Bom.id)
            .where(main.BomLine.id_parts == part_id)
            .distinct()
        ).all()
        if blocking:
            bom_info = [
                {"id": b.id, "code": b.code,
                 "description": b.description or ""}
                for b in blocking
            ]
            return (
                False,
                f"Impossible de supprimer : pièce utilisée dans "
                f"{len(bom_info)} BOM(s).",
                bom_info
            )

        # Cascade : revisions PLM (avec leurs fichiers)
        plm_rows = session.exec(
            select(main.PLM).where(main.PLM.id_parts == part_id)
        ).all()
        for plm in plm_rows:
            main._delete_file_if_exists(plm.path_2_cadfile)
            main._delete_file_if_exists(plm.path_2_thumbnail)
            main._delete_file_if_exists(plm.path_2_3dglb)
            session.delete(plm)

        # Stock (avec photo + doc)
        stock = session.exec(
            select(main.Stock).where(main.Stock.id_parts == part_id)
        ).first()
        if stock is not None:
            main._delete_file_if_exists(stock.path_2_img)
            main._delete_file_if_exists(stock.path_2_doc)
            session.delete(stock)

        part_name = part.part_name
        session.delete(part)
        session.commit()
        return (
            True,
            f"Pièce '{part_name}' supprimée ({len(plm_rows)} révision(s) "
            f"PLM, stock {'oui' if stock else 'non'}).",
            None
        )


def add_bom_line_db(bom_id: int, part_id: int | None,
                     quantity: int, subbom_id: int | None = None):
    """Ajoute une ligne BOM. Soit part_id soit subbom_id, pas les deux.
    Si la cible existe deja dans la BOM, la quantite est cumulee.
    Pour subbom_id : refus si cycle detecte. Retourne (ok, msg)."""
    if (part_id is None) == (subbom_id is None):
        return (False, "Sélectionnez exactement une pièce OU une sous-BOM.")
    if quantity is None or quantity <= 0:
        return (False, "La quantité doit être > 0.")
    import main
    with Session(main.engine) as session:
        if session.get(main.Bom, bom_id) is None:
            return (False, "BOM introuvable.")
        if part_id is not None:
            if session.get(main.Parts, part_id) is None:
                return (False, "Pièce introuvable.")
            existing = session.exec(
                select(main.BomLine)
                .where(main.BomLine.id_bom == bom_id)
                .where(main.BomLine.id_parts == part_id)
            ).first()
            new_line = main.BomLine(id_bom=bom_id, id_parts=part_id,
                                      quantity=int(quantity))
        else:
            sub = session.get(main.Bom, subbom_id)
            if sub is None:
                return (False, "Sous-BOM introuvable.")
            if main._would_create_cycle(session, bom_id, subbom_id):
                return (False, f"Cycle détecté : '{sub.code}' contient "
                                f"déjà cette BOM directement ou non.")
            existing = session.exec(
                select(main.BomLine)
                .where(main.BomLine.id_bom == bom_id)
                .where(main.BomLine.id_subbom == subbom_id)
            ).first()
            new_line = main.BomLine(id_bom=bom_id, id_subbom=subbom_id,
                                      quantity=int(quantity))

        if existing:
            existing.quantity += int(quantity)
            session.add(existing)
            session.commit()
            return (True, f"Quantité cumulée à {existing.quantity}.")
        session.add(new_line)
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
    """Applique 'factor' fois la BOM sur le stock. Traverse
    RECURSIVEMENT les sous-BOMs via main._flatten_bom : pour une
    BOM contenant des sous-BOMs, on calcule d'abord le total par
    piece feuille, puis on applique sur le stock.
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
        # Verifie qu'il y a au moins une ligne (sinon BOM vide)
        any_line = session.exec(
            select(main.BomLine).where(main.BomLine.id_bom == bom_id).limit(1)
        ).first()
        if any_line is None:
            return (False, "La BOM est vide.", None)

        # Flatten hierarchique -> {part_id: total_qty}
        try:
            totals = main._flatten_bom(session, bom_id, factor=factor)
        except Exception as e:
            return (False, f"Erreur lors du calcul : {e}", None)

        if direction == "sub":
            # Verification atomique sur les pieces feuilles
            shortages = []
            for part_id, needed in totals.items():
                stock = session.exec(
                    select(main.Stock).where(main.Stock.id_parts == part_id)
                ).first()
                current = stock.quantity if stock else 0
                if current < needed:
                    part = session.get(main.Parts, part_id)
                    shortages.append({
                        "part_name": part.part_name if part else "?",
                        "needed": needed,
                        "available": current,
                        "missing": needed - current,
                    })
            if shortages:
                return (False, "Stock insuffisant.", shortages)

        # Application des changements aux pieces feuilles
        for part_id, qty in totals.items():
            stock = main._get_or_create_stock(session, part_id)
            delta = qty if direction == "add" else -qty
            stock.quantity += delta
            session.add(stock)
        session.commit()
        verb = "ajoutée" if direction == "add" else "retirée"
        return (True, f"BOM {verb} ×{factor}.", None)


# Note : la sauvegarde des photos de stock se fait via l'endpoint REST
# POST /api/v1/parts/{id}/stock-photo dans main.py, appele directement
# par le JS du navigateur (fetch). On n'a pas besoin d'une version
# Python ici, ce qui evite aussi de dupliquer la logique de chemins.
