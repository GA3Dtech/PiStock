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
Endpoints STOCK (quantite, location, supply, photo, fiche composant).

A noter : on NE verifie PAS le verrou ici. Le verrou protege le design
(projet, statut) ; le stock est de l'info operationnelle qu'on doit
pouvoir mettre a jour meme sur une piece "Asset" verrouillee.

Le helper `_get_or_create_stock` est aussi reutilise par les operations
de stock des BOMs (services/boms.py).
"""
import os
from datetime import datetime, timezone
from shutil import copyfileobj

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from sqlmodel import Session, select

from config import engine, logger, DATA_DIR
from model import Parts, Stock

router = APIRouter()


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


@router.get("/api/v1/parts/{part_id}/stock")
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


@router.post("/api/v1/parts/{part_id}/stock")
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


@router.post("/api/v1/parts/{part_id}/stock-doc")
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


@router.post("/api/v1/parts/{part_id}/stock-photo")
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
