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
Generation d'identifiants lisibles et incrementaux :
  - code projet (AAA..ZZZ, base 26 sur 3 lettres) ;
  - code BOM (B0001..B9999) ;
  - version PLM par piece (aa..zz, base 26 sur 2 lettres).

Plus le helper `_get_current_plm` qui centralise la regle "quelle
revision afficher" (is_main sinon la plus recente).
"""
from fastapi import HTTPException
from sqlmodel import Session, select

from model import Project, Bom, PLM


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
