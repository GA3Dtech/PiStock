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
import hashlib
import secrets
import logging
import traceback
from datetime import datetime, timezone
from shutil import copyfileobj
from fastapi import (FastAPI, UploadFile, File, Form, HTTPException,
                      Header, Depends)
from fastapi.staticfiles import StaticFiles
from sqlmodel import SQLModel, Session, create_engine, select
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
# Le schema de la base est defini dans model.py (SOURCE UNIQUE DE
# VERITE), partage avec init_db.py. On les re-exporte ici pour que le
# reste du code — ainsi que l'UI et les plugins — puisse continuer a
# faire `main.Parts`, `main.Bom`, etc.
from model import (  # noqa: E402
    Parts, PLM, Stock, Project, Bom, BomLine, Admin,
)


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


# ----------------------------------------------------------------------
#  AUTHENTIFICATION ADMIN (mot de passe singleton)
# ----------------------------------------------------------------------
# Le compte admin est unique. Mot de passe stocke en PBKDF2-HMAC-SHA256
# (200_000 iter, sel aleatoire 16 octets). Uniquement stdlib, aucune
# dependance ajoutee.
#
#   - Cree au premier demarrage via POST /api/v1/admin/setup
#     (refuse si un admin existe deja).
#   - Renouvelable via POST /api/v1/admin/change-password (necessite
#     l'ancien mot de passe).
#
# Les endpoints destructifs (DELETE *) et le DEVERROUILLAGE d'une
# piece exigent le header HTTP `X-Admin-Password`. Suffisant en LAN
# avec HTTPS (cert auto-signe) ; pour de l'expose internet, prevoir
# un vrai jeton de session.
PBKDF2_ITER = 200_000


def _new_salt() -> bytes:
    return secrets.token_bytes(16)


def _hash_password(password: str, salt: bytes) -> str:
    """PBKDF2-HMAC-SHA256, retourne le hash en hex."""
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 salt, PBKDF2_ITER).hex()


def _verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    """Comparaison en temps constant (anti timing-attack)."""
    expected = _hash_password(password, bytes.fromhex(salt_hex))
    return secrets.compare_digest(expected, hash_hex)


def _get_admin(session: Session):
    return session.exec(select(Admin)).first()


def _check_admin_password(password):
    """Valide un mot de passe admin. Leve 401/403/503 sinon.
    Utile en code (verifications conditionnelles, ex. deverrouillage).
    Pour proteger un endpoint entier, utiliser plutot _require_admin
    en Depends."""
    if not password:
        raise HTTPException(
            status_code=401,
            detail=("Authentification admin requise "
                     "(header X-Admin-Password).")
        )
    with Session(engine) as session:
        admin = _get_admin(session)
        if admin is None:
            raise HTTPException(
                status_code=503,
                detail="Compte admin non configure (POST /admin/setup)."
            )
        if not _verify_password(password, admin.salt, admin.password_hash):
            raise HTTPException(status_code=403,
                                detail="Mot de passe admin invalide.")


def _require_admin(
    x_admin_password: str | None = Header(default=None),
) -> None:
    """Dependance FastAPI : impose le header X-Admin-Password valide."""
    _check_admin_password(x_admin_password)


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


# ----------------------------------------------------------------------
#  ENDPOINTS API : ADMIN (singleton, mot de passe)
# ----------------------------------------------------------------------
@app.get("/api/v1/admin/status")
def admin_status():
    """Indique si un compte admin existe. Utilise par l'UI pour
    declencher le dialogue de setup au premier lancement."""
    with Session(engine) as session:
        return {"configured": _get_admin(session) is not None}


@app.post("/api/v1/admin/setup")
def admin_setup(password: str = Form(...)):
    """Cree le compte admin au PREMIER lancement uniquement.
    409 si un admin existe deja (utiliser change-password)."""
    if len(password) < 6:
        raise HTTPException(
            status_code=400,
            detail="Le mot de passe doit faire au moins 6 caracteres."
        )
    with Session(engine) as session:
        if _get_admin(session) is not None:
            raise HTTPException(
                status_code=409,
                detail=("Un compte admin existe deja. "
                         "Utilisez /admin/change-password.")
            )
        salt = _new_salt()
        admin = Admin(salt=salt.hex(),
                      password_hash=_hash_password(password, salt))
        session.add(admin); session.commit()
        logger.info("Compte admin cree.")
        return {"status": "success"}


@app.post("/api/v1/admin/verify")
def admin_verify(password: str = Form(...)):
    """Verifie un mot de passe admin. Utilise par l'UI pour le login."""
    _check_admin_password(password)   # leve 401/403/503 si invalide
    return {"status": "success"}


@app.post("/api/v1/admin/change-password")
def admin_change_password(
    current_password: str = Form(...),
    new_password: str = Form(...),
):
    """Renouvelle le mot de passe admin. Necessite l'ancien."""
    if len(new_password) < 6:
        raise HTTPException(
            status_code=400,
            detail=("Le nouveau mot de passe doit faire au moins "
                     "6 caracteres.")
        )
    with Session(engine) as session:
        admin = _get_admin(session)
        if admin is None:
            raise HTTPException(
                status_code=503,
                detail="Compte admin non configure (POST /admin/setup)."
            )
        if not _verify_password(current_password, admin.salt,
                                 admin.password_hash):
            raise HTTPException(status_code=403,
                                detail="Mot de passe actuel invalide.")
        new_salt = _new_salt()
        admin.salt = new_salt.hex()
        admin.password_hash = _hash_password(new_password, new_salt)
        admin.updated_at = datetime.now(timezone.utc).isoformat()
        session.add(admin); session.commit()
        logger.info("Mot de passe admin renouvele.")
        return {"status": "success"}


