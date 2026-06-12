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

"""Cross-cutting UI helpers: applying the session language, injecting
the PWA tags, and the link to the source code (AGPLv3).
"""
from nicegui import ui, app
from i18n import set_lang



# ----------------------------------------------------------------------
#  AGPLv3 COMPLIANCE: link to the source code
# ----------------------------------------------------------------------
# The AGPLv3 requires that users accessing the application over the
# network can obtain the source code. We expose a visible link in the
# header of every page to discharge this obligation.
SOURCE_CODE_URL = "https://github.com/pistock-org/PiStock"


def _apply_user_lang():
    """Reads the language chosen by the user (stored in the server-side
    storage, tied to a session cookie) and applies it globally for the
    current request. To be called at the VERY START of each
    @ui.page."""
    try:
        # We use app.storage.user and NOT app.storage.browser:
        # browser is a signed cookie whose value is set in the HTTP
        # headers, so it is read-only outside the initial construction
        # of the response. user is server-side, modifiable from
        # anywhere (including event handlers).
        lang = app.storage.user.get("lang", "en")
    except Exception:
        lang = "en"
    set_lang(lang)


def _register_pwa():
    """Injects the PWA tags into the <head>: manifest, theme-color,
    icon and service worker registration. To be called from each
    @ui.page so that the app is installable.

    The service worker is only active over HTTPS or on localhost
    (standard browser limitation). On a Pi accessed via
    http://192.168.x.y from a mobile, the SW will not register, but the
    manifest and the meta tags remain useful."""
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
