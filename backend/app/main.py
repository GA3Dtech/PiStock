import os
import logging
import traceback
from datetime import datetime, timezone
from shutil import copyfileobj
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from sqlmodel import SQLModel, Field, Session, create_engine, select

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


class Stock(SQLModel, table=True):
    __tablename__ = "stock"
    id: int | None = Field(default=None, primary_key=True)
    id_parts: int = Field(foreign_key="parts.id")
    path_2_img: str | None = None
    quantity: int = Field(default=0)
    location: str | None = None
    supply: str | None = None


class Project(SQLModel, table=True):
    __tablename__ = "project"
    id: int | None = Field(default=None, primary_key=True)
    # Code alphabetique a 3 lettres (AAA, AAB...), incremente
    # automatiquement par le serveur. Unique : sert d'identifiant
    # lisible pour l'utilisateur.
    code: str = Field(index=True, unique=True, max_length=3)
    description: str | None = None


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
#  ENDPOINTS API
# ----------------------------------------------------------------------
@app.get("/api/v1/parts")
def list_parts():
    """Liste minimale (id + nom) — utilisé par le GUI de la macro."""
    with Session(engine) as session:
        parts = session.exec(select(Parts).order_by(Parts.part_name)).all()
        return [{"id": p.id, "part_name": p.part_name} for p in parts]


@app.get("/api/v1/parts/full")
def list_parts_full():
    """Liste enrichie pour le dashboard frontend :
    pour chaque pièce, on renvoie la DERNIERE revision PLM (la plus
    récente par timestamp) et les infos de stock si disponibles."""
    with Session(engine) as session:
        parts = session.exec(select(Parts).order_by(Parts.part_name)).all()
        result = []
        for p in parts:
            # Derniere revision PLM (timestamp le plus recent)
            latest_plm = session.exec(
                select(PLM)
                .where(PLM.id_parts == p.id)
                .order_by(PLM.timestamp.desc())
            ).first()

            # Premiere ligne de stock liee a cette piece (s'il y en a une).
            # On reste simple : une piece = une ligne de stock attendue.
            stock_row = session.exec(
                select(Stock).where(Stock.id_parts == p.id)
            ).first()

            result.append({
                "id": p.id,
                "part_name": p.part_name,
                # URLs relatives a la racine du serveur (servies par /uploads/)
                "thumbnail_url": (
                    f"/{latest_plm.path_2_thumbnail}"
                    if latest_plm and latest_plm.path_2_thumbnail else None
                ),
                "glb_url": (
                    f"/{latest_plm.path_2_3dglb}"
                    if latest_plm and latest_plm.path_2_3dglb else None
                ),
                "last_author": latest_plm.author if latest_plm else None,
                "last_timestamp": (
                    latest_plm.timestamp.isoformat()
                    if latest_plm else None
                ),
                # Infos de stock (None si la piece n'a pas encore de stock)
                "stock_img_url": (
                    f"/{stock_row.path_2_img}"
                    if stock_row and stock_row.path_2_img else None
                ),
                "quantity": stock_row.quantity if stock_row else None,
                "location": stock_row.location if stock_row else None,
            })
        return result


@app.get("/api/v1/parts/{part_id}")
def get_part(part_id: int):
    """Détail d'une pièce (utilisé par la page viewer 3D)."""
    with Session(engine) as session:
        p = session.get(Parts, part_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Pièce introuvable.")
        latest_plm = session.exec(
            select(PLM)
            .where(PLM.id_parts == p.id)
            .order_by(PLM.timestamp.desc())
        ).first()
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

            new_plm = PLM(
                id_parts=part.id,
                path_2_cadfile=saved_paths["cad"],
                path_2_thumbnail=saved_paths["img"],
                path_2_3dglb=saved_paths["glb"],
                timestamp=ts_dt,
                author=author,
            )
            session.add(new_plm)
            session.commit()

            part_id_final = part.id
            part_name_final = part.part_name
            plm_id = new_plm.id

        return {
            "status": "success",
            "part_id": part_id_final,
            "part_name": part_name_final,
            "plm_id": plm_id,
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
