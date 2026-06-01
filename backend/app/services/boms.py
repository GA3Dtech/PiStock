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
Endpoints BOM (Bill of Materials / nomenclatures) + logique de
hierarchie (sous-BOMs) : aplatissage recursif et detection de cycle.

Les helpers `_flatten_bom` et `_would_create_cycle` sont aussi utilises
par les plugins (cf. plugins/bom_tree) via la facade main.
"""
from fastapi import APIRouter, Form, HTTPException, Depends
from sqlmodel import Session, select
from pydantic import BaseModel

from config import engine, logger
from model import Parts, Project, Bom, BomLine, Stock
from services.codes import _next_bom_code
from services.stock import _get_or_create_stock
from services.admin import _require_admin

router = APIRouter()


# ----------------------------------------------------------------------
#  HELPERS BOM : HIERARCHIE (sous-BOMs)
# ----------------------------------------------------------------------
def _flatten_bom(session: Session, bom_id: int, factor: int = 1,
                  visited: set | None = None) -> dict[int, int]:
    """Parcourt recursivement la BOM et retourne un dict
    {part_id: quantite_totale} en accumulant les besoins des sous-BOMs.

    'factor' multiplie tout (utile pour les calculs de stock-add/sub
    avec un facteur global). 'visited' garde l'ensemble des BOM_IDs
    deja traversees pour eviter les boucles infinies (securite, meme
    si _would_create_cycle previent normalement leur creation).

    Exemple : si BOM A contient :
       - 5 vis-M3
       - 2× sous-BOM B (qui contient 3 ecrou + 1 rondelle)
    alors _flatten_bom(A, factor=1) renvoie :
       {vis-M3: 5, ecrou: 6, rondelle: 2}
    """
    if visited is None:
        visited = set()
    if bom_id in visited:
        # Cycle : ne devrait pas arriver, mais on protege quand meme.
        raise HTTPException(
            status_code=500,
            detail=f"Cycle detecte lors du parcours de la BOM "
                   f"(id={bom_id})."
        )
    visited = visited | {bom_id}

    totals: dict[int, int] = {}
    lines = session.exec(
        select(BomLine).where(BomLine.id_bom == bom_id)
    ).all()
    for line in lines:
        if line.id_parts is not None:
            delta = line.quantity * factor
            totals[line.id_parts] = totals.get(line.id_parts, 0) + delta
        elif line.id_subbom is not None:
            # Recursion : on accumule les besoins de la sous-BOM,
            # multiplies par la quantite de cette ligne.
            sub_totals = _flatten_bom(
                session, line.id_subbom,
                factor=line.quantity * factor,
                visited=visited,
            )
            for pid, qty in sub_totals.items():
                totals[pid] = totals.get(pid, 0) + qty
        # Les lignes avec NI part NI subbom sont ignorees (donnee
        # corrompue, mais pas raison de planter)
    return totals


def _would_create_cycle(session: Session, parent_bom_id: int,
                          candidate_subbom_id: int) -> bool:
    """Verifie si ajouter candidate_subbom_id comme sous-BOM de
    parent_bom_id creerait un cycle. True = cycle detecte (refuser).

    Trois cas :
    1. Auto-reference : parent == candidate
    2. Parent est ancetre direct ou indirect de candidate
       (qui irait creer une boucle si on ajoute le lien)
    """
    if parent_bom_id == candidate_subbom_id:
        return True
    # DFS sur la descendance de candidate. Si on tombe sur parent,
    # c'est qu'il y a deja un chemin candidate -> ... -> parent,
    # et ajouter parent -> candidate boucle.
    stack = [candidate_subbom_id]
    visited = set()
    while stack:
        current = stack.pop()
        if current in visited:
            continue
        visited.add(current)
        sub_lines = session.exec(
            select(BomLine)
            .where(BomLine.id_bom == current)
            .where(BomLine.id_subbom.is_not(None))
        ).all()
        for line in sub_lines:
            if line.id_subbom == parent_bom_id:
                return True
            stack.append(line.id_subbom)
    return False


# ======================================================================
#  ENDPOINTS BOM
# ======================================================================
@router.get("/api/v1/boms")
def list_boms(project_code: str | None = None):
    """Liste les BOMs. Chaque entree comprend le code, la description,
    le projet associe (si rattachee), et le nombre de lignes."""
    with Session(engine) as session:
        query = select(Bom).order_by(Bom.code)
        if project_code:
            project = session.exec(
                select(Project).where(Project.code == project_code)
            ).first()
            if project is None:
                return []
            query = query.where(Bom.id_project == project.id)
        boms = session.exec(query).all()
        # Pre-charge codes projet pour eviter une requete par BOM
        projects_by_id = {
            p.id: p.code
            for p in session.exec(select(Project)).all()
        }
        result = []
        for b in boms:
            # Compte les lignes (sans charger les objets) pour rester
            # leger sur la liste.
            n_lines = session.exec(
                select(BomLine).where(BomLine.id_bom == b.id)
            ).all()
            result.append({
                "id": b.id,
                "code": b.code,
                "description": b.description,
                "id_project": b.id_project,
                "project_code": projects_by_id.get(b.id_project),
                "line_count": len(n_lines),
            })
        return result


@router.get("/api/v1/boms/{bom_id}")
def get_bom(bom_id: int):
    """Detail d'une BOM avec toutes ses lignes. Chaque ligne est soit
    une piece (id_parts + part_name), soit une sous-BOM (id_subbom +
    subbom_code + subbom_description). Le champ 'line_type' vaut
    'part' ou 'subbom' selon le cas."""
    with Session(engine) as session:
        bom = session.get(Bom, bom_id)
        if bom is None:
            raise HTTPException(status_code=404,
                                detail=f"BOM id={bom_id} introuvable.")
        project = None
        if bom.id_project is not None:
            project = session.get(Project, bom.id_project)

        lines = session.exec(
            select(BomLine).where(BomLine.id_bom == bom_id)
            .order_by(BomLine.id)
        ).all()
        # Pre-charge les parts ET les sous-BOMs referencees pour eviter
        # une requete par ligne.
        part_ids = {l.id_parts for l in lines if l.id_parts is not None}
        subbom_ids = {l.id_subbom for l in lines if l.id_subbom is not None}
        parts_by_id = {
            p.id: p for p in session.exec(
                select(Parts).where(Parts.id.in_(part_ids))
            ).all()
        } if part_ids else {}
        subboms_by_id = {
            b.id: b for b in session.exec(
                select(Bom).where(Bom.id.in_(subbom_ids))
            ).all()
        } if subbom_ids else {}

        result_lines = []
        for l in lines:
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
                # Ligne corrompue (ni part ni subbom) - rare, on log et skip
                logger.warning(f"BomLine id={l.id} sans part ni subbom.")
                continue
            result_lines.append(entry)

        return {
            "id": bom.id,
            "code": bom.code,
            "description": bom.description,
            "id_project": bom.id_project,
            "project_code": project.code if project else None,
            "lines": result_lines,
        }


@router.post("/api/v1/boms")
def create_bom(description: str = Form(default=""),
                id_project: int | None = Form(default=None)):
    """Cree une BOM avec un code auto-genere (B0001, B0002...)."""
    description = (description or "").strip() or None
    with Session(engine) as session:
        if id_project is not None:
            if session.get(Project, id_project) is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Projet id={id_project} introuvable."
                )
        code = _next_bom_code(session)
        bom = Bom(code=code, description=description, id_project=id_project)
        session.add(bom)
        session.commit()
        session.refresh(bom)
        logger.info(f"BOM '{code}' creee (id={bom.id}).")
        return {
            "status": "success",
            "id": bom.id,
            "code": bom.code,
            "description": bom.description,
            "id_project": bom.id_project,
        }


@router.delete("/api/v1/boms/{bom_id}")
def delete_bom(bom_id: int,
               _admin: None = Depends(_require_admin)):
    """Supprime une BOM ET toutes ses lignes (suppression en cascade
    geree manuellement puisque SQLite n'enforce pas les FK par defaut)."""
    with Session(engine) as session:
        bom = session.get(Bom, bom_id)
        if bom is None:
            raise HTTPException(status_code=404,
                                detail=f"BOM id={bom_id} introuvable.")
        # Cascade manuelle
        lines = session.exec(
            select(BomLine).where(BomLine.id_bom == bom_id)
        ).all()
        for line in lines:
            session.delete(line)
        session.delete(bom)
        session.commit()
        logger.info(f"BOM '{bom.code}' supprimee ({len(lines)} lignes).")
        return {"status": "success", "deleted_id": bom_id,
                "lines_removed": len(lines)}


@router.post("/api/v1/boms/{bom_id}/lines")
def add_bom_line(bom_id: int,
                  part_id: int | None = Form(default=None),
                  subbom_id: int | None = Form(default=None),
                  quantity: int = Form(default=1)):
    """Ajoute une ligne a une BOM : soit une piece (part_id), soit une
    sous-BOM (subbom_id). EXACTEMENT UN des deux doit etre fourni.

    Si une ligne identique (meme type ET meme cible) existe deja, la
    quantite est CUMULEE plutot que de creer une nouvelle ligne.

    Pour subbom_id : refus si l'ajout creerait un cycle dans la
    hierarchie (auto-reference ou boucle indirecte)."""
    # Validation : exactement un des deux non-null
    if (part_id is None) == (subbom_id is None):
        raise HTTPException(
            status_code=400,
            detail="Fournir exactement un de part_id ou subbom_id."
        )
    if quantity <= 0:
        raise HTTPException(status_code=400,
                            detail="La quantité doit être > 0.")
    with Session(engine) as session:
        bom = session.get(Bom, bom_id)
        if bom is None:
            raise HTTPException(status_code=404,
                                detail=f"BOM id={bom_id} introuvable.")

        if part_id is not None:
            # --- Ligne de type "piece" ---
            part = session.get(Parts, part_id)
            if part is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Pièce id={part_id} introuvable."
                )
            existing = session.exec(
                select(BomLine)
                .where(BomLine.id_bom == bom_id)
                .where(BomLine.id_parts == part_id)
            ).first()
            new_line = BomLine(id_bom=bom_id, id_parts=part_id,
                                 quantity=quantity)
        else:
            # --- Ligne de type "sous-BOM" ---
            sub = session.get(Bom, subbom_id)
            if sub is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"BOM id={subbom_id} introuvable."
                )
            # Detection de cycle AVANT toute modification de la base
            if _would_create_cycle(session, bom_id, subbom_id):
                raise HTTPException(
                    status_code=400,
                    detail=f"Cycle détecté : la BOM '{sub.code}' ne peut "
                           f"pas être incluse dans '{bom.code}' "
                           f"(elle la contient déjà directement ou "
                           f"indirectement)."
                )
            existing = session.exec(
                select(BomLine)
                .where(BomLine.id_bom == bom_id)
                .where(BomLine.id_subbom == subbom_id)
            ).first()
            new_line = BomLine(id_bom=bom_id, id_subbom=subbom_id,
                                 quantity=quantity)

        if existing:
            existing.quantity += quantity
            session.add(existing)
            session.commit()
            return {"status": "success", "id": existing.id,
                    "quantity": existing.quantity, "merged": True}
        session.add(new_line)
        session.commit()
        session.refresh(new_line)
        return {"status": "success", "id": new_line.id,
                "quantity": new_line.quantity, "merged": False}