# ----------------------------------------------------------------------
#  ENDPOINT API : SUPPRESSION DE PROJET (admin requis)
# ----------------------------------------------------------------------
@app.delete("/api/v1/projects/{project_id}")
def delete_project(
    project_id: int,
    _admin: None = Depends(_require_admin),
):
    """Supprime un projet. REFUSE (409) si des pieces ou des BOMs
    y sont encore rattachees (meme principe que les pieces dans
    les BOMs). Necessite le header X-Admin-Password."""
    with Session(engine) as session:
        project = session.get(Project, project_id)
        if project is None:
            raise HTTPException(
                status_code=404,
                detail=f"Projet id={project_id} introuvable."
            )
        parts_left = session.exec(
            select(Parts).where(Parts.id_project == project_id)
        ).all()
        boms_left = session.exec(
            select(Bom).where(Bom.id_project == project_id)
        ).all()
        if parts_left or boms_left:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        f"Impossible de supprimer le projet "
                        f"'{project.code}' : "
                        f"{len(parts_left)} pieces(s) et "
                        f"{len(boms_left)} BOM(s) y sont encore "
                        f"rattachees."
                    ),
                    "parts": [
                        {"id": p.id, "part_name": p.part_name}
                        for p in parts_left
                    ],
                    "boms": [
                        {"id": b.id, "code": b.code,
                         "description": b.description}
                        for b in boms_left
                    ],
                },
            )
        code = project.code
        session.delete(project); session.commit()
        logger.info(f"Projet '{code}' (id={project_id}) supprime.")
        return {"status": "success", "deleted_id": project_id}


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


@app.post("/api/v1/boms/{bom_id}/lines")
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


@app.post("/api/v1/boms/{bom_id}/stock-sub")
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
def toggle_part_lock(
    part_id: int,
    locked: bool = Form(...),
    x_admin_password: str | None = Header(default=None),
):
    """Toggle le verrou d'une piece. Pas de protection vs lui-meme :
    le verrou peut toujours etre modifie (sinon il serait impossible
    de le retirer une fois pose)."""
    with Session(engine) as session:
        part = session.get(Parts, part_id)
        if part is None:
            raise HTTPException(status_code=404, detail="Pièce introuvable.")
        new_locked = bool(locked)
        # Le DEVERROUILLAGE exige une auth admin ; le verrouillage
        # reste libre (lock-down rapide en cas de besoin).
        if part.locked and not new_locked:
            _check_admin_password(x_admin_password)
        part.locked = new_locked
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
def delete_plm_revision(plm_id: int,
                         _admin: None = Depends(_require_admin)):
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


@app.delete("/api/v1/parts/{part_id}")
def delete_part(part_id: int,
                _admin: None = Depends(_require_admin)):
    """Supprime DEFINITIVEMENT une piece de la base :
    - Refus (409) si la piece est referencee dans une ou plusieurs BOMs
      (avec la liste des BOMs concernees dans le detail)
    - Sinon : supprime toutes les revisions PLM associees (avec leurs
      fichiers : .FCStd, .glb, .png thumb), l'entree Stock (avec sa
      photo et son doc), puis la Part elle-meme.
    Les fichiers manquants sur disque sont logges mais ne bloquent pas
    l'operation (idempotence)."""
    with Session(engine) as session:
        part = session.get(Parts, part_id)
        if part is None:
            raise HTTPException(
                status_code=404,
                detail=f"Pièce id={part_id} introuvable."
            )

        # Verification : la piece est-elle utilisee dans une BOM ?
        # On regarde les BomLine qui pointent vers cette piece (en tant
        # que id_parts, pas en tant que id_subbom evidemment).
        used_in = session.exec(
            select(Bom).join(BomLine, BomLine.id_bom == Bom.id)
            .where(BomLine.id_parts == part_id)
            .distinct()
        ).all()
        if used_in:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (f"Impossible de supprimer la pièce "
                                 f"'{part.part_name}' : elle est utilisée "
                                 f"dans {len(used_in)} BOM(s)."),
                    "boms": [
                        {"id": b.id, "code": b.code,
                         "description": b.description}
                        for b in used_in
                    ]
                }
            )

        # Suppression en cascade des revisions PLM (+ fichiers physiques)
        plm_rows = session.exec(
            select(PLM).where(PLM.id_parts == part_id)
        ).all()
        for plm in plm_rows:
            _delete_file_if_exists(plm.path_2_cadfile)
            _delete_file_if_exists(plm.path_2_thumbnail)
            _delete_file_if_exists(plm.path_2_3dglb)
            session.delete(plm)

        # Suppression de la ligne Stock (+ photo + doc)
        stock = session.exec(
            select(Stock).where(Stock.id_parts == part_id)
        ).first()
        if stock is not None:
            _delete_file_if_exists(stock.path_2_img)
            _delete_file_if_exists(stock.path_2_doc)
            session.delete(stock)

        # Suppression de la Part elle-meme
        part_name = part.part_name
        session.delete(part)
        session.commit()
        logger.info(
            f"Pièce '{part_name}' (id={part_id}) supprimée "
            f"({len(plm_rows)} révisions PLM, "
            f"stock {'oui' if stock else 'non'})."
        )
        return {
            "status": "success",
            "deleted_id": part_id,
            "deleted_part_name": part_name,
            "plm_revisions_removed": len(plm_rows),
            "stock_removed": stock is not None,
        }


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
