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
Authentification admin (mot de passe singleton) + endpoints /admin/*.

Le compte admin est unique. Mot de passe stocke en PBKDF2-HMAC-SHA256
(200_000 iter, sel aleatoire 16 octets). Uniquement stdlib, aucune
dependance ajoutee.

  - Cree au premier demarrage via POST /api/v1/admin/setup
    (refuse si un admin existe deja).
  - Renouvelable via POST /api/v1/admin/change-password (necessite
    l'ancien mot de passe).

Les endpoints destructifs (DELETE *) et le DEVERROUILLAGE d'une piece
exigent le header HTTP `X-Admin-Password`. Suffisant en LAN avec HTTPS
(cert auto-signe) ; pour de l'expose internet, prevoir un vrai jeton de
session. Les helpers `_check_admin_password` (en code) et `_require_admin`
(dependance FastAPI) sont reutilises par les autres services.
"""
import hashlib
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Header
from sqlmodel import Session, select

from config import engine, logger
from model import Admin

router = APIRouter()

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


# ----------------------------------------------------------------------
#  ENDPOINTS API : ADMIN (singleton, mot de passe)
# ----------------------------------------------------------------------
@router.get("/api/v1/admin/status")
def admin_status():
    """Indique si un compte admin existe. Utilise par l'UI pour
    declencher le dialogue de setup au premier lancement."""
    with Session(engine) as session:
        return {"configured": _get_admin(session) is not None}


@router.post("/api/v1/admin/setup")
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


@router.post("/api/v1/admin/verify")
def admin_verify(password: str = Form(...)):
    """Verifie un mot de passe admin. Utilise par l'UI pour le login."""
    _check_admin_password(password)   # leve 401/403/503 si invalide
    return {"status": "success"}


@router.post("/api/v1/admin/change-password")
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