@router.put("/api/v1/boms/{bom_id}/lines/{line_id}")
def update_bom_line(bom_id: int, line_id: int,
                     quantity: int = Form(...)):
    """Met a jour la quantite d'une ligne BOM."""
    if quantity <= 0:
        raise HTTPException(status_code=400,
                            detail="La quantité doit être > 0.")
    with Session(engine) as session:
        line = session.get(BomLine, line_id)
        if line is None or line.id_bom != bom_id:
            raise HTTPException(status_code=404,
                                detail="Ligne BOM introuvable.")
        line.quantity = quantity
        session.add(line)
        session.commit()
        return {"status": "success", "id": line.id, "quantity": quantity}


@router.delete("/api/v1/boms/{bom_id}/lines/{line_id}")
def delete_bom_line(bom_id: int, line_id: int):
    """Supprime une ligne d'une BOM."""
    with Session(engine) as session:
        line = session.get(BomLine, line_id)
        if line is None or line.id_bom != bom_id:
            raise HTTPException(status_code=404,
                                detail="Ligne BOM introuvable.")
        session.delete(line)
        session.commit()
        return {"status": "success", "deleted_id": line_id}


@router.post("/api/v1/boms/{bom_id}/stock-add")
def bom_stock_add(bom_id: int, factor: int = Form(default=1)):
    """Ajoute 'factor' fois la BOM au stock. Traverse RECURSIVEMENT
    les sous-BOMs : si la BOM A contient 2× une sous-BOM B, et que B
    contient 3 vis, alors stock-add(A, factor=1) ajoute 6 vis au stock.

    Cree les lignes Stock manquantes. Atomique : tout ou rien."""
    if factor <= 0:
        raise HTTPException(status_code=400,
                            detail="Le facteur doit être > 0.")
    with Session(engine) as session:
        bom = session.get(Bom, bom_id)
        if bom is None:
            raise HTTPException(status_code=404,
                                detail=f"BOM id={bom_id} introuvable.")
        # Verifie qu'il y a au moins une ligne
        any_line = session.exec(
            select(BomLine).where(BomLine.id_bom == bom_id).limit(1)
        ).first()
        if any_line is None:
            raise HTTPException(status_code=400,
                                detail="La BOM est vide.")

        # Flatten hierarchique -> {part_id: total_qty}
        totals = _flatten_bom(session, bom_id, factor=factor)

        # Applique les increments aux pieces feuilles
        changes = []
        for part_id, delta in totals.items():
            stock = _get_or_create_stock(session, part_id)
            stock.quantity += delta
            session.add(stock)
            changes.append({
                "id_parts": part_id,
                "delta": delta,
                "new_quantity": stock.quantity,
            })
        session.commit()
        logger.info(f"BOM '{bom.code}' ajoutee x{factor} au stock "
                    f"({len(changes)} pieces feuilles affectees).")
        return {"status": "success", "factor": factor, "changes": changes}


