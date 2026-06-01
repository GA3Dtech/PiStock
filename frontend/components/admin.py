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

"""Authentification admin cote UI : etat de session (stocke dans
app.storage.user), dialogues (setup, login, changement de mot de
passe) et garde universelle _ensure_admin. Le mot de passe vit en
base (table admin) ; on accede a main de facon paresseuse.
"""
import time
from nicegui import ui, app
from sqlmodel import Session, select


# ======================================================================
#  ADMIN — session (login dialogues, garde _ensure_admin)
# ======================================================================
# Le mot de passe est en base (table 'admin' cf. main.py). Cote UI on
# garde un epoch dans app.storage.user["admin_until"]. Une fois expire,
# le dialogue de login s'ouvre lors de la prochaine action protegee.
ADMIN_SESSION_SECONDS = 30 * 60  # 30 minutes


def _admin_configured() -> bool:
    import main
    with Session(main.engine) as session:
        return session.exec(select(main.Admin)).first() is not None


def _session_admin_active() -> bool:
    try:
        until = float(app.storage.user.get("admin_until", 0) or 0)
    except (TypeError, ValueError):
        until = 0
    return time.time() < until


def _mark_session_admin():
    app.storage.user["admin_until"] = time.time() + ADMIN_SESSION_SECONDS


def _clear_session_admin():
    app.storage.user.pop("admin_until", None)


def _verify_admin_password(password: str) -> bool:
    import main
    if not password:
        return False
    with Session(main.engine) as session:
        rec = session.exec(select(main.Admin)).first()
        if rec is None:
            return False
        return main._verify_password(password, rec.salt, rec.password_hash)


def _open_admin_setup_dialog(on_done=None):
    """Premier demarrage : creer le mot de passe admin."""
    with ui.dialog().props("persistent") as dialog, \
            ui.card().classes("min-w-[420px]"):
        ui.label("Configuration initiale — mot de passe admin") \
            .classes("text-lg font-bold")
        ui.label("Aucun compte admin n\'est configuré. Choisissez un "
                  "mot de passe : il sera demandé pour toute "
                  "suppression et tout déverrouillage.") \
            .classes("text-sm text-gray-700")
        p1 = ui.input("Mot de passe (min. 6 caractères)",
                       password=True, password_toggle_button=True) \
            .classes("w-full")
        p2 = ui.input("Confirmer le mot de passe",
                       password=True, password_toggle_button=True) \
            .classes("w-full")
        err = ui.label("").classes("text-sm text-red-600")

        def submit():
            v1, v2 = p1.value or "", p2.value or ""
            if len(v1) < 6:
                err.text = "Le mot de passe doit faire au moins 6 caractères."
                return
            if v1 != v2:
                err.text = "Les deux saisies ne correspondent pas."
                return
            import main
            with Session(main.engine) as session:
                if session.exec(select(main.Admin)).first() is not None:
                    err.text = ("Un compte admin existe déjà — "
                                  "rechargez la page.")
                    return
                salt = main._new_salt()
                session.add(main.Admin(
                    salt=salt.hex(),
                    password_hash=main._hash_password(v1, salt),
                ))
                session.commit()
            _mark_session_admin()
            ui.notify("Compte admin créé.", type="positive")
            dialog.close()
            if on_done:
                on_done()

        with ui.row().classes("w-full justify-end gap-2 mt-1"):
            ui.button("Créer", on_click=submit).props("color=primary")
    dialog.open()


def _open_admin_login_dialog(on_success=None, on_cancel=None):
    with ui.dialog() as dialog, ui.card().classes("min-w-[380px]"):
        ui.label("Authentification admin").classes("text-lg font-bold")
        ui.label("Saisissez le mot de passe admin pour continuer.") \
            .classes("text-sm text-gray-700")
        pwd = ui.input("Mot de passe", password=True,
                         password_toggle_button=True).classes("w-full")
        err = ui.label("").classes("text-sm text-red-600")

        def submit():
            if _verify_admin_password(pwd.value or ""):
                _mark_session_admin()
                dialog.close()
                if on_success:
                    on_success()
            else:
                err.text = "Mot de passe invalide."
                pwd.value = ""

        pwd.on("keydown.enter", lambda _e: submit())
        with ui.row().classes("w-full justify-end gap-2 mt-1"):
            def cancel():
                dialog.close()
                if on_cancel:
                    on_cancel()
            ui.button("Annuler", on_click=cancel).props("flat")
            ui.button("Valider", on_click=submit).props("color=primary")
    dialog.open()


def _open_admin_change_password_dialog():
    with ui.dialog() as dialog, ui.card().classes("min-w-[420px]"):
        ui.label("Changer le mot de passe admin") \
            .classes("text-lg font-bold")
        cur = ui.input("Mot de passe actuel", password=True,
                         password_toggle_button=True).classes("w-full")
        n1 = ui.input("Nouveau mot de passe (min. 6 car.)",
                       password=True, password_toggle_button=True) \
            .classes("w-full")
        n2 = ui.input("Confirmer le nouveau", password=True,
                       password_toggle_button=True).classes("w-full")
        err = ui.label("").classes("text-sm text-red-600")

        def submit():
            c, a, b = cur.value or "", n1.value or "", n2.value or ""
            if not _verify_admin_password(c):
                err.text = "Mot de passe actuel invalide."
                return
            if len(a) < 6:
                err.text = "Le nouveau doit faire au moins 6 caractères."
                return
            if a != b:
                err.text = "Les deux saisies ne correspondent pas."
                return
            import main
            with Session(main.engine) as session:
                rec = session.exec(select(main.Admin)).first()
                if rec is None:
                    err.text = "Compte admin introuvable."
                    return
                new_salt = main._new_salt()
                rec.salt = new_salt.hex()
                rec.password_hash = main._hash_password(a, new_salt)
                from datetime import datetime as _dt, timezone as _tz
                rec.updated_at = _dt.now(_tz.utc).isoformat()
                session.add(rec); session.commit()
            _mark_session_admin()
            ui.notify("Mot de passe admin renouvelé.", type="positive")
            dialog.close()

        with ui.row().classes("w-full justify-end gap-2 mt-1"):
            ui.button("Annuler", on_click=dialog.close).props("flat")
            ui.button("Valider", on_click=submit).props("color=primary")
    dialog.open()


def _ensure_admin(on_success, on_cancel=None):
    """Garde universelle : exige une session admin.
    - Admin actif -> appelle on_success() immediatement.
    - Pas d'admin configure -> ouvre le setup ; au succes -> on_success.
    - Admin configure mais session expiree -> ouvre le login."""
    if _session_admin_active():
        on_success()
        return
    if not _admin_configured():
        _open_admin_setup_dialog(on_done=on_success)
        return
    _open_admin_login_dialog(on_success=on_success, on_cancel=on_cancel)
