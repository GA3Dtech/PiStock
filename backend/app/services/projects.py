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

"""Endpoints PROJETS : liste, creation (code auto-genere), suppression."""
from fastapi import APIRouter, Form, HTTPException, Depends
from sqlmodel import Session, select

from config import engine, logger
from model import Parts, Project, Bom
from services.codes import _next_project_code
from services.admin import _require_admin

router = APIRouter()


@router.get("/api/v1/projects")
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


@router.post("/api/v1/projects")
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


@router.delete("/api/v1/projects/{project_id}")
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
