import os
import logging
import traceback
from shutil import copyfileobj
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from sqlmodel import SQLModel, Field, Session, create_engine

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


# Définition minimale des modèles pour l'insertion
class Parts(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)


class PLM(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    id_parts: int = Field(foreign_key="parts.id")
    path_2_cadfile: str | None = None
    path_2_thumbnail: str | None = None
    path_2_3dglb: str | None = None


@app.on_event("startup")
def on_startup():
    # Cree les tables si elles n'existent pas encore
    SQLModel.metadata.create_all(engine)
    logger.info("Base de donnees initialisee.")


@app.post("/api/v1/parts/upload")
async def upload_new_part(
    part_name: str = Form(...),
    cad_file: UploadFile = File(...),
    thumbnail_file: UploadFile = File(...),
    glb_file: UploadFile = File(...),
):
    try:
        # 1. Sauvegarde des fichiers physiques sur le disque
        saved_paths = {}
        for file_type, upload_file in [
            ("cad", cad_file),
            ("img", thumbnail_file),
            ("glb", glb_file),
        ]:
            # Choix du sous-dossier de destination dans /data/uploads/
            sub_folder = "cad" if file_type == "img" else "cad"
            dest_dir = os.path.join(DATA_DIR, "uploads", sub_folder)
            os.makedirs(dest_dir, exist_ok=True)  # securite supplementaire

            file_path = os.path.join(dest_dir, upload_file.filename)
            with open(file_path, "wb") as buffer:
                copyfileobj(upload_file.file, buffer)

            # Stockage du chemin relatif pour la base de donnees
            saved_paths[file_type] = f"uploads/{sub_folder}/{upload_file.filename}"
            logger.info(f"Fichier sauvegarde : {file_path}")

        # 2. Insertion dans la base de donnees SQLite via SQLModel
        with Session(engine) as session:
            # Etape A : On cree une nouvelle entree dans 'parts' pour obtenir un ID
            new_part = Parts()
            session.add(new_part)
            session.flush()  # genere l'ID sans committer definitivement

            # Etape B : On lie les fichiers a cet ID dans la table 'plm'
            new_plm = PLM(
                id_parts=new_part.id,
                path_2_cadfile=saved_paths["cad"],
                path_2_thumbnail=saved_paths["img"],
                path_2_3dglb=saved_paths["glb"],
            )
            session.add(new_plm)
            session.commit()  # commit unique : les deux lignes, ou aucune

            # On capture l'ID AVANT de sortir du bloc with
            # (sinon DetachedInstanceError : l'objet n'est plus lie a la session)
            new_part_id = new_part.id

        return {
            "status": "success",
            "part_id": new_part_id,
            "message": f"Part '{part_name}' successfully cataloged!",
        }

    except Exception as e:
        # On logge le traceback complet cote serveur pour le debug
        tb = traceback.format_exc()
        logger.error(f"Erreur lors de l'upload :\n{tb}")
        raise HTTPException(status_code=500, detail=str(e))


# uvicorn main:app --reload --host 0.0.0.0 --port 8000