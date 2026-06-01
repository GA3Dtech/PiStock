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

"""En-tete commun a toutes les pages : titre, bouton accueil/refresh,
indicateur admin, selecteur de langue et lien source AGPLv3.
"""
from nicegui import ui, app
from i18n import _, get_lang, AVAILABLE_LANGS
from app_core import SOURCE_CODE_URL
from components.admin import (_admin_configured, _session_admin_active, _clear_session_admin, _open_admin_login_dialog, _open_admin_change_password_dialog)


def render_app_header(title_key: str, show_home: bool = False):
    """En-tete commun aux pages : titre a gauche, sélecteur de langue
    et lien vers le code source a droite (obligation AGPLv3).

    'title_key' est un msgid qui sera traduit via _().
    'show_home' affiche un bouton 🏠 vers le catalogue (par defaut
    False : la page catalogue elle-meme ne doit pas l'afficher)."""
    with ui.header().classes("bg-stone-800 text-white shadow"):
        with ui.row().classes("w-full items-center no-wrap gap-3"):
            ui.label(_(title_key)).classes("text-xl font-medium")
            ui.element("div").classes("flex-grow")  # spacer

            # --- Bouton retour catalogue (pages secondaires uniquement)
            if show_home:
                ui.button(icon="home",
                           on_click=lambda: ui.navigate.to("/")) \
                    .props("flat round dense color=white") \
                    .tooltip("Retour au catalogue")

            # --- Bouton actualiser (toutes les pages) -----------------
            # Recharge la page courante. Plus simple pour l'utilisateur
            # final qu'un F5 et ne perd pas la navigation (URL inchangee).
            ui.button(icon="refresh",
                       on_click=lambda: ui.navigate.reload()) \
                .props("flat round dense color=white") \
                .tooltip("Actualiser la page")

            # --- Indicateur admin ---------------------------------
            # 3 etats visuels :
            #   - admin actif         -> icone verte + menu (change mdp, logout)
            #   - admin configure inactif -> icone grise, ouvre le login
            #   - aucun admin configure  -> rien (le setup s'ouvre tout seul)
            if _admin_configured():
                if _session_admin_active():
                    with ui.button(icon="admin_panel_settings") \
                            .props("flat round dense color=green-3") \
                            .tooltip("Session admin active"):
                        with ui.menu():
                            ui.menu_item(
                                "Changer le mot de passe",
                                on_click=_open_admin_change_password_dialog)
                            ui.menu_item(
                                "Déconnecter l\'admin",
                                on_click=lambda: (
                                    _clear_session_admin(),
                                    ui.notify("Session admin terminée.",
                                               type="info"),
                                    ui.navigate.reload(),
                                ))
                else:
                    ui.button(
                        icon="admin_panel_settings",
                        on_click=lambda: _open_admin_login_dialog(
                            on_success=lambda: ui.navigate.reload()),
                    ).props("flat round dense color=grey-5") \
                     .tooltip("Se connecter comme admin")

            # --- Selecteur de langue --------------------------------
            # Toggle EN/FR. Au changement : on stocke la preference
            # cote navigateur et on recharge la page pour appliquer.
            current = get_lang()
            lang_options = {code: code.upper()
                             for code, _label in AVAILABLE_LANGS}

            def on_lang_change(e):
                new_lang = e.value
                # app.storage.user (cote serveur) au lieu de
                # app.storage.browser (cookie signe, read-only hors
                # construction de reponse HTTP).
                app.storage.user["lang"] = new_lang
                # Reload pour reconstruire toute la page dans la
                # nouvelle langue. Plus simple et fiable qu'un rebuild
                # incremental qui demanderait de tracker tous les
                # widgets contenant du texte.
                ui.navigate.reload()

            ui.toggle(lang_options, value=current,
                       on_change=on_lang_change) \
                .props("color=white dense").classes("text-sm")

            ui.link(_("Source code (AGPLv3)"),
                    SOURCE_CODE_URL,
                    new_tab=True) \
                .classes("text-stone-300 hover:text-white "
                          "text-sm no-underline")