@router.post("/api/v1/boms/{bom_id}/stock-sub")
def bom_stock_sub(bom_id: int, factor: int = Form(default=1)):
    """Retire 'factor' fois la BOM du stock. Traverse RECURSIVEMENT
    les sous-BOMs. ATOMIQUE : si une seule piece n'a pas assez,
    on REFUSE tout et on renvoie la liste exhaustive des manques
    (status 409 Conflict)."""
    if factor <= 0:
        raise HTTPException(status_code=400,
                            detail="Le facteur doit être > 0.")
    with Session(engine) as session:
        bom = session.get(Bom, bom_id)
        if bom is None:
            raise HTTPException(status_code=404,
                                detail=f"BOM id={bom_id} introuvable.")
        any_line = session.exec(
            select(BomLine).where(BomLine.id_bom == bom_id).limit(1)
        ).first()
        if any_line is None:
            raise HTTPException(status_code=400,
                                detail="La BOM est vide.")

        # Flatten hierarchique -> {part_id: total_qty_needed}
        totals = _flatten_bom(session, bom_id, factor=factor)

        # Phase 1 : verification atomique de la disponibilite
        shortages = []
        for part_id, needed in totals.items():
            stock = session.exec(
                select(Stock).where(Stock.id_parts == part_id)
            ).first()
            current = stock.quantity if stock else 0
            if current < needed:
                part = session.get(Parts, part_id)
                shortages.append({
                    "id_parts": part_id,
                    "part_name": part.part_name if part else "?",
                    "needed": needed,
                    "available": current,
                    "missing": needed - current,
                })
        if shortages:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Stock insuffisant pour cette BOM.",
                    "shortages": shortages,
                }
            )

        # Phase 2 : application des decrements
        changes = []
        for part_id, needed in totals.items():
            stock = _get_or_create_stock(session, part_id)
            stock.quantity -= needed
            session.add(stock)
            changes.append({
                "id_parts": part_id,
                "delta": -needed,
                "new_quantity": stock.quantity,
            })
        session.commit()
        logger.info(f"BOM '{bom.code}' retiree x{factor} du stock "
                    f"({len(changes)} pieces feuilles affectees).")
        return {"status": "success", "factor": factor, "changes": changes}


