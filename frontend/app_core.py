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

"""Helpers transverses de l'UI : application de la langue de session,
injection des balises PWA, et lien vers le code source (AGPLv3).
"""
from nicegui import ui, app
from i18n import set_lang



# ----------------------------------------------------------------------
#  CONFORMITE AGPLv3 : lien vers le code source
# ----------------------------------------------------------------------
# L'AGPLv3 exige que les utilisateurs accedant a l'application via le
# reseau puissent obtenir le code source. On expose un lien visible
# dans le header de chaque page pour s'acquitter de cette obligation.
SOURCE_CODE_URL = "https://github.com/GA3Dtech/PiStock"


def _apply_user_lang():
    """Lit la langue choisie par l'utilisateur (stockee dans le
    storage cote serveur, lie a un cookie de session) et l'applique
    globalement pour la requete en cours. A appeler en TOUT DEBUT de
    chaque @ui.page."""
    try:
        # On utilise app.storage.user et NON app.storage.browser :
        # browser est un cookie signe dont la valeur est posee dans
        # les headers HTTP, donc en lecture seule en dehors de la
        # construction initiale de la reponse. user est cote serveur,
        # modifiable de partout (event handlers compris).
        lang = app.storage.user.get("lang", "en")
    except Exception:
        lang = "en"
    set_lang(lang)


def _register_pwa():
    """Injecte les balises PWA dans le <head> : manifest, theme-color,
    icone et enregistrement du service worker. A appeler depuis chaque
    @ui.page pour que l'app soit installable.

    Le service worker n'est actif qu'en HTTPS ou sur localhost (limite
    standard des navigateurs). Sur un Pi accédé via http://192.168.x.y
    depuis un mobile, le SW ne s'enregistrera pas, mais le manifest
    et les meta tags resteront utiles."""
    ui.add_head_html('''
        <link rel="manifest" href="/static/manifest.json">
        <meta name="theme-color" content="#292524">
        <link rel="icon" href="/static/icon-192.png" type="image/png">
        <link rel="apple-touch-icon" href="/static/icon-192.png">
        <script>
        if ('serviceWorker' in navigator) {
            window.addEventListener('load', () => {
                navigator.serviceWorker.register('/static/service-worker.js')
                    .then(reg => console.log('PiStock SW registered:', reg.scope))
                    .catch(err => console.warn('PiStock SW failed:', err));
            });
        }
        </script>
    ''')
