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

import os
import logging
import traceback
from datetime import datetime, timezone
from shutil import copyfileobj
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from sqlmodel import SQLModel, Field, Session, create_engine, select
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pistock")

# Configuration des chemins (à adapter selon votre arborescence)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, "../../../data-pistock"))
CAD_DIR = os.path.join(DATA_DIR, "uploads", "cad")
IMG_DIR = os.path.join(DATA_DIR, "uploads", "img")
DB_PATH = os.path.join(DATA_DIR, "pistockdatabase.sqlite3")

# S'assurer que tous les dossiers nécessaires existent
os.makedirs(CAD_DIR, exist_ok=True)
os.makedirs(IMG_DIR, exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}")

app = FastAPI(title="PiStock PLM Receiver")


# ----------------------------------------------------------------------
#  MODELES
# ----------------------------------------------------------------------
# IMPORTANT : ces modèles doivent rester cohérents avec init_db.py.
class Parts(SQLModel, table=True):
    __tablename__ = "parts"
    id: int | None = Field(default=None, primary_key=True)
    part_name: str = Field(index=True, unique=True)
    # Lien optionnel vers un projet. Voir init_db.py pour le detail.
    id_project: int | None = Field(default=None, foreign_key="project.id")
    status: str = Field(default="Init")
    locked: bool = Field(default=False)


class PLM(SQLModel, table=True):
    __tablename__ = "plm"
    id: int | None = Field(default=None, primary_key=True)
    id_parts: int = Field(foreign_key="parts.id")
    path_2_cadfile: str | None = None
    path_2_thumbnail: str | None = None
    path_2_3dglb: str | None = None
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    author: str | None = None
    # Numero de version (aa..zz), incremente par piece a chaque
    # nouvelle revision. Geree automatiquement cote serveur.
    version: str = Field(default="aa", max_length=2)
    # Flag "revision principale" : si True, c'est cette revision qui
    # est affichee. Sinon, fallback sur la plus recente par timestamp.
    is_main: bool = Field(default=False)


class Stock(SQLModel, table=True):
    __tablename__ = "stock"
    id: int | None = Field(default=None, primary_key=True)
    id_parts: int = Field(foreign_key="parts.id")
    path_2_img: str | None = None
    quantity: int = Field(default=0)
    location: str | None = None
    supply: str | None = None
    # Fiche composant (PDF, datasheet...) dans uploads/doc/
    path_2_doc: str | None = None


class Project(SQLModel, table=True):
    __tablename__ = "project"
    id: int | None = Field(default=None, primary_key=True)
    # Code alphabetique a 3 lettres (AAA, AAB...), incremente
    # automatiquement par le serveur. Unique : sert d'identifiant
    # lisible pour l'utilisateur.
    code: str = Field(index=True, unique=True, max_length=3)
    description: str | None = None


class Bom(SQLModel, table=True):
    __tablename__ = "bom"
    id: int | None = Field(default=None, primary_key=True)
    # Code BOM : B + 4 chiffres (B0001, B0002...). Incremental, unique.
    code: str = Field(index=True, unique=True, max_length=5)
    description: str | None = None
    # Lien optionnel vers un projet.
    id_project: int | None = Field(default=None, foreign_key="project.id")


class BomLine(SQLModel, table=True):
    __tablename__ = "bom_line"
    id: int | None = Field(default=None, primary_key=True)
    id_bom: int = Field(foreign_key="bom.id")
    id_parts: int = Field(foreign_key="parts.id")
    quantity: int = Field(default=1)


# ----------------------------------------------------------------------
#  HELPERS GENERATION DU CODE PROJET
# ----------------------------------------------------------------------
# Le code projet est un "nombre" en base 26 sur 3 positions :
#   AAA = 0, AAB = 1, ..., AAZ = 25, ABA = 26, ..., ZZZ = 17575.
# On le manipule comme un entier pour l'incrementer, puis on le
# reconvertit en chaine. Cette approche est plus robuste qu'une
# manipulation caractere par caractere avec gestion des retenues.
PROJECT_CODE_MAX = 26 ** 3 - 1  # = 17575 -> "ZZZ"


def _code_to_int(code: str) -> int:
    """Convertit 'AAA'->0, 'AAB'->1, ..., 'ZZZ'->17575."""
    return ((ord(code[0]) - ord("A")) * 676
            + (ord(code[1]) - ord("A")) * 26
            + (ord(code[2]) - ord("A")))