# ----------------------------------------------------------------------
#  ENDPOINT : creation atomique d'une BOM a partir d'un assemblage
# ----------------------------------------------------------------------
# Recoit un JSON contenant : description, id_project optionnel, et une
# liste de lignes {name, quantity, use_existing_id?}. Pour chaque ligne :
#   - si use_existing_id fourni : on l'utilise direct
#   - sinon, on cherche une piece existante avec ce nom : si trouvee,
#     on l'utilise ; sinon, on cree une nouvelle piece.
# Tout est fait dans UNE transaction : si quoi que ce soit echoue
# (nom invalide, ID inexistant), RIEN n'est cree.

class BomFromAssemblyLine(BaseModel):
    name: str
    quantity: int
    use_existing_id: int | None = None


class BomFromAssemblyRequest(BaseModel):
    description: str = ""
    id_project: int | None = None
    lines: list[BomFromAssemblyLine]


@router.post("/api/v1/boms/from-assembly")
def create_bom_from_assembly(req: BomFromAssemblyRequest):
    """Cree une BOM avec ses lignes a partir d'un scan d'assemblage.
    Cree au passage les pieces qui n'existent pas encore. Atomique :
    tout ou rien."""
    if not req.lines:
        raise HTTPException(status_code=400,
                            detail="La liste des lignes est vide.")
    description = (req.description or "").strip() or None

    # Pre-merge cote serveur : si plusieurs lignes ont le meme nom
    # (cas pas impossible si la macro envoie a la fois des doublons et
    # des Links separes), on cumule les quantites. Le merge se fait
    # par cle = use_existing_id si fourni, sinon par nom.
    merged: dict[tuple, int] = {}
    for line in req.lines:
        if line.quantity <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"Quantité invalide pour '{line.name}' : "
                       f"{line.quantity}"
            )
        key = ("id", line.use_existing_id) if line.use_existing_id \
              else ("name", line.name)
        merged[key] = merged.get(key, 0) + line.quantity

    with Session(engine) as session:
        # Verifie le projet si specifie
        if req.id_project is not None:
            if session.get(Project, req.id_project) is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Projet id={req.id_project} introuvable."
                )

        # Phase 1 : resoudre toutes les lignes en (part_id, qty),
        # en creant les pieces manquantes au passage. On accumule
        # dans une liste pour la phase 2.
        resolved: list[tuple[int, int]] = []  # [(part_id, qty), ...]
        created_parts: list[dict] = []        # pour le rapport final

        for key, qty in merged.items():
            if key[0] == "id":
                part_id = key[1]
                part = session.get(Parts, part_id)
                if part is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Pièce id={part_id} introuvable "
                               f"(mapping explicite)."
                    )
                resolved.append((part.id, qty))
            else:
                name = key[1].strip()
                if not name:
                    raise HTTPException(
                        status_code=400,
                        detail="Nom de pièce vide dans la liste."
                    )
                # Cherche par nom exact
                existing = session.exec(
                    select(Parts).where(Parts.part_name == name)
                ).first()
                if existing is not None:
                    resolved.append((existing.id, qty))
                else:
                    # Cree la piece (Parts vide, sans CAO, sans projet)
                    new_part = Parts(part_name=name)
                    session.add(new_part)
                    session.flush()  # pour avoir new_part.id
                    resolved.append((new_part.id, qty))
                    created_parts.append({
                        "id": new_part.id,
                        "part_name": new_part.part_name,
                    })

        # Phase 2 : cree la BOM et ses lignes
        try:
            code = _next_bom_code(session)
        except HTTPException:
            raise  # 507 sur dépassement BOM_CODE_MAX

        bom = Bom(code=code, description=description,
                   id_project=req.id_project)
        session.add(bom)
        session.flush()

        for part_id, qty in resolved:
            session.add(BomLine(id_bom=bom.id, id_parts=part_id,
                                 quantity=qty))

        session.commit()
        session.refresh(bom)
        logger.info(f"BOM '{code}' creee depuis assemblage "
                    f"({len(resolved)} lignes, "
                    f"{len(created_parts)} pieces creees).")
        return {
            "status": "success",
            "id": bom.id,
            "code": bom.code,
            "lines_created": len(resolved),
            "parts_created": created_parts,
        }
