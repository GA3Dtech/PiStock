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
PiStock database schema — SINGLE SOURCE OF TRUTH.

This module defines the SQLModel tables used both by:
  - the server (backend/app/main.py) at runtime;
  - the initialization script (backend/app/install/init_db.py) which
    calls SQLModel.metadata.create_all().

Previously these classes were duplicated in main.py AND in init_db.py,
which forced changing the schema in two places (and had already
diverged: the 'admin' table was missing from init_db.py). By
centralizing here, the schema stays consistent by construction.

IMPORTANT: define the tables ONLY ONCE. SQLModel registers each
`table=True` class in a shared metadata; a double definition would raise
"Table '...' is already defined". So we import these classes, we never
redefine them.
"""
from datetime import datetime, timezone

from sqlmodel import SQLModel, Field


class Parts(SQLModel, table=True):
    __tablename__ = "parts"
    id: int | None = Field(default=None, primary_key=True)
    part_name: str = Field(index=True, unique=True)
    # Optional link to a project. Nullable because a part can exist
    # without a project (legacy or standalone parts).
    id_project: int | None = Field(default=None, foreign_key="project.id")
    # Maturity status of the part: 'Init' (in progress), 'Revue'
    # (under review), 'Asset' (validated, ready for production use).
    status: str = Field(default="Init")
    # Lock: when True, the UI prevents modifications (project, status).
    # Does NOT prevent uploads of new revisions via the FreeCAD macro
    # (otherwise too restrictive for a PLM).
    locked: bool = Field(default=False)
    # Free-form, searchable info field. Meant for hashtags or subject
    # codes (manufacturing method, type of use, ...) so parts can be
    # grouped/searched across projects. Optional, editable even when
    # the part is locked (organizational metadata, not structural).
    info: str | None = Field(default=None)


class PLM(SQLModel, table=True):
    __tablename__ = "plm"
    id: int | None = Field(default=None, primary_key=True)
    id_parts: int = Field(foreign_key="parts.id", nullable=False)
    path_2_cadfile: str | None = Field(default=None)
    path_2_thumbnail: str | None = Field(default=None)
    path_2_3dglb: str | None = Field(default=None)
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    author: str | None = Field(default=None)
    # Version number: two lowercase letters, aa->zz (676 max).
    # Incremented automatically on each new PLM revision FOR A GIVEN
    # PART. First push of a part = 'aa'.
    version: str = Field(default="aa", max_length=2)
    # "Main revision" flag: if a revision is marked is_main=True, it is
    # the one displayed everywhere (instead of the most recent by
    # timestamp, which remains the fallback).
    is_main: bool = Field(default=False)


class Stock(SQLModel, table=True):
    __tablename__ = "stock"
    id: int | None = Field(default=None, primary_key=True)
    # Direct link to the primary key of the parts table.
    id_parts: int = Field(foreign_key="parts.id", nullable=False)
    path_2_img: str | None = Field(default=None)
    quantity: int = Field(default=0)
    location: str | None = Field(default=None)
    supply: str | None = Field(default=None)
    # Path to the component datasheet (PDF, datasheet...) stored in
    # data-pistock/uploads/doc/.
    path_2_doc: str | None = Field(default=None)


class Project(SQLModel, table=True):
    __tablename__ = "project"
    id: int | None = Field(default=None, primary_key=True)
    # Alphabetical code of 3 uppercase letters, incremental: AAA, AAB,
    # ..., AAZ, ABA, ..., ZZZ. Unique because it serves as a readable
    # identifier (visible in the UI).
    code: str = Field(index=True, unique=True, max_length=3)
    # Free-form, multi-line description. Optional.
    description: str | None = Field(default=None)


class Bom(SQLModel, table=True):
    __tablename__ = "bom"
    id: int | None = Field(default=None, primary_key=True)
    # BOM code: B + 4 zero-padded digits (B0001, B0002, ...).
    # Incremental, unique, readable.
    code: str = Field(index=True, unique=True, max_length=5)
    # Free-form description.
    description: str | None = Field(default=None)
    # Optional link to a project: a BOM can be attached to a project
    # (the BOM of a product) or exist independently.
    id_project: int | None = Field(default=None, foreign_key="project.id")


class BomLine(SQLModel, table=True):
    __tablename__ = "bom_line"
    id: int | None = Field(default=None, primary_key=True)
    id_bom: int = Field(foreign_key="bom.id", nullable=False)
    # Exactly ONE of the two following fields must be set:
    # - id_parts: line for a part (standard case)
    # - id_subbom: line for a sub-BOM (hierarchical assembly)
    # The constraint is enforced on the application side.
    id_parts: int | None = Field(default=None, foreign_key="parts.id")
    id_subbom: int | None = Field(default=None, foreign_key="bom.id")
    # Quantity of this part or sub-BOM required to assemble one unit of
    # the parent BOM.
    quantity: int = Field(default=1)


# Admin account (singleton: we only ever use a single row, id=1).
# Used for destructive operations (deletions, unlocking). See the
# /api/v1/admin/* endpoints and the _check_admin_password /
# _require_admin helpers in main.py.
class Admin(SQLModel, table=True):
    __tablename__ = "admin"
    id: int | None = Field(default=None, primary_key=True)
    salt: str            # 16 random bytes, in hex
    password_hash: str   # PBKDF2-HMAC-SHA256, 200_000 iter, in hex
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
