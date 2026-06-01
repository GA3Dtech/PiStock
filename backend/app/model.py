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
Schema de la base de donnees PiStock — SOURCE UNIQUE DE VERITE.

Ce module definit les tables SQLModel utilisees a la fois par :
  - le serveur (backend/app/main.py) au runtime ;
  - le script d'initialisation (backend/app/install/init_db.py) qui
    appelle SQLModel.metadata.create_all().

Auparavant ces classes etaient dupliquees dans main.py ET dans
init_db.py, ce qui imposait de modifier le schema a deux endroits (et
avait deja divergé : la table 'admin' manquait dans init_db.py). En
centralisant ici, le schema reste coherent par construction.

IMPORTANT : ne definir les tables qu'UNE SEULE FOIS. SQLModel enregistre
chaque classe `table=True` dans une metadata partagee ; une double
definition leverait "Table '...' is already defined". On importe donc
ces classes, on ne les redefinit jamais.
"""
from datetime import datetime, timezone

from sqlmodel import SQLModel, Field


class Parts(SQLModel, table=True):
    __tablename__ = "parts"
    id: int | None = Field(default=None, primary_key=True)
    part_name: str = Field(index=True, unique=True)
    # Lien optionnel vers un projet. Nullable car une piece peut
    # exister sans projet (legacy ou pieces standalone).
    id_project: int | None = Field(default=None, foreign_key="project.id")
    # Statut de maturite de la piece : 'Init' (en cours), 'Revue'
    # (en relecture), 'Asset' (validee, prete pour usage prod).
    status: str = Field(default="Init")
    # Verrou : quand True, l'UI empeche les modifications (projet,
    # statut). N'empeche PAS les uploads de nouvelles revisions via la
    # macro FreeCAD (sinon trop restrictif pour un PLM).
    locked: bool = Field(default=False)


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
    # Numero de version : deux lettres minuscules, aa->zz (676 max).
    # Incremente automatiquement a chaque nouvelle revision PLM POUR
    # UNE PIECE DONNEE. Premier push d'une piece = 'aa'.
    version: str = Field(default="aa", max_length=2)
    # Flag "revision principale" : si une revision est marquee
    # is_main=True, c'est elle qui s'affiche partout (au lieu de la
    # plus recente par timestamp, qui reste le fallback).
    is_main: bool = Field(default=False)


class Stock(SQLModel, table=True):
    __tablename__ = "stock"
    id: int | None = Field(default=None, primary_key=True)
    # Lien direct vers la cle primaire de la table parts.
    id_parts: int = Field(foreign_key="parts.id", nullable=False)
    path_2_img: str | None = Field(default=None)
    quantity: int = Field(default=0)
    location: str | None = Field(default=None)
    supply: str | None = Field(default=None)
    # Chemin vers la fiche composant (PDF, datasheet...) stockee dans
    # data-pistock/uploads/doc/.
    path_2_doc: str | None = Field(default=None)


class Project(SQLModel, table=True):
    __tablename__ = "project"
    id: int | None = Field(default=None, primary_key=True)
    # Code alphabetique a 3 lettres majuscules, incremental : AAA, AAB,
    # ..., AAZ, ABA, ..., ZZZ. Unique car il sert d'identifiant lisible
    # (visible dans l'UI).
    code: str = Field(index=True, unique=True, max_length=3)
    # Description libre, multi-lignes. Optionnelle.
    description: str | None = Field(default=None)


class Bom(SQLModel, table=True):
    __tablename__ = "bom"
    id: int | None = Field(default=None, primary_key=True)
    # Code BOM : B + 4 chiffres zero-padded (B0001, B0002, ...).
    # Incremental, unique, lisible.
    code: str = Field(index=True, unique=True, max_length=5)
    # Description libre.
    description: str | None = Field(default=None)
    # Lien optionnel vers un projet : une BOM peut etre rattachee a un
    # projet (la BOM d'un produit) ou exister independamment.
    id_project: int | None = Field(default=None, foreign_key="project.id")


class BomLine(SQLModel, table=True):
    __tablename__ = "bom_line"
    id: int | None = Field(default=None, primary_key=True)
    id_bom: int = Field(foreign_key="bom.id", nullable=False)
    # Exactement UN des deux champs suivants doit etre renseigne :
    # - id_parts : ligne pour une piece (cas standard)
    # - id_subbom : ligne pour une sous-BOM (assemblage hierarchique)
    # La contrainte est appliquee cote applicatif.
    id_parts: int | None = Field(default=None, foreign_key="parts.id")
    id_subbom: int | None = Field(default=None, foreign_key="bom.id")
    # Quantite necessaire de cette piece ou sous-BOM pour assembler une
    # unite de la BOM parente.
    quantity: int = Field(default=1)


# Compte admin (singleton : on n'utilise jamais qu'une seule ligne,
# id=1). Sert aux operations destructives (suppressions, deverrouillage).
# Voir endpoints /api/v1/admin/* et helpers _check_admin_password /
# _require_admin dans main.py.
class Admin(SQLModel, table=True):
    __tablename__ = "admin"
    id: int | None = Field(default=None, primary_key=True)
    salt: str            # 16 octets aleatoires, en hex
    password_hash: str   # PBKDF2-HMAC-SHA256, 200_000 iter, en hex
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
