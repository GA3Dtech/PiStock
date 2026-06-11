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
PARTS endpoints: listing (simple and enriched), detail, manual creation,
actions (project / status / lock), PLM revisions (list, deletion,
set-main) and upload of a new revision from FreeCAD.

MIND THE ORDER of the routes: `/api/v1/parts/full` is declared BEFORE
`/api/v1/parts/{part_id}` so that it is not captured by the path
parameter. The original declaration order is therefore preserved.
"""
import os
import traceback
from datetime import datetime, timezone
from shutil import copyfileobj

from fastapi import (APIRouter, UploadFile, File, Form, HTTPException,
                      Header, Depends)
from sqlmodel import Session, select

from config import engine, logger, DATA_DIR, _delete_file_if_exists
from model import Parts, PLM, Stock, Project, Bom, BomLine
from services.codes import _get_current_plm, _next_version_for_part
from services.admin import _check_admin_password, _require_admin

router = APIRouter()


@router.get("/api/v1/parts")
def list_parts():
    """Enriched listing (id + name + project + lock) — used by the
    FreeCAD macro GUI to filter by project and block the selection of
    locked parts."""
    with Session(engine) as session:
        # Preload the project codes to avoid one query per part
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
                "info": p.info,
            }
            for p in parts
        ]


@router.get("/api/v1/parts/full")
def list_parts_full(project_code: str | None = None):
    """Enriched listing for the frontend dashboard.
    For each part: latest PLM revision, stock info, associated project,
    status, lock. Optional filter by 'project_code'."""
    with Session(engine) as session:
        # Build the query with an optional filter
        query = select(Parts).order_by(Parts.part_name)
        if project_code:
            # Resolve the project code into an id for the where clause
            project = session.exec(
                select(Project).where(Project.code == project_code)
            ).first()
            if project is None:
                return []  # non-existent project code -> empty list
            query = query.where(Parts.id_project == project.id)
        parts = session.exec(query).all()

        # Preload ALL the projects into a dict {id: code} to avoid one
        # query per part.
        projects_by_id = {
            p.id: p.code
            for p in session.exec(select(Project)).all()
        }

        result = []
        for p in parts:
            # "Current revision": is_main if flagged, otherwise the
            # most recent one by timestamp (see _get_current_plm).
            latest_plm = _get_current_plm(session, p.id)

            stock_row = session.exec(
                select(Stock).where(Stock.id_parts == p.id)
            ).first()

            result.append({
                "id": p.id,
                "part_name": p.part_name,
                # Added fields
                "id_project": p.id_project,
                "project_code": projects_by_id.get(p.id_project),
                "status": p.status,
                "locked": p.locked,
                "info": p.info,
                "version": latest_plm.version if latest_plm else None,
                # PLM file URLs (relative to the server root)
                "thumbnail_url": (
                    f"/{latest_plm.path_2_thumbnail}"
                    if latest_plm and latest_plm.path_2_thumbnail else None
                ),
                "glb_url": (
                    f"/{latest_plm.path_2_3dglb}"
                    if latest_plm and latest_plm.path_2_3dglb else None
                ),
                # CAD file URL (.FCStd): used by PiStock Explorer to
                # download and open the part in FreeCAD.
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


@router.get("/api/v1/parts/{part_id}")
def get_part(part_id: int):
    """Detail of a part (used by the 3D viewer page)."""
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
            "info": p.info,
            "last_author": latest_plm.author if latest_plm else None,
            "last_timestamp": (
                latest_plm.timestamp.isoformat() if latest_plm else None
            ),
        }


@router.post("/api/v1/parts")
def create_part_manual(part_name: str = Form(...)):
    """Creates a part WITHOUT going through CAD (no files).
    Used by the dashboard's "+ Nouvelle pièce" button.
    The id is assigned automatically by SQLite."""
    part_name = part_name.strip()
    if not part_name:
        raise HTTPException(status_code=400,
                            detail="Le nom de la pièce est obligatoire.")

    with Session(engine) as session:
        # Check the name's uniqueness before insertion (otherwise we
        # would get an unhelpful IntegrityError to return to the frontend).
        existing = session.exec(
            select(Parts).where(Parts.part_name == part_name)
        ).first()
        if existing:
            raise HTTPException(
                status_code=409,  # 409 Conflict = resource already exists
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
#  PER-PART ACTIONS: project / status / lock
# ----------------------------------------------------------------------
# All these actions check the lock (except the lock toggle itself, of
# course). If the part is locked, we return 423.

VALID_STATUSES = {"Init", "Revue", "Asset"}


def _check_not_locked(part: Parts):
    if part.locked:
        raise HTTPException(
            status_code=423,  # 423 Locked
            detail=f"La pièce '{part.part_name}' est verrouillée. "
                   f"Déverrouillez-la avant de la modifier.",
        )


@router.post("/api/v1/parts/{part_id}/assign-project")
def assign_project(part_id: int,
                    project_id: int | None = Form(default=None)):
    """Associates a part with a project (or dissociates it if project_id
    is null/absent). Rejected if the part is locked."""
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


@router.post("/api/v1/parts/{part_id}/status")
def set_part_status(part_id: int, new_status: str = Form(...)):
    """Changes a part's status (Init / Revue / Asset).
    Rejected if the part is locked."""
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


@router.post("/api/v1/parts/{part_id}/info")
def set_part_info(part_id: int, info: str = Form(default="")):
    """Sets the free-form, searchable info field (hashtags / subject
    codes). Intentionally NOT lock-guarded: info is organizational
    metadata, useful to tag even validated/locked parts. An empty
    string clears the field (stored as NULL)."""
    with Session(engine) as session:
        part = session.get(Parts, part_id)
        if part is None:
            raise HTTPException(status_code=404, detail="Pièce introuvable.")
        part.info = (info or "").strip() or None
        session.add(part)
        session.commit()
        return {"status": "success", "info": part.info}


@router.post("/api/v1/parts/{part_id}/lock")
def toggle_part_lock(
    part_id: int,
    locked: bool = Form(...),
    x_admin_password: str | None = Header(default=None),
):
    """Toggles a part's lock. No self-protection: the lock can always
    be modified (otherwise it would be impossible to remove it once
    set)."""
    with Session(engine) as session:
        part = session.get(Parts, part_id)
        if part is None:
            raise HTTPException(status_code=404, detail="Pièce introuvable.")
        new_locked = bool(locked)
        # UNLOCKING requires admin auth; locking remains open
        # (quick lock-down when needed).
        if part.locked and not new_locked:
            _check_admin_password(x_admin_password)
        part.locked = new_locked
        session.add(part)
        session.commit()
        return {"status": "success", "locked": part.locked}


@router.get("/api/v1/last-used-project")
def get_last_used_project():
    """Returns the project of the most recently created PART that has
    an associated project. Used by the UI to pre-select a project when
    assigning one to a new part. None if no part has a project yet."""
    with Session(engine) as session:
        # 'id DESC' = reverse creation order (auto-incremented id)
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
#  PLM REVISION ENDPOINTS (list, deletion, set-main)
# ----------------------------------------------------------------------
@router.get("/api/v1/parts/{part_id}/revisions")
def list_part_revisions(part_id: int):
    """Lists all the PLM revisions of a part, from the most recent to
    the oldest. Marks the one that is "current" (is_main if it exists,
    otherwise the most recent)."""
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


@router.delete("/api/v1/plm/{plm_id}")
def delete_plm_revision(plm_id: int,
                         _admin: None = Depends(_require_admin)):
    """Deletes a PLM revision: the database row AND the associated
    files on disk (.FCStd, .glb, .png). Rejected if the part is
    locked."""
    with Session(engine) as session:
        plm = session.get(PLM, plm_id)
        if plm is None:
            raise HTTPException(status_code=404,
                                detail=f"Révision PLM id={plm_id} introuvable.")
        part = session.get(Parts, plm.id_parts)
        if part is not None:
            _check_not_locked(part)

        # Delete the files BEFORE destroying the row, so the paths
        # remain available.
        _delete_file_if_exists(plm.path_2_cadfile)
        _delete_file_if_exists(plm.path_2_thumbnail)
        _delete_file_if_exists(plm.path_2_3dglb)

        session.delete(plm)
        session.commit()
        logger.info(f"Révision PLM {plm_id} supprimée (piece {part.part_name if part else '?'}).")
        return {"status": "success", "deleted_id": plm_id}


@router.delete("/api/v1/parts/{part_id}")
def delete_part(part_id: int,
                _admin: None = Depends(_require_admin)):
    """PERMANENTLY deletes a part from the database:
    - Rejected (409) if the part is referenced in one or more BOMs
      (with the list of the BOMs concerned in the detail)
    - Otherwise: deletes all the associated PLM revisions (with their
      files: .FCStd, .glb, .png thumb), the Stock entry (with its photo
      and its doc), then the Part itself.
    Files missing on disk are logged but do not block the operation
    (idempotence)."""
    with Session(engine) as session:
        part = session.get(Parts, part_id)
        if part is None:
            raise HTTPException(
                status_code=404,
                detail=f"Pièce id={part_id} introuvable."
            )

        # Check: is the part used in a BOM? We look at the BomLine rows
        # that point to this part (as id_parts, not as id_subbom of
        # course).
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

        # Cascade deletion of the PLM revisions (+ physical files)
        plm_rows = session.exec(
            select(PLM).where(PLM.id_parts == part_id)
        ).all()
        for plm in plm_rows:
            _delete_file_if_exists(plm.path_2_cadfile)
            _delete_file_if_exists(plm.path_2_thumbnail)
            _delete_file_if_exists(plm.path_2_3dglb)
            session.delete(plm)

        # Deletion of the Stock row (+ photo + doc)
        stock = session.exec(
            select(Stock).where(Stock.id_parts == part_id)
        ).first()
        if stock is not None:
            _delete_file_if_exists(stock.path_2_img)
            _delete_file_if_exists(stock.path_2_doc)
            session.delete(stock)

        # Deletion of the Part itself
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


@router.post("/api/v1/plm/{plm_id}/set-main")
def set_plm_main(plm_id: int):
    """Marks this revision as "main" (is_main=True) and clears the flag
    on all the other revisions of the same part. Rejected if the part
    is locked."""
    with Session(engine) as session:
        plm = session.get(PLM, plm_id)
        if plm is None:
            raise HTTPException(status_code=404,
                                detail=f"Révision PLM id={plm_id} introuvable.")
        part = session.get(Parts, plm.id_parts)
        if part is not None:
            _check_not_locked(part)

        # Reset is_main on all the other revisions of this part, then
        # flag this one. All in the same transaction.
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


@router.post("/api/v1/parts/upload")
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

        # --- PRE-CHECK: lock ------------------------------------------
        # Check the lock BEFORE saving the files: avoid writing orphan
        # files if the part is locked. Covers both cases: a direct
        # part_id OR a part_name that matches an existing part
        # (reuse fallback).
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
                # If target_part is None, it's a new part -> OK
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

            # Compute the next PLM version for this part. Must be done
            # AFTER the flush (so that part.id exists) but BEFORE
            # creating the PLM row.
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