def _int_to_code(n: int) -> str:
    """Inverse de _code_to_int. n doit etre dans [0, 17575]."""
    return (chr(ord("A") + n // 676)
            + chr(ord("A") + (n // 26) % 26)
            + chr(ord("A") + n % 26))


def _next_project_code(session: Session) -> str:
    """Calcule le prochain code disponible. Si aucun projet n'existe
    encore : 'AAA'. Sinon : (max existant) + 1. Leve HTTPException si
    on depasse 'ZZZ' (limite tres haute en pratique : 17576 projets)."""
    # Comme tous les codes ont 3 caracteres A-Z, l'ordre alphabetique
    # coincide avec l'ordre numerique : un simple MAX(code) suffit.
    last = session.exec(
        select(Project.code).order_by(Project.code.desc()).limit(1)
    ).first()
    if last is None:
        return "AAA"
    next_n = _code_to_int(last) + 1
    if next_n > PROJECT_CODE_MAX:
        raise HTTPException(
            status_code=507,  # 507 Insufficient Storage
            detail="Limite de codes projet atteinte (ZZZ)."
        )
    return _int_to_code(next_n)


# ----------------------------------------------------------------------
#  HELPER GENERATION CODE BOM
# ----------------------------------------------------------------------
# Format : 'B' + 4 chiffres zero-padded (B0001..B9999). L'ordre
# alphabetique coincide avec l'ordre numerique grace au zero-padding,
# donc on peut faire un MAX(code) en SQL.
BOM_CODE_MAX = 9999


def _next_bom_code(session: Session) -> str:
    last = session.exec(
        select(Bom.code).order_by(Bom.code.desc()).limit(1)
    ).first()
    if last is None:
        return "B0001"
    # Extrait la partie numerique (apres le 'B')
    try:
        n = int(last[1:])
    except (ValueError, IndexError):
        n = 0
    n += 1
    if n > BOM_CODE_MAX:
        raise HTTPException(
            status_code=507,
            detail="Limite de codes BOM atteinte (B9999)."
        )
    return f"B{n:04d}"


# ----------------------------------------------------------------------
#  HELPER GENERATION VERSION PLM
# ----------------------------------------------------------------------
# Meme logique que les codes projet, mais sur 2 lettres minuscules
# (aa..zz, soit 676 versions max par piece). Calcule PAR PIECE.
PLM_VERSION_MAX = 26 * 26 - 1  # = 675 -> "zz"


def _version_to_int(v: str) -> int:
    return (ord(v[0]) - ord("a")) * 26 + (ord(v[1]) - ord("a"))


def _int_to_version(n: int) -> str:
    return chr(ord("a") + n // 26) + chr(ord("a") + n % 26)


def _next_version_for_part(session: Session, part_id: int) -> str:
    """Renvoie la prochaine version PLM pour une piece donnee.
    Premiere revision -> 'aa'. Sinon : (max existant pour cette piece) + 1."""
    last = session.exec(
        select(PLM.version)
        .where(PLM.id_parts == part_id)
        .order_by(PLM.version.desc())
        .limit(1)
    ).first()
    if last is None:
        return "aa"
    next_n = _version_to_int(last) + 1
    if next_n > PLM_VERSION_MAX:
        raise HTTPException(
            status_code=507,
            detail=f"Limite de versions PLM atteinte (zz) pour cette piece."
        )
    return _int_to_version(next_n)


def _get_current_plm(session: Session, part_id: int):
    """Renvoie la revision PLM "courante" d'une piece :
    - celle marquee is_main=True si elle existe
    - sinon, la plus recente par timestamp
    - None si la piece n'a aucune revision PLM
    Centralise la logique de "quelle revision afficher" pour rester
    coherent entre /parts/full, /parts/{id} et le dashboard."""
    main = session.exec(
        select(PLM)
        .where(PLM.id_parts == part_id)
        .where(PLM.is_main == True)  # noqa: E712 (SQLAlchemy needs ==)
    ).first()
    if main is not None:
        return main
    return session.exec(
        select(PLM)
        .where(PLM.id_parts == part_id)
        .order_by(PLM.timestamp.desc())
    ).first()


@app.on_event("startup")
def on_startup():
    SQLModel.metadata.create_all(engine)
    logger.info("Base de donnees initialisee.")


# ----------------------------------------------------------------------
#  ENDPOINTS API : PROJETS
# ----------------------------------------------------------------------
@app.get("/api/v1/projects")
def list_projects():
    """Liste de tous les projets, tries par code croissant."""
    with Session(engine) as session:
        projects = session.exec(
            select(Project).order_by(Project.code)
        ).all()
        return [
            {"id": p.id, "code": p.code, "description": p.description}
            for p in projects
        ]


@app.post("/api/v1/projects")
def create_project(description: str = Form(default="")):
    """Cree un nouveau projet avec un code auto-genere.
    L'utilisateur fournit seulement la description (optionnelle) ;
    le code est calcule par le serveur (AAA, AAB, ...)."""
    description = (description or "").strip() or None
    with Session(engine) as session:
        code = _next_project_code(session)
        project = Project(code=code, description=description)
        session.add(project)
        session.commit()
        session.refresh(project)
        logger.info(f"Projet '{code}' cree (id={project.id}).")
        return {
            "status": "success",
            "id": project.id,
            "code": project.code,
            "description": project.description,
        }


# ======================================================================
#  ENDPOINTS BOM (Bill of Materials / nomenclatures)
# ======================================================================
@app.get("/api/v1/boms")
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


@app.get("/api/v1/boms/{bom_id}")
def get_bom(bom_id: int):
    """Detail d'une BOM avec toutes ses lignes (id, part, qty)."""
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
        # Pre-charge les parts pour eviter une requete par ligne
        if lines:
            part_ids = {l.id_parts for l in lines}
            parts_by_id = {
                p.id: p for p in session.exec(
                    select(Parts).where(Parts.id.in_(part_ids))
                ).all()
            }
        else:
            parts_by_id = {}

        return {
            "id": bom.id,
            "code": bom.code,
            "description": bom.description,
            "id_project": bom.id_project,
            "project_code": project.code if project else None,
            "lines": [
                {
                    "id": l.id,
                    "id_parts": l.id_parts,
                    "part_name": (parts_by_id[l.id_parts].part_name
                                   if l.id_parts in parts_by_id else None),
                    "quantity": l.quantity,
                }
                for l in lines
            ],
        }


@app.post("/api/v1/boms")
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


@app.delete("/api/v1/boms/{bom_id}")
def delete_bom(bom_id: int):
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


@app.post("/api/v1/boms/{bom_id}/lines")
def add_bom_line(bom_id: int,
                  part_id: int = Form(...),
                  quantity: int = Form(default=1)):
    """Ajoute une ligne (part + quantite) a une BOM. Si la part est
    deja presente dans la BOM, la quantite est CUMULEE (plutot que de
    remplacer ou refuser) : commportement le plus utile en pratique."""
    if quantity <= 0:
        raise HTTPException(status_code=400,
                            detail="La quantité doit être > 0.")
    with Session(engine) as session:
        bom = session.get(Bom, bom_id)
        if bom is None:
            raise HTTPException(status_code=404,
                                detail=f"BOM id={bom_id} introuvable.")
        part = session.get(Parts, part_id)
        if part is None:
            raise HTTPException(status_code=404,
                                detail=f"Pièce id={part_id} introuvable.")
        # Ligne deja presente ?
        existing = session.exec(
            select(BomLine)
            .where(BomLine.id_bom == bom_id)
            .where(BomLine.id_parts == part_id)
        ).first()
        if existing:
            existing.quantity += quantity
            session.add(existing)
            session.commit()
            return {"status": "success", "id": existing.id,
                    "quantity": existing.quantity, "merged": True}

        line = BomLine(id_bom=bom_id, id_parts=part_id, quantity=quantity)
        session.add(line)
        session.commit()
        session.refresh(line)
        return {"status": "success", "id": line.id,
                "quantity": line.quantity, "merged": False}


@app.put("/api/v1/boms/{bom_id}/lines/{line_id}")
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


@app.delete("/api/v1/boms/{bom_id}/lines/{line_id}")
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


@app.post("/api/v1/boms/{bom_id}/stock-add")
def bom_stock_add(bom_id: int, factor: int = Form(default=1)):
    """Ajoute 'factor' fois la BOM au stock : pour chaque ligne,
    on incremente la quantite stock de (line.quantity * factor).
    Cree la ligne stock si absente. Atomique : tout ou rien."""
    if factor <= 0:
        raise HTTPException(status_code=400,
                            detail="Le facteur doit être > 0.")
    with Session(engine) as session:
        bom = session.get(Bom, bom_id)
        if bom is None:
            raise HTTPException(status_code=404,
                                detail=f"BOM id={bom_id} introuvable.")
        lines = session.exec(
            select(BomLine).where(BomLine.id_bom == bom_id)
        ).all()
        if not lines:
            raise HTTPException(status_code=400,
                                detail="La BOM est vide.")

        # Applique les increments
        changes = []
        for line in lines:
            stock = _get_or_create_stock(session, line.id_parts)
            delta = line.quantity * factor
            stock.quantity += delta
            session.add(stock)
            changes.append({
                "id_parts": line.id_parts,
                "delta": delta,
                "new_quantity": stock.quantity,
            })
        session.commit()
        logger.info(f"BOM '{bom.code}' ajoutee x{factor} au stock "
                    f"({len(changes)} pieces affectees).")
        return {"status": "success", "factor": factor, "changes": changes}


@app.post("/api/v1/boms/{bom_id}/stock-sub")
def bom_stock_sub(bom_id: int, factor: int = Form(default=1)):
    """Retire 'factor' fois la BOM du stock. ATOMIQUE : si une seule
    piece n'a pas assez de stock, on REFUSE tout et on renvoie la
    liste des manques (status 409 Conflict). Pas de retrait partiel."""
    if factor <= 0:
        raise HTTPException(status_code=400,
                            detail="Le facteur doit être > 0.")
    with Session(engine) as session:
        bom = session.get(Bom, bom_id)
        if bom is None:
            raise HTTPException(status_code=404,
                                detail=f"BOM id={bom_id} introuvable.")
        lines = session.exec(
            select(BomLine).where(BomLine.id_bom == bom_id)
        ).all()
        if not lines:
            raise HTTPException(status_code=400,
                                detail="La BOM est vide.")

        # Phase 1 : verifier que TOUT est disponible. On accumule les
        # manques pour un message d'erreur exhaustif (l'utilisateur
        # voit d'un coup tout ce qui bloque).
        shortages = []
        for line in lines:
            stock = session.exec(
                select(Stock).where(Stock.id_parts == line.id_parts)
            ).first()
            current = stock.quantity if stock else 0
            needed = line.quantity * factor
            if current < needed:
                part = session.get(Parts, line.id_parts)
                shortages.append({
                    "id_parts": line.id_parts,
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

        # Phase 2 : appliquer les decrements
        changes = []
        for line in lines:
            stock = _get_or_create_stock(session, line.id_parts)
            delta = -(line.quantity * factor)
            stock.quantity += delta
            session.add(stock)
            changes.append({
                "id_parts": line.id_parts,
                "delta": delta,
                "new_quantity": stock.quantity,
            })
        session.commit()
        logger.info(f"BOM '{bom.code}' retiree x{factor} du stock "
                    f"({len(changes)} pieces affectees).")
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


@app.post("/api/v1/boms/from-assembly")
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


# ----------------------------------------------------------------------
#  ENDPOINTS API
# ----------------------------------------------------------------------
@app.get("/api/v1/parts")
def list_parts():
    """Liste enrichie (id + nom + projet + verrou) — utilise par le
    GUI de la macro FreeCAD pour filtrer par projet et bloquer les
    selections de pieces verrouillees."""
    with Session(engine) as session:
        # Pre-charger les codes projet pour eviter une requete par piece
        projects_by_id = {
            p.id: p.code
            for p in session.exec(select(Project)).all()
        }
        parts = session.exec(select(Parts).order_by(Parts.part_name)).all()
        return [
            {
                "id": p.id,
                "part_name": p.part_name,
                "id_project": p.id_project,
                "project_code": projects_by_id.get(p.id_project),
                "locked": p.locked,
            }
            for p in parts
        ]


@app.get("/api/v1/parts/full")
def list_parts_full(project_code: str | None = None):
    """Liste enrichie pour le dashboard frontend.
    Pour chaque piece : derniere revision PLM, infos de stock,
    projet associe, statut, verrou. Filtre optionnel par 'project_code'."""
    with Session(engine) as session:
        # Construction de la requete avec filtre optionnel
        query = select(Parts).order_by(Parts.part_name)
        if project_code:
            # Resoudre le code projet en id pour le where
            project = session.exec(
                select(Project).where(Project.code == project_code)
            ).first()
            if project is None:
                return []  # code projet inexistant -> liste vide
            query = query.where(Parts.id_project == project.id)
        parts = session.exec(query).all()

        # Pre-charger TOUS les projets dans un dict {id: code}
        # pour eviter une requete par piece.
        projects_by_id = {
            p.id: p.code
            for p in session.exec(select(Project)).all()
        }

        result = []
        for p in parts:
            # "Revision courante" : is_main si marquee, sinon la
            # plus recente par timestamp (cf. _get_current_plm).
            latest_plm = _get_current_plm(session, p.id)

            stock_row = session.exec(
                select(Stock).where(Stock.id_parts == p.id)
            ).first()

            result.append({
                "id": p.id,
                "part_name": p.part_name,
                # Champs ajoutes
                "id_project": p.id_project,
                "project_code": projects_by_id.get(p.id_project),
                "status": p.status,
                "locked": p.locked,
                "version": latest_plm.version if latest_plm else None,
                # URLs des fichiers PLM (relatives a la racine du serveur)
                "thumbnail_url": (
                    f"/{latest_plm.path_2_thumbnail}"
                    if latest_plm and latest_plm.path_2_thumbnail else None
                ),
                "glb_url": (
                    f"/{latest_plm.path_2_3dglb}"
                    if latest_plm and latest_plm.path_2_3dglb else None
                ),
                # URL du fichier CAO (.FCStd) : utilise par PiStock Explorer
                # pour telecharger et ouvrir la piece dans FreeCAD.
                "cad_url": (
                    f"/{latest_plm.path_2_cadfile}"
                    if latest_plm and latest_plm.path_2_cadfile else None
                ),
                "last_author": latest_plm.author if latest_plm else None,
                "last_timestamp": (
                    latest_plm.timestamp.isoformat()
                    if latest_plm else None
                ),
                "stock_img_url": (
                    f"/{stock_row.path_2_img}"
                    if stock_row and stock_row.path_2_img else None
                ),
                "quantity": stock_row.quantity if stock_row else None,
                "location": stock_row.location if stock_row else None,
                "supply": stock_row.supply if stock_row else None,
                "doc_url": (
                    f"/{stock_row.path_2_doc}"
                    if stock_row and stock_row.path_2_doc else None
                ),
            })
        return result


@app.get("/api/v1/parts/{part_id}")
def get_part(part_id: int):
    """Détail d'une pièce (utilisé par la page viewer 3D)."""
    with Session(engine) as session:
        p = session.get(Parts, part_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Pièce introuvable.")
        latest_plm = _get_current_plm(session, p.id)
        return {
            "id": p.id,
            "part_name": p.part_name,
            "glb_url": (
                f"/{latest_plm.path_2_3dglb}"
                if latest_plm and latest_plm.path_2_3dglb else None
            ),
            "thumbnail_url": (
                f"/{latest_plm.path_2_thumbnail}"
                if latest_plm and latest_plm.path_2_thumbnail else None
            ),
            "last_author": latest_plm.author if latest_plm else None,
            "last_timestamp": (
                latest_plm.timestamp.isoformat() if latest_plm else None
            ),
        }


@app.post("/api/v1/parts")
def create_part_manual(part_name: str = Form(...)):
    """Crée une pièce SANS passer par la CAO (pas de fichiers).
    Utilisé par le bouton "+ Nouvelle pièce" du dashboard.
    L'id est attribué automatiquement par SQLite."""
    part_name = part_name.strip()
    if not part_name:
        raise HTTPException(status_code=400,
                            detail="Le nom de la pièce est obligatoire.")

    with Session(engine) as session:
        # On verifie l'unicite du nom avant insertion (sinon on aurait
        # une IntegrityError peu parlante a renvoyer au frontend).
        existing = session.exec(
            select(Parts).where(Parts.part_name == part_name)
        ).first()
        if existing:
            raise HTTPException(
                status_code=409,  # 409 Conflict = ressource existe deja
                detail=f"Une pièce nommée '{part_name}' existe déjà "
                       f"(id={existing.id}).",
            )

        part = Parts(part_name=part_name)
        session.add(part)
        session.commit()
        session.refresh(part)
        logger.info(f"Pièce '{part_name}' créée manuellement (id={part.id}).")
        return {
            "status": "success",
            "id": part.id,
            "part_name": part.part_name,
        }


# ----------------------------------------------------------------------
#  ACTIONS PAR PIECE : projet / status / verrou
# ----------------------------------------------------------------------
# Toutes ces actions verifient le verrou (sauf le toggle du verrou
# lui-meme, evidemment). Si la piece est verrouillee, on renvoie 423.

VALID_STATUSES = {"Init", "Revue", "Asset"}


def _check_not_locked(part: Parts):
    if part.locked:
        raise HTTPException(
            status_code=423,  # 423 Locked
            detail=f"La pièce '{part.part_name}' est verrouillée. "
                   f"Déverrouillez-la avant de la modifier.",
        )


@app.post("/api/v1/parts/{part_id}/assign-project")
def assign_project(part_id: int,
                    project_id: int | None = Form(default=None)):
    """Associe (ou dissocie si project_id est null/absent) une piece
    a un projet. Refuse si la piece est verrouillee."""
    with Session(engine) as session:
        part = session.get(Parts, part_id)
        if part is None:
            raise HTTPException(status_code=404, detail="Pièce introuvable.")
        _check_not_locked(part)

        if project_id is not None:
            project = session.get(Project, project_id)
            if project is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Projet id={project_id} introuvable."
                )

        part.id_project = project_id
        session.add(part)
        session.commit()
        return {"status": "success", "id_project": part.id_project}


@app.post("/api/v1/parts/{part_id}/status")
def set_part_status(part_id: int, new_status: str = Form(...)):
    """Change le statut d'une piece (Init / Revue / Asset).
    Refuse si la piece est verrouillee."""
    if new_status not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Statut invalide. Valeurs autorisees : "
                   f"{', '.join(sorted(VALID_STATUSES))}"
        )
    with Session(engine) as session:
        part = session.get(Parts, part_id)
        if part is None:
            raise HTTPException(status_code=404, detail="Pièce introuvable.")
        _check_not_locked(part)
        part.status = new_status
        session.add(part)
        session.commit()
        return {"status": "success", "new_status": part.status}


@app.post("/api/v1/parts/{part_id}/lock")
def toggle_part_lock(part_id: int, locked: bool = Form(...)):
    """Toggle le verrou d'une piece. Pas de protection vs lui-meme :
    le verrou peut toujours etre modifie (sinon il serait impossible
    de le retirer une fois pose)."""
    with Session(engine) as session:
        part = session.get(Parts, part_id)
        if part is None:
            raise HTTPException(status_code=404, detail="Pièce introuvable.")
        part.locked = bool(locked)
        session.add(part)
        session.commit()
        return {"status": "success", "locked": part.locked}


@app.get("/api/v1/last-used-project")
def get_last_used_project():
    """Renvoie le projet de la PIECE creee le plus recemment qui a
    un projet associe. Utilise par l'UI pour pre-selectionner un
    projet quand on en assigne un a une nouvelle piece. None si
    aucune piece n'a encore de projet."""
    with Session(engine) as session:
        # 'id DESC' = ordre de creation inverse (id auto-incremente)
        part = session.exec(
            select(Parts)
            .where(Parts.id_project.is_not(None))
            .order_by(Parts.id.desc())
            .limit(1)
        ).first()
        if part is None:
            return {"id": None, "code": None}
        project = session.get(Project, part.id_project)
        if project is None:
            return {"id": None, "code": None}
        return {"id": project.id, "code": project.code}


# ----------------------------------------------------------------------
#  ENDPOINTS STOCK (quantite, location, supply, fiche composant)
# ----------------------------------------------------------------------
# A noter : on NE verifie PAS le verrou ici. Le verrou protege le
# design (projet, statut) ; le stock est de l'info operationnelle qu'on
# doit pouvoir mettre a jour meme sur une piece "Asset" verrouillee.

def _get_or_create_stock(session: Session, part_id: int) -> Stock:
    """Renvoie la ligne stock pour cette piece, la cree si absente."""
    stock_row = session.exec(
        select(Stock).where(Stock.id_parts == part_id)
    ).first()
    if stock_row is None:
        stock_row = Stock(id_parts=part_id)
        session.add(stock_row)
        session.flush()
    return stock_row


@app.get("/api/v1/parts/{part_id}/stock")
def get_part_stock(part_id: int):
    """Renvoie les infos de stock d'une piece. Si la ligne n'existe
    pas encore, on renvoie des valeurs par defaut (quantity=0, le
    reste a None) plutot que 404 : du point de vue de l'UI, toute
    piece a un stock (eventuellement vide)."""
    with Session(engine) as session:
        part = session.get(Parts, part_id)
        if part is None:
            raise HTTPException(status_code=404,
                                detail=f"Pièce id={part_id} introuvable.")
        stock_row = session.exec(
            select(Stock).where(Stock.id_parts == part_id)
        ).first()
        if stock_row is None:
            return {
                "part_id": part_id,
                "quantity": 0,
                "location": None,
                "supply": None,
                "stock_img_url": None,
                "doc_url": None,
            }
        return {
            "part_id": part_id,
            "quantity": stock_row.quantity,
            "location": stock_row.location,
            "supply": stock_row.supply,
            "stock_img_url": (f"/{stock_row.path_2_img}"
                               if stock_row.path_2_img else None),
            "doc_url": (f"/{stock_row.path_2_doc}"
                         if stock_row.path_2_doc else None),
        }


@app.post("/api/v1/parts/{part_id}/stock")
def update_part_stock(
    part_id: int,
    quantity: int = Form(default=0),
    location: str | None = Form(default=None),
    supply: str | None = Form(default=None),
):
    """Met a jour les infos de stock (quantite, location, supply).
    Cree la ligne stock si elle n'existe pas. Les chaines vides sont
    converties en NULL pour la coherence en base."""
    if quantity < 0:
        raise HTTPException(status_code=400,
                            detail="La quantité ne peut pas être négative.")

    # Normalise : "" -> None
    location = (location or "").strip() or None
    supply = (supply or "").strip() or None

    with Session(engine) as session:
        part = session.get(Parts, part_id)
        if part is None:
            raise HTTPException(status_code=404,
                                detail=f"Pièce id={part_id} introuvable.")
        stock_row = _get_or_create_stock(session, part_id)
        stock_row.quantity = quantity
        stock_row.location = location
        stock_row.supply = supply
        session.add(stock_row)
        session.commit()
        logger.info(f"Stock piece {part_id} : qty={quantity} "
                    f"loc={location} supply={supply}")
        return {
            "status": "success",
            "quantity": stock_row.quantity,
            "location": stock_row.location,
            "supply": stock_row.supply,
        }


@app.post("/api/v1/parts/{part_id}/stock-doc")
async def upload_stock_doc(part_id: int, doc: UploadFile = File(...)):
    """Upload (ou remplace) la fiche composant d'une piece. Le fichier
    va dans data-pistock/uploads/doc/ avec un suffixe timestamp pour
    eviter les collisions tout en gardant le nom original lisible."""
    with Session(engine) as session:
        part = session.get(Parts, part_id)
        if part is None:
            raise HTTPException(status_code=404,
                                detail=f"Pièce id={part_id} introuvable.")

        # Nom final : "<basename>_<timestamp>.<ext>".
        # On garde le nom d'origine pour l'identification visuelle.
        ts_tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        original = doc.filename or "fiche.pdf"
        base, ext = os.path.splitext(original)
        if not ext:
            ext = ".pdf"
        # Sanitisation legere du basename pour eviter les caracteres
        # problematiques sur disque.
        safe_base = "".join(c if c.isalnum() or c in "-_." else "_"
                             for c in base) or "fiche"
        stamped_name = f"{safe_base}_{ts_tag}{ext}"

        dest_dir = os.path.join(DATA_DIR, "uploads", "doc")
        os.makedirs(dest_dir, exist_ok=True)
        file_path = os.path.join(dest_dir, stamped_name)
        with open(file_path, "wb") as buffer:
            copyfileobj(doc.file, buffer)
        rel_path = f"uploads/doc/{stamped_name}"
        logger.info(f"Fiche composant sauvegardee : {file_path}")

        stock_row = _get_or_create_stock(session, part_id)
        stock_row.path_2_doc = rel_path
        session.add(stock_row)
        session.commit()

        return {
            "status": "success",
            "part_id": part_id,
            "doc_url": f"/{rel_path}",
            "filename": stamped_name,
        }


@app.post("/api/v1/parts/{part_id}/stock-photo")
async def upload_stock_photo(part_id: int, photo: UploadFile = File(...)):
    """Ajoute (ou remplace) la photo de stock d'une piece.
    Le fichier est sauvegarde sous data-pistock/uploads/img/stock_<id>_<ts>.<ext>
    et le chemin est stocke dans la table 'stock'. Si aucune ligne
    stock n'existe encore pour cette piece, on en cree une."""
    with Session(engine) as session:
        part = session.get(Parts, part_id)
        if part is None:
            raise HTTPException(status_code=404,
                                detail=f"Aucune pièce avec l'id {part_id}.")

        # Sauvegarde du fichier sur disque dans uploads/stkimg/.
        # Ce dossier est dedie aux photos de pieces "en stock" (prises
        # au telephone, scannees, etc.), distinct de uploads/img/ qui
        # contient les vignettes CAO generees par FreeCAD.
        ts_tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        _, ext = os.path.splitext(photo.filename or "")
        if not ext:
            ext = ".jpg"  # fallback raisonnable
        stamped_name = f"stock_{part_id}_{ts_tag}{ext}"
        dest_dir = os.path.join(DATA_DIR, "uploads", "stkimg")
        os.makedirs(dest_dir, exist_ok=True)
        file_path = os.path.join(dest_dir, stamped_name)
        with open(file_path, "wb") as buffer:
            copyfileobj(photo.file, buffer)
        rel_path = f"uploads/stkimg/{stamped_name}"
        logger.info(f"Photo stock sauvegardée : {file_path}")

        # Mise a jour (ou creation) de la ligne stock
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

        return {
            "status": "success",
            "part_id": part_id,
            "stock_img_url": f"/{rel_path}",
        }


# ----------------------------------------------------------------------
#  ENDPOINTS REVISIONS PLM (liste, suppression, set-main)
# ----------------------------------------------------------------------
@app.get("/api/v1/parts/{part_id}/revisions")
def list_part_revisions(part_id: int):
    """Liste toutes les revisions PLM d'une piece, de la plus recente
    a la plus ancienne. Marque celle qui est "courante" (is_main si
    elle existe, sinon la plus recente)."""
    with Session(engine) as session:
        part = session.get(Parts, part_id)
        if part is None:
            raise HTTPException(status_code=404,
                                detail=f"Pièce id={part_id} introuvable.")
        revisions = session.exec(
            select(PLM)
            .where(PLM.id_parts == part_id)
            .order_by(PLM.timestamp.desc())
        ).all()
        current = _get_current_plm(session, part_id)
        current_id = current.id if current else None
        return [
            {
                "id": r.id,
                "version": r.version,
                "timestamp": r.timestamp.isoformat(),
                "author": r.author,
                "is_main": r.is_main,
                "is_current": (r.id == current_id),
                "glb_url": (f"/{r.path_2_3dglb}"
                             if r.path_2_3dglb else None),
                "thumbnail_url": (f"/{r.path_2_thumbnail}"
                                   if r.path_2_thumbnail else None),
            }
            for r in revisions
        ]


def _delete_file_if_exists(rel_path: str | None):
    """Supprime un fichier sur disque a partir d'un chemin relatif
    a DATA_DIR. Silencieux si le fichier n'existe pas ou en cas
    d'erreur d'I/O (on prefere ne pas planter pour ca)."""
    if not rel_path:
        return
    abs_path = os.path.join(DATA_DIR, rel_path)
    try:
        if os.path.isfile(abs_path):
            os.remove(abs_path)
            logger.info(f"Fichier supprime : {abs_path}")
    except OSError as e:
        logger.warning(f"Impossible de supprimer {abs_path} : {e}")


@app.delete("/api/v1/plm/{plm_id}")
def delete_plm_revision(plm_id: int):
    """Supprime une revision PLM : la ligne en base ET les fichiers
    associes sur disque (.FCStd, .glb, .png). Refuse si la piece
    est verrouillee."""
    with Session(engine) as session:
        plm = session.get(PLM, plm_id)
        if plm is None:
            raise HTTPException(status_code=404,
                                detail=f"Révision PLM id={plm_id} introuvable.")
        part = session.get(Parts, plm.id_parts)
        if part is not None:
            _check_not_locked(part)

        # Supprimer les fichiers AVANT de detruire la ligne, pour
        # avoir les chemins disponibles.
        _delete_file_if_exists(plm.path_2_cadfile)
        _delete_file_if_exists(plm.path_2_thumbnail)
        _delete_file_if_exists(plm.path_2_3dglb)

        session.delete(plm)
        session.commit()
        logger.info(f"Révision PLM {plm_id} supprimée (piece {part.part_name if part else '?'}).")
        return {"status": "success", "deleted_id": plm_id}


@app.post("/api/v1/plm/{plm_id}/set-main")
def set_plm_main(plm_id: int):
    """Marque cette revision comme "principale" (is_main=True) et
    deflaggent toutes les autres revisions de la meme piece. Refuse
    si la piece est verrouillee."""
    with Session(engine) as session:
        plm = session.get(PLM, plm_id)
        if plm is None:
            raise HTTPException(status_code=404,
                                detail=f"Révision PLM id={plm_id} introuvable.")
        part = session.get(Parts, plm.id_parts)
        if part is not None:
            _check_not_locked(part)

        # Reset is_main sur toutes les autres revisions de cette piece,
        # puis flagger celle-ci. Tout dans la meme transaction.
        others = session.exec(
            select(PLM)
            .where(PLM.id_parts == plm.id_parts)
            .where(PLM.id != plm_id)
            .where(PLM.is_main == True)  # noqa: E712
        ).all()
        for o in others:
            o.is_main = False
            session.add(o)
        plm.is_main = True
        session.add(plm)
        session.commit()
        logger.info(f"Révision PLM {plm_id} (v{plm.version}) marquee principale.")
        return {"status": "success", "id": plm_id, "is_main": True}


@app.post("/api/v1/parts/upload")
async def upload_new_part(
    part_id: int | None = Form(default=None),
    part_name: str | None = Form(default=None),
    author: str = Form(...),
    cad_file: UploadFile = File(...),
    thumbnail_file: UploadFile = File(...),
    glb_file: UploadFile = File(...),
):
    try:
        if part_id is None and not part_name:
            raise HTTPException(
                status_code=400,
                detail="Il faut fournir soit 'part_id' (pièce "
                       "existante), soit 'part_name' (nouvelle pièce).",
            )

        # --- PRE-CHECK : verrou ---------------------------------------
        # On verifie le verrou AVANT de sauver les fichiers : eviter
        # d'ecrire des fichiers orphelins si la piece est verrouillee.
        # Couvre les deux cas : part_id direct OU part_name qui matche
        # une piece existante (fallback de reutilisation).
        with Session(engine) as quick_session:
            target_part = None
            if part_id is not None:
                target_part = quick_session.get(Parts, part_id)
                if target_part is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Aucune pièce avec l'id {part_id}.",
                    )
            elif part_name:
                target_part = quick_session.exec(
                    select(Parts).where(Parts.part_name == part_name)
                ).first()
                # Si target_part est None, c'est une nouvelle piece -> OK
            if target_part is not None and target_part.locked:
                raise HTTPException(
                    status_code=423,  # 423 Locked
                    detail=f"La pièce '{target_part.part_name}' est "
                           f"verrouillée. Impossible d'ajouter une "
                           f"nouvelle révision PLM.",
                )

        ts_dt = datetime.now(timezone.utc)
        ts_tag = ts_dt.strftime("%Y%m%d_%H%M%S")
        logger.info(f"Timestamp de l'enregistrement : {ts_tag}")

        saved_paths = {}
        for file_type, upload_file, sub_folder in [
            ("cad", cad_file, "cad"),
            ("img", thumbnail_file, "img"),
            ("glb", glb_file, "cad"),
        ]:
            dest_dir = os.path.join(DATA_DIR, "uploads", sub_folder)
            os.makedirs(dest_dir, exist_ok=True)

            base_name, extension = os.path.splitext(upload_file.filename)
            stamped_name = f"{base_name}_{ts_tag}{extension}"

            file_path = os.path.join(dest_dir, stamped_name)
            with open(file_path, "wb") as buffer:
                copyfileobj(upload_file.file, buffer)

            saved_paths[file_type] = f"uploads/{sub_folder}/{stamped_name}"
            logger.info(f"Fichier sauvegarde : {file_path}")

        with Session(engine) as session:
            if part_id is not None:
                part = session.get(Parts, part_id)
                if part is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Aucune pièce avec l'id {part_id}.",
                    )
                part_created = False
                logger.info(f"Pièce existante sélectionnée : "
                            f"'{part.part_name}' (id={part.id}).")
            else:
                existing = session.exec(
                    select(Parts).where(Parts.part_name == part_name)
                ).first()
                if existing:
                    part = existing
                    part_created = False
                    logger.info(f"Pièce '{part_name}' déjà connue "
                                f"(id={part.id}), réutilisation.")
                else:
                    part = Parts(part_name=part_name)
                    session.add(part)
                    session.flush()
                    part_created = True
                    logger.info(f"Nouvelle pièce '{part_name}' "
                                f"créée (id={part.id}).")

            # Calcul de la prochaine version PLM pour cette piece.
            # Doit etre fait APRES le flush (pour que part.id existe)
            # mais AVANT la creation de la ligne PLM.
            new_version = _next_version_for_part(session, part.id)

            new_plm = PLM(
                id_parts=part.id,
                path_2_cadfile=saved_paths["cad"],
                path_2_thumbnail=saved_paths["img"],
                path_2_3dglb=saved_paths["glb"],
                timestamp=ts_dt,
                author=author,
                version=new_version,
            )
            session.add(new_plm)
            session.commit()

            part_id_final = part.id
            part_name_final = part.part_name
            plm_id = new_plm.id
            plm_version = new_plm.version

        return {
            "status": "success",
            "part_id": part_id_final,
            "part_name": part_name_final,
            "plm_id": plm_id,
            "plm_version": plm_version,
            "part_created": part_created,
            "author": author,
            "timestamp": ts_dt.isoformat(),
            "message": (
                f"Part '{part_name_final}' successfully cataloged!"
                if part_created
                else f"New PLM revision added to part "
                     f"'{part_name_final}'."
            ),
        }

    except HTTPException:
        raise
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"Erreur lors de l'upload :\n{tb}")
        raise HTTPException(status_code=500, detail=str(e))


# ----------------------------------------------------------------------
#  FICHIERS STATIQUES + INTERFACE NiceGUI
# ----------------------------------------------------------------------
# 1. Les fichiers uploadés (vignettes .png, modèles .glb...) sont servis
#    sous /uploads/. C'est utilisé à la fois par l'interface NiceGUI
#    (pour afficher les images) et par le viewer 3D (qui charge le .glb
#    via une URL HTTP, pas un chemin disque).
uploads_root = os.path.join(DATA_DIR, "uploads")
app.mount("/uploads", StaticFiles(directory=uploads_root), name="uploads")

# 2. Assets statiques du frontend (model-viewer.min.js, etc.)
#    Permet de servir des libs JS en local plutot que via un CDN
#    -> autonomie complete sans internet, et meilleur controle.
FRONTEND_STATIC = os.path.abspath(
    os.path.join(BASE_DIR, "../../frontend/static")
)
if os.path.isdir(FRONTEND_STATIC):
    app.mount("/static", StaticFiles(directory=FRONTEND_STATIC),
              name="frontend_static")
    logger.info(f"Static frontend assets servis depuis {FRONTEND_STATIC}")
else:
    logger.warning(f"Dossier static frontend introuvable : {FRONTEND_STATIC}. "
                    f"Le viewer 3D essaiera de charger depuis CDN.")

# 2. L'interface NiceGUI est définie dans frontend/ui.py et s'attache
#    au MEME FastAPI 'app'. Donc tout tourne sur le meme port :
#    - http://127.0.0.1:8000/       -> dashboard NiceGUI
#    - http://127.0.0.1:8000/api/v1 -> endpoints REST (utilises par la macro)
#    - http://127.0.0.1:8000/uploads/... -> fichiers statiques
import sys
FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, "../../frontend"))
if FRONTEND_DIR not in sys.path:
    sys.path.insert(0, FRONTEND_DIR)

try:
    # ui_module enregistre ses pages sur 'app' via @ui.page(...) et
    # appelle ui.run_with(app) pour brancher NiceGUI sur FastAPI.
    import ui as ui_module  # noqa: F401  (l'import suffit a tout enregistrer)
    logger.info("Interface NiceGUI chargee.")
except ImportError as e:
    logger.warning(f"Impossible de charger l'UI NiceGUI : {e}")


# uvicorn main:app --reload --host 0.0.0.0 --port 8000
