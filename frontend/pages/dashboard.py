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

"""Catalog page (/): list of parts as cards, filters, and all the
associated dialogs (part options, deletion, project assignment,
stock, BOM management).
"""
import os
import json
import shutil
from datetime import datetime, timezone
import time
from nicegui import ui, app, events
from sqlmodel import Session, select
from i18n import _, set_lang, get_lang, AVAILABLE_LANGS
from app_core import (_apply_user_lang, _register_pwa)
from components.header import render_app_header
from components.admin import (_admin_configured, _open_admin_setup_dialog, _ensure_admin)
from db import (UNASSIGNED, fetch_parts_full, fetch_last_used_project_id, assign_project_to_part, set_part_status_db, set_part_info_db, toggle_part_lock_db, fetch_stock, save_stock, create_part_in_db, fetch_projects, create_project_in_db, fetch_boms, fetch_bom_detail, create_bom_db, delete_bom_db, delete_part_db, add_bom_line_db, update_bom_line_db, delete_bom_line_db, bom_stock_apply, delete_project_db)


# ======================================================================
#  PAGE : DASHBOARD
# ======================================================================
@ui.page("/catalog")
def dashboard_page(project: str | None = None):
    """Catalog page: list of parts as cards.

    'project' is an optional query parameter (e.g. /catalog?project=AAB)
    used to open the catalog already filtered on a given project — this
    is how the projects overview ("/") links into the catalog."""
    # Apply the language chosen by the user BEFORE building anything
    # (the first calls to _() depend on it).
    _apply_user_lang()
    _register_pwa()
    # First startup: no admin password yet -> setup
    if not _admin_configured():
        _open_admin_setup_dialog()
    # Browser tab title (visible in the tab bar + history)
    ui.page_title(_("PiStock — Catalog"))

    # JavaScript injected into the page <head>. Since NiceGUI 3.x
    # sanitizes the content of ui.html() and STRIPS the 'on*'
    # attributes (onchange, onclick...), we cannot use inline
    # onchange="...". Instead: event delegation. A single listener
    # attached to the document detects all change events on inputs
    # carrying data-stock-upload="{part_id}" and performs the upload.
    ui.add_head_html('''
        <script>
        // Garde-fou : n'installe les listeners qu'une seule fois
        if (!window._stockUploadInstalled) {
            window._stockUploadInstalled = true;

            // ---- Listener pour les PHOTOS de stock ----
            // Cible : input[data-stock-upload="{part_id}"]
            // Endpoint : POST /api/v1/parts/{id}/stock-photo
            document.addEventListener('change', async function(e) {
                if (!e.target || !e.target.matches('input[data-stock-upload]')) {
                    return;
                }
                const partId = e.target.dataset.stockUpload;
                const file = e.target.files[0];
                if (!file) return;
                const formData = new FormData();
                formData.append("photo", file);
                try {
                    const response = await fetch(
                        `/api/v1/parts/${partId}/stock-photo`,
                        { method: "POST", body: formData }
                    );
                    if (!response.ok) {
                        const err = await response.json().catch(() => ({}));
                        alert("Erreur upload : " + (err.detail || response.status));
                        return;
                    }
                    window.location.reload();
                } catch (err) {
                    alert("Erreur : " + err.message);
                }
            });

            // ---- Listener pour les FICHES COMPOSANT (doc) ----
            // Cible : input[data-stock-doc="{part_id}"]
            // Endpoint : POST /api/v1/parts/{id}/stock-doc
            document.addEventListener('change', async function(e) {
                if (!e.target || !e.target.matches('input[data-stock-doc]')) {
                    return;
                }
                const partId = e.target.dataset.stockDoc;
                const file = e.target.files[0];
                if (!file) return;
                const formData = new FormData();
                formData.append("doc", file);
                try {
                    const response = await fetch(
                        `/api/v1/parts/${partId}/stock-doc`,
                        { method: "POST", body: formData }
                    );
                    if (!response.ok) {
                        const err = await response.json().catch(() => ({}));
                        alert("Erreur upload fiche : " + (err.detail || response.status));
                        return;
                    }
                    window.location.reload();
                } catch (err) {
                    alert("Erreur : " + err.message);
                }
            });

            // ---- Listener pour les BOUTONS CAPTURE CAMERA ----
            // Cible : a[data-pistock-capture="{part_id}"]
            // Au clic : appelle pistockCapturePhoto(part_id) qui ouvre
            // un dialogue avec le live preview de la camera.
            document.addEventListener('click', function(e) {
                const trigger = e.target.closest('[data-pistock-capture]');
                if (!trigger) return;
                e.preventDefault();
                const partId = trigger.dataset.pistockCapture;
                pistockCapturePhoto(parseInt(partId, 10));
            });

            // ---- Listener pour ZOOM IMAGE (lightbox) ----
            // Cible : tout <img data-pistock-zoom> (photo de stock...).
            // Au clic : ouvre une visionneuse plein ecran zoomable.
            document.addEventListener('click', function(e) {
                const img = e.target.closest('img[data-pistock-zoom]');
                if (!img) return;
                e.preventDefault();
                window.pistockOpenImage(img.getAttribute('src'));
            });
        }

        // ===================================================
        //  VISIONNEUSE D'IMAGE ZOOMABLE (lightbox)
        // ===================================================
        // Overlay plein ecran : zoom molette + boutons +/-/ajuster,
        // glisser pour se deplacer quand c'est zoome, Echap/clic fond
        // pour fermer. Pur DOM, aucune dependance.
        window.pistockOpenImage = function(src) {
            if (!src) return;
            let k = 1, tx = 0, ty = 0;          // echelle + translation
            let dragging = false, sx = 0, sy = 0;

            const overlay = document.createElement('div');
            overlay.style.cssText =
                'position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,.92);' +
                'display:flex;flex-direction:column;';

            const bar = document.createElement('div');
            bar.style.cssText =
                'display:flex;align-items:center;gap:4px;padding:8px;' +
                'background:#292524;color:#fff;flex:0 0 auto;';
            const mkBtn = (label, title) => {
                const b = document.createElement('button');
                b.textContent = label; b.title = title || '';
                b.style.cssText =
                    'font-size:20px;line-height:1;width:40px;height:40px;' +
                    'border:none;border-radius:8px;background:transparent;' +
                    'color:#fff;cursor:pointer;';
                b.onmouseenter = () => b.style.background = 'rgba(255,255,255,.15)';
                b.onmouseleave = () => b.style.background = 'transparent';
                return b;
            };
            const bOut = mkBtn('−', 'Zoom -');
            const pct = document.createElement('span');
            pct.style.cssText = 'width:56px;text-align:center;font-size:14px;';
            const bIn  = mkBtn('+', 'Zoom +');
            const bFit = mkBtn('⤢', 'Ajuster');
            const spacer = document.createElement('div'); spacer.style.flex = '1';
            const bClose = mkBtn('✕', 'Fermer');
            bar.append(bOut, pct, bIn, bFit, spacer, bClose);

            const stage = document.createElement('div');
            stage.style.cssText =
                'flex:1 1 auto;overflow:hidden;display:flex;align-items:center;' +
                'justify-content:center;cursor:grab;';

            const img = document.createElement('img');
            img.src = src;
            img.style.cssText =
                'max-width:95vw;max-height:100%;user-select:none;' +
                '-webkit-user-drag:none;transition:transform .05s linear;';

            stage.appendChild(img);
            overlay.append(bar, stage);
            document.body.appendChild(overlay);

            function apply() {
                k = Math.max(0.25, Math.min(k, 12));
                if (k <= 1) { tx = 0; ty = 0; }   // recentre une fois ajuste
                img.style.transform =
                    'translate(' + tx + 'px,' + ty + 'px) scale(' + k + ')';
                pct.textContent = Math.round(k * 100) + '%';
                stage.style.cursor = k > 1 ? 'grab' : 'default';
            }
            const zoomAt = (factor) => { k *= factor; apply(); };

            bOut.onclick   = () => { k -= 0.25; apply(); };
            bIn.onclick    = () => { k += 0.25; apply(); };
            bFit.onclick   = () => { k = 1; tx = 0; ty = 0; apply(); };
            function close() {
                document.removeEventListener('keydown', onKey);
                overlay.remove();
            }
            bClose.onclick = close;
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay || e.target === stage) close();
            });
            function onKey(e) { if (e.key === 'Escape') close(); }
            document.addEventListener('keydown', onKey);

            stage.addEventListener('wheel', function(e) {
                e.preventDefault();
                zoomAt(e.deltaY > 0 ? 0.9 : 1.1);
            }, { passive: false });

            stage.addEventListener('mousedown', function(e) {
                if (k <= 1) return;
                dragging = true; sx = e.clientX - tx; sy = e.clientY - ty;
                stage.style.cursor = 'grabbing'; e.preventDefault();
            });
            window.addEventListener('mousemove', function(e) {
                if (!dragging) return;
                tx = e.clientX - sx; ty = e.clientY - sy; apply();
            });
            window.addEventListener('mouseup', function() {
                if (!dragging) return;
                dragging = false; stage.style.cursor = 'grab';
            });

            apply();
        };

        // ===================================================
        //  FONCTION DE CAPTURE PHOTO VIA getUserMedia
        // ===================================================
        // Ouvre un dialogue plein ecran avec un live preview de la
        // camera. L'utilisateur clique "Capturer" -> aperçu de la
        // photo + boutons "Enregistrer" / "Reprendre". L'envoi se
        // fait vers POST /api/v1/parts/{id}/stock-photo (le meme
        // endpoint que pour l'upload fichier), puis reload de la page.
        window.pistockCapturePhoto = async function(partId) {
            // Verification : navigator.mediaDevices n'est dispo que
            // sur les contextes HTTPS (sauf localhost). Sur du HTTP
            // depuis une autre machine, on previent l'utilisateur.
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                alert(
                    "La caméra n'est accessible qu'en HTTPS ou en localhost.\\n\\n" +
                    "Pour un accès depuis une autre machine, configurez " +
                    "HTTPS (certificat auto-signé ou reverse-proxy)."
                );
                return;
            }

            // --- Construction du dialogue en JS pur --------------
            // (pas de NiceGUI ici, on garde tout cote client pour
            // simplifier la gestion du media stream)
            const overlay = document.createElement('div');
            overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.85);' +
                'display:flex;align-items:center;justify-content:center;z-index:9999;' +
                'padding:20px;';
            const dialog = document.createElement('div');
            dialog.style.cssText = 'background:white;border-radius:12px;padding:20px;' +
                'max-width:95vw;max-height:95vh;display:flex;flex-direction:column;' +
                'align-items:center;gap:12px;';
            dialog.innerHTML =
                '<h3 style="margin:0;font-size:18px;font-weight:600;">' +
                'Capture photo — pièce ' + partId + '</h3>' +
                '<div style="position:relative;">' +
                '  <video id="pistock-cam-video" autoplay playsinline muted ' +
                '         style="max-width:80vw;max-height:60vh;border-radius:8px;' +
                '         background:#000;"></video>' +
                '  <img id="pistock-cam-preview" style="display:none;max-width:80vw;' +
                '       max-height:60vh;border-radius:8px;">' +
                '</div>' +
                '<canvas id="pistock-cam-canvas" style="display:none;"></canvas>' +
                '<div id="pistock-cam-status" style="font-size:13px;color:#6b7280;' +
                '     min-height:20px;"></div>' +
                '<div id="pistock-cam-actions" style="display:flex;gap:10px;">' +
                '  <button id="pistock-cam-capture-btn" ' +
                '          style="padding:10px 20px;background:#2563eb;color:white;' +
                '          border:none;border-radius:6px;font-size:14px;cursor:pointer;">' +
                '    📷 Capturer</button>' +
                '  <button id="pistock-cam-retake-btn" style="display:none;' +
                '          padding:10px 20px;background:#6b7280;color:white;border:none;' +
                '          border-radius:6px;font-size:14px;cursor:pointer;">' +
                '    ↻ Reprendre</button>' +
                '  <button id="pistock-cam-save-btn" style="display:none;' +
                '          padding:10px 20px;background:#16a34a;color:white;border:none;' +
                '          border-radius:6px;font-size:14px;cursor:pointer;">' +
                '    💾 Enregistrer</button>' +
                '  <button id="pistock-cam-cancel-btn" ' +
                '          style="padding:10px 20px;background:#dc2626;color:white;' +
                '          border:none;border-radius:6px;font-size:14px;cursor:pointer;">' +
                '    ✕ Annuler</button>' +
                '</div>';
            overlay.appendChild(dialog);
            document.body.appendChild(overlay);

            const video = document.getElementById('pistock-cam-video');
            const canvas = document.getElementById('pistock-cam-canvas');
            const preview = document.getElementById('pistock-cam-preview');
            const status = document.getElementById('pistock-cam-status');
            const captureBtn = document.getElementById('pistock-cam-capture-btn');
            const retakeBtn = document.getElementById('pistock-cam-retake-btn');
            const saveBtn = document.getElementById('pistock-cam-save-btn');
            const cancelBtn = document.getElementById('pistock-cam-cancel-btn');

            let stream = null;
            let capturedBlob = null;

            const cleanup = () => {
                if (stream) {
                    stream.getTracks().forEach(t => t.stop());
                    stream = null;
                }
                overlay.remove();
            };

            // Lance le stream camera. facingMode='environment' = camera
            // arriere sur mobile (la plus utile pour photographier
            // une piece devant soi). Fallback sur 'user' si refuse.
            try {
                status.textContent = "Démarrage de la caméra…";
                stream = await navigator.mediaDevices.getUserMedia({
                    video: {
                        facingMode: { ideal: 'environment' },
                        width: { ideal: 1920 },
                        height: { ideal: 1080 }
                    },
                    audio: false
                });
                video.srcObject = stream;
                status.textContent = "Cadrez la pièce puis cliquez sur « Capturer »";
            } catch (err) {
                status.textContent = "";
                let msg = "Caméra inaccessible : " + (err.message || err.name);
                if (err.name === 'NotAllowedError') {
                    msg = "Accès caméra refusé. Autorisez-le dans les " +
                          "paramètres du navigateur.";
                } else if (err.name === 'NotFoundError') {
                    msg = "Aucune caméra détectée sur cet appareil.";
                }
                alert(msg);
                cleanup();
                return;
            }

            // Clic "Capturer" -> dessine la frame courante du video
            // dans le canvas, convertit en blob JPEG, affiche l'aperçu.
            captureBtn.addEventListener('click', () => {
                canvas.width = video.videoWidth;
                canvas.height = video.videoHeight;
                const ctx = canvas.getContext('2d');
                ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
                canvas.toBlob((blob) => {
                    if (!blob) {
                        alert("Échec de la capture.");
                        return;
                    }
                    capturedBlob = blob;
                    preview.src = URL.createObjectURL(blob);
                    video.style.display = 'none';
                    preview.style.display = 'block';
                    captureBtn.style.display = 'none';
                    retakeBtn.style.display = 'inline-block';
                    saveBtn.style.display = 'inline-block';
                    status.textContent = "Aperçu — Enregistrer ou Reprendre ?";
                }, 'image/jpeg', 0.85);
            });

            // Clic "Reprendre" -> on retourne au live preview
            retakeBtn.addEventListener('click', () => {
                if (preview.src) URL.revokeObjectURL(preview.src);
                preview.src = '';
                capturedBlob = null;
                video.style.display = 'block';
                preview.style.display = 'none';
                captureBtn.style.display = 'inline-block';
                retakeBtn.style.display = 'none';
                saveBtn.style.display = 'none';
                status.textContent = "Cadrez la pièce puis cliquez sur « Capturer »";
            });

            // Clic "Enregistrer" -> POST vers l'endpoint stock-photo
            saveBtn.addEventListener('click', async () => {
                if (!capturedBlob) return;
                status.textContent = "Envoi en cours…";
                saveBtn.disabled = true;
                retakeBtn.disabled = true;
                const formData = new FormData();
                // Le serveur accepte n'importe quel nom de fichier ; on
                // utilise un nom qui indique l'origine (camera) + la date.
                const ts = new Date().toISOString().replace(/[:.]/g, '-');
                formData.append('photo', capturedBlob, 'camera_' + ts + '.jpg');
                try {
                    const response = await fetch(
                        '/api/v1/parts/' + partId + '/stock-photo',
                        { method: 'POST', body: formData }
                    );
                    if (!response.ok) {
                        const err = await response.json().catch(() => ({}));
                        alert("Erreur upload : " + (err.detail || response.status));
                        saveBtn.disabled = false;
                        retakeBtn.disabled = false;
                        status.textContent = "Échec — vous pouvez réessayer";
                        return;
                    }
                    cleanup();
                    window.location.reload();
                } catch (err) {
                    alert("Erreur réseau : " + err.message);
                    saveBtn.disabled = false;
                    retakeBtn.disabled = false;
                    status.textContent = "Échec — vous pouvez réessayer";
                }
            });

            // Clic "Annuler" -> ferme le dialogue, coupe la camera
            cancelBtn.addEventListener('click', cleanup);
            // Echappe = Annuler aussi
            const escHandler = (e) => {
                if (e.key === 'Escape') {
                    cleanup();
                    document.removeEventListener('keydown', escHandler);
                }
            };
            document.addEventListener('keydown', escHandler);
        };
        </script>
    ''')

    # Dark header. The catalog is no longer the landing page (the
    # projects overview is, at "/"), so we expose the 🏠 button to go
    # back to it.
    render_app_header("PiStock — Catalog", show_home=True)

    # Main centered container, max width
    with ui.column().classes("w-full max-w-5xl mx-auto p-4 gap-4"):

        # Action bar: project filter on the left, buttons on the right
        with ui.row().classes("w-full items-center gap-2"):
            ui.label(_("Project:")).classes("text-sm text-gray-600")
            # The select is filled dynamically (may be empty if no
            # project exists yet). It DEFAULTS to "(No project)" so the
            # startup view stays light even when the catalog grows large;
            # the user can still switch to "All projects" or one project.
            # The full option list is populated by refresh_project_filter().
            project_filter = ui.select(
                options={"": _("All projects"), UNASSIGNED: _("(No project)")},
                value=UNASSIGNED,
                on_change=lambda _: refresh_list()
            ).classes("min-w-[200px]")

            # Keyword search (substring, case-insensitive) on the name.
            search_input = ui.input(
                label=_("Search"),
                on_change=lambda _: refresh_list()
            ).props("clearable dense").classes("min-w-[180px]")

            # Status filter (Init / Revue / Asset). Raw status values are
            # the domain vocabulary, kept as-is as option labels.
            status_filter = ui.select(
                options={"": _("All statuses"),
                         "Init": "Init", "Revue": "Revue", "Asset": "Asset"},
                value="",
                on_change=lambda _: refresh_list()
            ).props("dense").classes("min-w-[140px]")

            # Push the buttons to the right
            ui.element("div").classes("flex-grow")

            ui.button(_("Project"), on_click=lambda: open_projects_dialog()) \
                .props("color=primary outline").classes("text-base")
            ui.button(_("BOMs"), on_click=lambda: open_boms_dialog()) \
                .props("color=primary outline").classes("text-base")
            ui.button(_("Plugins"),
                       on_click=lambda: ui.navigate.to("/plugins")) \
                .props("color=primary outline").classes("text-base")
            ui.button(_("+ New part"), on_click=lambda: open_new_part_dialog()) \
                .props("color=primary").classes("text-base")

        def refresh_project_filter():
            """Reload the options of the project filter dropdown."""
            options = {"": _("All projects"), UNASSIGNED: _("(No project)")}
            for proj in fetch_projects():
                options[proj["code"]] = f"{proj['code']} — {proj['description'] or _('(no description)')}"
            # Keep the current value if it is still valid
            current = project_filter.value
            project_filter.options = options
            if current not in options:
                project_filter.value = ""
            project_filter.update()

        refresh_project_filter()

        # Honor the optional ?project=CODE query parameter: when the
        # catalog is opened from the projects overview, it lands already
        # filtered on that project. Ignored silently if the code is
        # unknown (the default UNASSIGNED filter then applies).
        if project and project in project_filter.options:
            project_filter.value = project

        # List container, filled then re-filled by refresh_list()
        list_container = ui.column().classes("w-full gap-3")

        def refresh_list():
            """Clear then re-fill the list from the database, applying
            the project filter (server-side), then the keyword search and
            status filter (client-side on the returned list)."""
            list_container.clear()
            code = project_filter.value or None
            parts = fetch_parts_full(project_code=code)

            # Client-side refinement
            term = (search_input.value or "").strip().lower()
            status = status_filter.value or None
            if term:
                # Match the name OR the free-form info field (hashtags /
                # subject codes), so a tag like "#cnc" or a usage code
                # finds every part carrying it.
                parts = [p for p in parts
                         if term in (p["part_name"] or "").lower()
                         or term in (p["info"] or "").lower()]
            if status:
                parts = [p for p in parts if p["status"] == status]

            if not parts:
                if term or status:
                    msg = _("No matching part.")
                elif code == UNASSIGNED:
                    msg = _("No unassigned part.")
                elif code:
                    msg = _("No part for project '{code}'.").format(code=code)
                else:
                    msg = _("No part in the database yet. "
                            "Click « + New part » or export one from FreeCAD.")
                with list_container:
                    ui.label(msg) \
                        .classes("text-gray-500 text-center p-8")
                return

            for part in parts:
                with list_container:
                    render_part_row(part, refresh_list)

        # First fill
        refresh_list()

        # --- "New part" dialog ----------------------------------------
        # Built once, opened on demand. NiceGUI lets us create the
        # dialog here and display it with .open().
        with ui.dialog() as new_part_dialog, ui.card().classes("min-w-[360px]"):
            ui.label(_("New part")).classes("text-lg font-medium")
            name_input = ui.input(_("Part name"), placeholder=_("e.g.: bracket-v2")) \
                .classes("w-full")
            error_label = ui.label("").classes("text-red-600 text-sm min-h-[1.2em]")
            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button(_("Cancel"), on_click=new_part_dialog.close) \
                    .props("flat")
                ui.button(_("Create"),
                          on_click=lambda: confirm_create_part()) \
                    .props("color=primary")

            def confirm_create_part():
                ok, msg, _new_id = create_part_in_db(name_input.value or "")
                if not ok:
                    error_label.text = msg
                    return
                error_label.text = ""
                ui.notify(msg, type="positive")
                new_part_dialog.close()
                refresh_list()

            # Enter key in the field -> submit
            name_input.on("keydown.enter", lambda _: confirm_create_part())

        def open_new_part_dialog():
            name_input.value = ""
            error_label.text = ""
            new_part_dialog.open()

        # --- "Projects" dialog ----------------------------------------
        # Lists existing projects + inline creation form (revealable).
        # The code (AAA, AAB...) is generated by the server, the user
        # only enters the description.
        with ui.dialog() as projects_dialog, \
                ui.card().classes("min-w-[480px] max-w-[600px]"):
            ui.label(_("Projects")).classes("text-lg font-medium")

            # Scrollable container for the list of projects.
            # Cleared then filled by refresh_projects_list().
            projects_list_container = ui.column() \
                .classes("w-full gap-2 max-h-[400px] overflow-y-auto")

            # Creation form, hidden by default.
            with ui.column().classes("w-full gap-2 mt-2") as creation_form:
                ui.label(_("New project")).classes("text-sm font-medium")
                desc_input = ui.textarea(
                    placeholder=_("Description (optional)")) \
                    .classes("w-full").props("autogrow rows=3")
                proj_error = ui.label("") \
                    .classes("text-red-600 text-sm min-h-[1.2em]")
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button(_("Cancel"),
                              on_click=lambda: hide_creation_form()) \
                        .props("flat")
                    ui.button(_("Create"),
                              on_click=lambda: confirm_create_project()) \
                        .props("color=primary")
            creation_form.set_visibility(False)

            # Footer buttons: "+ Nouveau projet" + "Fermer"
            with ui.row().classes("w-full justify-between gap-2 mt-2") \
                    as footer_row:
                add_btn = ui.button(_("+ New project"),
                                     on_click=lambda: show_creation_form()) \
                    .props("color=primary outline")
                ui.button(_("Close"), on_click=projects_dialog.close) \
                    .props("flat")

            def refresh_projects_list():
                """Clear then re-fill the list from the database."""
                projects_list_container.clear()
                projects = fetch_projects()
                if not projects:
                    with projects_list_container:
                        ui.label(_("No project yet. "
                                   "Click « + New project » to create one.")) \
                            .classes("text-gray-500 text-sm text-center p-4")
                    return
                for proj in projects:
                    with projects_list_container:
                        with ui.card().classes("w-full p-3"):
                            with ui.row().classes("items-start gap-3 no-wrap"):
                                # Code as a large badge
                                ui.label(proj["code"]) \
                                    .classes("text-lg font-mono font-bold "
                                              "text-blue-700 bg-blue-50 "
                                              "px-2 py-1 rounded "
                                              "flex-shrink-0")
                                # Description (or italic if empty)
                                desc = proj["description"]
                                if desc:
                                    ui.label(desc) \
                                        .classes("text-sm text-stone-700 "
                                                  "whitespace-pre-wrap "
                                                  "flex-grow")
                                else:
                                    ui.label(_("(no description)")) \
                                        .classes("text-sm text-gray-400 "
                                                  "italic flex-grow")
                                # Delete button (admin + empty project)
                                def _make_del(p=proj):
                                    def h():
                                        confirm_delete_project(
                                            p,
                                            on_done=lambda: (
                                                refresh_projects_list(),
                                                refresh_project_filter(),
                                            ))
                                    return h
                                ui.button(
                                    icon="delete",
                                    on_click=_make_del()) \
                                    .props("flat round dense color=grey-6") \
                                    .classes("flex-shrink-0") \
                                    .tooltip(_("Delete this project"))

            def show_creation_form():
                desc_input.value = ""
                proj_error.text = ""
                creation_form.set_visibility(True)
                add_btn.set_visibility(False)

            def hide_creation_form():
                creation_form.set_visibility(False)
                add_btn.set_visibility(True)

            def confirm_create_project():
                ok, msg, code = create_project_in_db(desc_input.value or "")
                if not ok:
                    proj_error.text = msg
                    return
                proj_error.text = ""
                ui.notify(msg, type="positive")
                hide_creation_form()
                refresh_projects_list()
                # The filter dropdown must also learn about the new project
                refresh_project_filter()

        def open_projects_dialog():
            # We refresh on every open (in case another tab/user has
            # added projects in the meantime).
            hide_creation_form_silently()
            refresh_projects_list()
            projects_dialog.open()

        def hide_creation_form_silently():
            """Reset the form state without notification."""
            creation_form.set_visibility(False)
            add_btn.set_visibility(True)


# ======================================================================
#  RENDERING A ROW
# ======================================================================
def render_part_row(part: dict, on_change):
    """Render a part row. 'on_change' is called after an action that
    modifies the database (photo upload, project/status/lock change),
    to refresh the list."""

    part_id = part["id"]
    locked = part["locked"]

    # Status badge colors depending on the value
    status_colors = {
        "Init":  "bg-gray-100 text-gray-700",
        "Revue": "bg-amber-100 text-amber-800",
        "Asset": "bg-green-100 text-green-800",
    }
    status_cls = status_colors.get(part["status"], status_colors["Init"])

    with ui.card().classes("w-full p-4"):
        with ui.row().classes("w-full items-center gap-3 no-wrap"):

            # --- Lock (padlock icon, clickable) ---------------------
            # Toggle on click. Visually distinct depending on the state.
            lock_icon = "lock" if locked else "lock_open"
            lock_color = "text-red-600" if locked else "text-gray-400"

            def make_toggle_lock(pid=part_id, is_locked=locked):
                def do_toggle():
                    # NB: third element bound to a named var, not `_`,
                    # which is the i18n translation function.
                    ok, msg, _new_locked = toggle_part_lock_db(pid)
                    if ok:
                        ui.notify(msg, type="info")
                        on_change()
                    else:
                        ui.notify(msg, type="negative")
                def handler():
                    # Lock: unrestricted. Unlock: admin required.
                    if is_locked:
                        _ensure_admin(do_toggle)
                    else:
                        do_toggle()
                return handler

            ui.button(icon=lock_icon, on_click=make_toggle_lock()) \
                .props(f"flat round dense") \
                .classes(f"{lock_color} flex-shrink-0") \
                .tooltip(_("Locked — click to unlock")
                          if locked else _("Click to lock"))

            # --- "⋯" button -> part options dialog -----------------
            # Entry point for the less frequent actions: deletion, and
            # later renaming / duplication / etc.
            def make_open_options(p=part):
                def handler():
                    open_part_options_dialog(p, on_change)
                return handler
            ui.button(icon="more_horiz", on_click=make_open_options()) \
                .props("flat round dense color=grey-7") \
                .classes("flex-shrink-0") \
                .tooltip(_("Part options"))

            # --- Name + version (side by side) ----------------------
            with ui.column().classes("gap-0 flex-grow"):
                with ui.row().classes("items-baseline gap-2 no-wrap"):
                    ui.label(part["part_name"]) \
                        .classes("text-base font-medium")
                    if part["version"]:
                        ui.label(part["version"]) \
                            .classes("text-xs font-mono text-gray-500")

                # --- Project badge (clickable -> assign dialog) -----
                with ui.row().classes("items-center gap-1 no-wrap mt-1"):
                    proj_code = part["project_code"]
                    if proj_code:
                        proj_label = ui.label(proj_code) \
                            .classes("text-xs font-mono font-bold "
                                      "text-blue-700 bg-blue-50 "
                                      "px-2 py-0.5 rounded "
                                      "cursor-pointer hover:bg-blue-100")
                    else:
                        proj_label = ui.label(_("no project")) \
                            .classes("text-xs italic text-gray-400 "
                                      "px-2 py-0.5 rounded border "
                                      "border-dashed border-gray-300 "
                                      "cursor-pointer hover:border-blue-400 "
                                      "hover:text-blue-500")
                    if not locked:
                        proj_label.on("click",
                                       lambda p=part: open_assign_project_dialog(p, on_change))
                        proj_label.tooltip(_("Click to change project"))
                    else:
                        proj_label.classes("opacity-60")
                        proj_label.tooltip(_("Part locked"))

                    # --- Status badge (clickable -> cycle) ----------
                    status_label = ui.label(part["status"]) \
                        .classes(f"text-xs font-semibold {status_cls} "
                                  f"px-2 py-0.5 rounded")
                    if not locked:
                        status_label.classes("cursor-pointer hover:brightness-95")
                        # Cycle: Init -> Revue -> Asset -> Init
                        next_status = {"Init": "Revue",
                                        "Revue": "Asset",
                                        "Asset": "Init"}
                        def make_cycle(pid=part_id, current=part["status"]):
                            def handler():
                                ok, msg = set_part_status_db(
                                    pid, next_status[current])
                                if ok:
                                    ui.notify(msg, type="info")
                                    on_change()
                                else:
                                    ui.notify(msg, type="negative")
                            return handler
                        status_label.on("click", make_cycle())
                        status_label.tooltip(
                            _("Click → {status}").format(
                                status=next_status[part['status']]))
                    else:
                        status_label.classes("opacity-60")

            # --- Info field (searchable: hashtags / subject codes) --
            # Free-form tags/codes (manufacturing method, usage type…).
            # Editable even when the part is locked: organizational
            # metadata, not structural. Saved on blur / Enter; the value
            # is picked up by the search box on the next refresh.
            info_input = ui.input(
                value=part["info"] or "",
                placeholder=_("#tags, codes…"),
            ).props("dense clearable").classes("w-44 flex-shrink-0")
            info_input.tooltip(_("Searchable info: hashtags or subject "
                                  "codes (manufacturing, usage type, …)"))

            def make_save_info(pid=part_id, field=info_input):
                def handler(_e=None):
                    ok, msg = set_part_info_db(pid, field.value or "")
                    if not ok:
                        ui.notify(msg, type="negative")
                return handler
            info_input.on("blur", make_save_info())
            info_input.on("keydown.enter", make_save_info())

            # --- CAD thumbnail (clickable -> 3D viewer) ------------
            with ui.element("div").classes(
                    "w-20 h-20 bg-stone-100 rounded-lg flex items-center "
                    "justify-center overflow-hidden flex-shrink-0"):
                if part["thumbnail_url"]:
                    img = ui.image(part["thumbnail_url"]) \
                        .classes("w-full h-full object-contain")
                    if part["glb_url"]:
                        img.classes("cursor-pointer hover:scale-105 transition")
                        img.on("click",
                               lambda p=part: ui.navigate.to(f"/part/{p['id']}"))
                        img.tooltip(_("Click to view in 3D"))
                else:
                    ui.label(_("No thumbnail")) \
                        .classes("text-xs text-gray-400 text-center")

            # --- Stock photo + add/replace button ------------------
            render_stock_photo_cell(part, on_change)

            # --- Quantity ------------------------------------------
            qty = part["quantity"]
            qty_text = "—" if qty is None else str(qty)
            qty_color = "text-gray-300" if qty is None else "text-stone-800"
            ui.label(qty_text) \
                .classes(f"text-lg {qty_color} w-16 text-center flex-shrink-0")

            # --- Location ------------------------------------------
            loc = part["location"]
            loc_text = loc if loc else "—"
            loc_color = "text-gray-300" if not loc else "text-stone-700"
            ui.label(loc_text) \
                .classes(f"text-sm {loc_color} w-32 flex-shrink-0")

            # --- Stock button ("inventory" icon, on the right) -----
            # Opens an edit dialog (quantity, location, supply,
            # component datasheet). The lock does not apply to stock.
            def make_open_stock(p=part):
                return lambda: open_stock_dialog(p, on_change)
            ui.button(icon="inventory_2",
                       on_click=make_open_stock()) \
                .props("flat round dense color=primary") \
                .classes("flex-shrink-0") \
                .tooltip(_("Manage stock"))


def render_stock_photo_cell(part: dict, on_change):
    """Stock photo cell: image + "Replace" button, or a large dashed
    "Add" button if there is no photo yet.

    APPROACH: we use plain HTML via ui.html() with a <label> that
    contains a hidden <input type="file">. Clicking the label triggers
    the native file picker (standard HTML behavior, works everywhere).
    The upload is then posted via fetch() to the REST endpoint
    /api/v1/parts/{id}/stock-photo. This approach is more reliable than
    ui.upload + pickFiles and allows full styling control. The JS
    'uploadStockPhoto' is defined in the page <head>."""

    part_id = part["id"]
    # 'on_change' is no longer used here: the refresh happens on the
    # browser side via window.location.reload() after the upload.
    # We keep the parameter for compatibility with the existing call.
    # NB: do NOT rebind `_` to mark it unused — `_` is the i18n
    # translation function used in the f-strings below.
    del on_change

    if part["stock_img_url"]:
        # Existing photo: 📁 (file) or 📷 (camera) on the right
        ui.html(f'''
            <div class="flex flex-col items-center gap-1 flex-shrink-0">
                <div class="w-20 h-20 bg-stone-100 rounded-lg flex items-center justify-center overflow-hidden">
                    <img src="{part["stock_img_url"]}"
                         alt="{_("Stock photo")}"
                         data-pistock-zoom
                         title="{_("Click to enlarge")}"
                         class="w-full h-full object-contain cursor-zoom-in">
                </div>
                <div class="flex gap-2 text-xs">
                    <label class="text-blue-600 cursor-pointer hover:underline">
                        📁
                        <input type="file" accept="image/*" style="display:none"
                               data-stock-upload="{part_id}">
                    </label>
                    <a class="text-blue-600 cursor-pointer hover:underline"
                       data-pistock-capture="{part_id}"
                       title="{_("Take a photo")}">📷</a>
                </div>
            </div>
        ''')
    else:
        # No photo: large button for a file + small camera link
        ui.html(f'''
            <div class="flex flex-col items-center gap-1 flex-shrink-0">
                <label class="cursor-pointer" title="{_("Add a photo of the part in stock")}">
                    <div class="w-20 h-20 border-2 border-dashed border-stone-300 rounded-lg
                                flex flex-col items-center justify-center gap-0
                                text-stone-500 transition
                                hover:border-blue-500 hover:text-blue-500 hover:bg-blue-50">
                        <span class="text-2xl leading-none">📁</span>
                        <span class="text-xs mt-1">{_("File")}</span>
                    </div>
                    <input type="file" accept="image/*" style="display:none"
                           data-stock-upload="{part_id}">
                </label>
                <a class="text-xs text-blue-600 cursor-pointer hover:underline"
                   data-pistock-capture="{part_id}"
                   title="{_("Take a photo with the camera")}">📷 {_("Camera")}</a>
            </div>
        ''')


# ======================================================================
#  PAGE: 3D VIEWER
# ======================================================================
def open_part_options_dialog(part: dict, on_change):
    """Options dialog for a given part. Contains the actions that are
    less frequent than simple modification (deletion, and later
    renaming, duplication, etc.). The lock does NOT prevent access to
    this dialog, but prevents the deletion of a locked part (the button
    is grayed out in that case)."""
    with ui.dialog() as dialog, ui.card().classes("min-w-[440px]"):
        # Header: name + project code + status
        ui.label(_("Part options")) \
            .classes("text-base font-medium text-gray-600")
        with ui.row().classes("items-center gap-2"):
            ui.label(part["part_name"]) \
                .classes("text-lg font-bold")
            if part.get("version"):
                ui.label(part["version"]) \
                    .classes("text-xs font-mono text-gray-500")
        meta_bits = []
        if part.get("project_code"):
            meta_bits.append(_("project {code}").format(code=part['project_code']))
        if part.get("status"):
            meta_bits.append(_("status « {status} »").format(status=part['status']))
        if part.get("locked"):
            meta_bits.append(_("🔒 locked"))
        if meta_bits:
            ui.label(" • ".join(meta_bits)) \
                .classes("text-xs text-gray-500")

        ui.separator()

        # --- "Danger zone" section: deletion ------------------------
        # We keep deletion visually isolated (red color, right-aligned)
        # to avoid accidental clicks.
        with ui.column().classes("w-full gap-2 mt-2"):
            ui.label(_("⚠️ Danger zone")) \
                .classes("text-sm font-medium text-red-600")
            ui.label(_("Deleting a part permanently erases its PLM "
                       "revisions, its stock and its associated files. "
                       "Irreversible action.")) \
                .classes("text-xs text-gray-600")

            def on_delete():
                # Launch the confirmation. If OK, the other dialog will
                # handle the API call + the notification + the refresh.
                dialog.close()
                confirm_delete_part(part, on_change)

            ui.button(_("🗑 Permanently delete this part…"),
                       on_click=on_delete) \
                .props("color=negative outline") \
                .classes("self-end")

        # --- Close button -------------------------------------------
        with ui.row().classes("w-full justify-end mt-2"):
            ui.button(_("Close"), on_click=dialog.close).props("flat")

    dialog.open()


def confirm_delete_part(part: dict, on_change):
    # Admin guard: we open the real dialog only after login.
    return _ensure_admin(lambda: _confirm_delete_part_inner(part, on_change))

def _confirm_delete_part_inner(part: dict, on_change):
    """Final confirmation dialog for deleting a part. Displays the name
    in bold and a warning. On confirmation: calls delete_part_db; if it
    is refused because of BOMs, displays the full list as a notification
    inside the dialog."""
    with ui.dialog() as dialog, ui.card().classes("min-w-[440px]"):
        ui.label(_("Confirm deletion")) \
            .classes("text-lg font-bold")
        ui.label(_("You are about to permanently delete "
                   "the part « {name} ».").format(name=part['part_name'])) \
            .classes("text-sm")
        ui.label(_("All its PLM revisions, its stock and its associated "
                   "files will be erased. This operation is "
                   "irreversible.")) \
            .classes("text-sm text-gray-600")

        # Error area that will be filled if the part is in a BOM
        error_area = ui.column().classes("w-full gap-1")

        def do_delete():
            error_area.clear()
            ok, msg, blocking = delete_part_db(part["id"])
            if ok:
                ui.notify(msg, type="positive")
                dialog.close()
                on_change()
                return
            # Failure: if it is because of a BOM, we display the list
            # directly in the dialog (no toast, so it can be read
            # calmly).
            if blocking:
                with error_area:
                    with ui.card().classes(
                            "w-full bg-red-50 border-l-4 "
                            "border-red-400 p-3 mt-2"):
                        ui.label(msg).classes("text-sm font-medium "
                                                "text-red-700")
                        ui.label(_("BOMs concerned:")) \
                            .classes("text-xs text-red-600 mt-1")
                        for b in blocking:
                            line = f"  • {b['code']}"
                            if b['description']:
                                line += f" — {b['description'][:40]}"
                            ui.label(line) \
                                .classes("text-xs font-mono "
                                          "text-red-600")
                        ui.label(_("Remove the part from these BOMs "
                                   "first, then try again.")) \
                            .classes("text-xs text-gray-600 mt-1")
            else:
                ui.notify(msg, type="negative")

        with ui.row().classes("w-full justify-end gap-2 mt-3"):
            ui.button(_("Cancel"), on_click=dialog.close).props("flat")
            ui.button(_("Permanently delete"),
                       on_click=do_delete) \
                .props("color=negative")

    dialog.open()


# ======================================================================
#  DIALOG: PROJECT ASSIGNMENT
# ======================================================================
def open_assign_project_dialog(part: dict, on_change):
    projects = fetch_projects()
    last_used_id = fetch_last_used_project_id()
    current_id = part["id_project"]
    part_id = part["id"]
    part_name = part["part_name"]

    # Build the dialog. We close and destroy it after use to avoid
    # accumulating dialogs on every open.
    with ui.dialog() as dialog, ui.card().classes("min-w-[440px] max-w-[600px]"):
        ui.label(_("Assign a project to « {name} »").format(name=part_name)) \
            .classes("text-lg font-medium")

        list_container = ui.column() \
            .classes("w-full gap-2 max-h-[360px] overflow-y-auto")

        # Project creation form, hidden by default
        with ui.column().classes("w-full gap-2 mt-2") as creation_form:
            ui.label(_("New project")).classes("text-sm font-medium")
            desc_input = ui.textarea(
                placeholder=_("Description (optional)")) \
                .classes("w-full").props("autogrow rows=2")
            err_label = ui.label("") \
                .classes("text-red-600 text-sm min-h-[1.2em]")
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button(_("Cancel"),
                          on_click=lambda: hide_creation()) \
                    .props("flat")
                ui.button(_("Create and assign"),
                          on_click=lambda: confirm_create_and_assign()) \
                    .props("color=primary")
        creation_form.set_visibility(False)

        # Footer: "+ Nouveau projet" / Dissocier / Fermer
        with ui.row().classes("w-full justify-between gap-2 mt-2"):
            add_btn = ui.button(_("+ New project"),
                                 on_click=lambda: show_creation()) \
                .props("color=primary outline")
            with ui.row().classes("gap-2"):
                if current_id is not None:
                    ui.button(_("Unassign"),
                              on_click=lambda: do_assign(None)) \
                        .props("flat color=negative")
                ui.button(_("Close"), on_click=dialog.close).props("flat")

        def render_options():
            list_container.clear()
            if not projects:
                with list_container:
                    ui.label(_("No project yet. "
                               "Create one with « + New project ».")) \
                        .classes("text-gray-500 text-sm text-center p-4")
                return
            for proj in projects:
                with list_container:
                    is_current = (proj["id"] == current_id)
                    is_last = (proj["id"] == last_used_id and not is_current)
                    # Special border if current project or last used
                    extra = ""
                    if is_current:
                        extra = " border-2 border-blue-500"
                    elif is_last:
                        extra = " border-2 border-dashed border-amber-400"
                    with ui.card().classes(f"w-full p-3 cursor-pointer "
                                            f"hover:bg-blue-50 transition"
                                            + extra) as card:
                        with ui.row().classes("items-start gap-3 no-wrap"):
                            ui.label(proj["code"]) \
                                .classes("text-base font-mono font-bold "
                                          "text-blue-700 bg-blue-50 "
                                          "px-2 py-1 rounded flex-shrink-0")
                            with ui.column().classes("gap-0 flex-grow"):
                                desc = proj["description"] or _("(no description)")
                                ui.label(desc) \
                                    .classes("text-sm text-stone-700 "
                                              "whitespace-pre-wrap")
                                if is_current:
                                    ui.label(_("Current project")) \
                                        .classes("text-xs text-blue-600 font-medium")
                                elif is_last:
                                    ui.label(_("Last used")) \
                                        .classes("text-xs text-amber-600")
                    # Click on the card = assign
                    card.on("click", lambda pid=proj["id"]: do_assign(pid))

        def do_assign(project_id):
            ok, msg = assign_project_to_part(part_id, project_id)
            if ok:
                ui.notify(msg, type="positive")
                dialog.close()
                on_change()
            else:
                ui.notify(msg, type="negative")

        def show_creation():
            desc_input.value = ""
            err_label.text = ""
            creation_form.set_visibility(True)
            add_btn.set_visibility(False)

        def hide_creation():
            creation_form.set_visibility(False)
            add_btn.set_visibility(True)

        def confirm_create_and_assign():
            # Create the project then assign it to the part immediately
            ok, msg, code = create_project_in_db(desc_input.value or "")
            if not ok:
                err_label.text = msg
                return
            # The project has just been created: we find its id by
            # searching by code (unique).
            import main
            with Session(main.engine) as s:
                proj = s.exec(
                    select(main.Project).where(main.Project.code == code)
                ).first()
                new_id = proj.id if proj else None
            if new_id is None:
                err_label.text = _("Project created but not found, aborting.")
                return
            ok2, msg2 = assign_project_to_part(part_id, new_id)
            if ok2:
                ui.notify(_("Project {code} created and assigned.").format(code=code),
                          type="positive")
                dialog.close()
                on_change()
            else:
                ui.notify(msg2, type="negative")

        render_options()
        dialog.open()


# ======================================================================
#  DIALOG: EDITING A PART'S STOCK
# ======================================================================
# Opens a dialog with: quantity (number), location (input), supply
# (textarea), and a button to upload a component datasheet. The
# uploaded datasheet goes into /data-pistock/uploads/doc/ via the REST
# endpoint /api/v1/parts/{id}/stock-doc (see the JS listener
# "data-stock-doc").
def open_stock_dialog(part: dict, on_change):
    part_id = part["id"]
    part_name = part["part_name"]
    # Current state read from the database (the 'part' passed in may be
    # stale if the user has modified the stock in another tab).
    stock = fetch_stock(part_id)

    with ui.dialog() as dialog, ui.card().classes("min-w-[480px] max-w-[600px]"):
        ui.label(_("Stock — « {name} »").format(name=part_name)) \
            .classes("text-lg font-medium")

        # --- Editable fields ------------------------------------------
        qty_input = ui.number(label=_("Quantity"),
                               value=stock["quantity"] or 0,
                               min=0, step=1, format="%d") \
            .classes("w-full")
        loc_input = ui.input(label=_("Location"),
                              value=stock["location"] or "",
                              placeholder=_("e.g.: Drawer A3, shelf 2")) \
            .classes("w-full")
        supply_input = ui.textarea(
                label=_("Supply"),
                value=stock["supply"] or "",
                placeholder=_("Supply URL, supplier, notes...")) \
            .classes("w-full").props("autogrow rows=3")

        # --- Component datasheet -------------------------------------
        # If a datasheet already exists, we display a link to view it.
        # The "Choisir un fichier" button opens the file picker and the
        # upload is triggered automatically via the global JS listener
        # (data-stock-doc).
        with ui.column().classes("w-full mt-2"):
            ui.label(_("Component datasheet")).classes("text-sm text-gray-600")
            doc_url = stock["doc_url"]
            if doc_url:
                # Link to the current datasheet (extract just the
                # displayed name by removing the directory and prefix).
                doc_name = doc_url.split("/")[-1]
                # Remove the _YYYYMMDD_HHMMSS suffix for display
                import re
                display_name = re.sub(r"_\d{8}_\d{6}", "", doc_name)
                with ui.row().classes("items-center gap-2"):
                    ui.html(
                        f'<a href="{doc_url}" target="_blank" '
                        f'class="text-blue-600 hover:underline text-sm">'
                        f'📄 {display_name}</a>'
                    )
                replace_label_text = _("Replace the datasheet")
            else:
                ui.label(_("(no datasheet saved)")) \
                    .classes("text-sm text-gray-400 italic")
                replace_label_text = _("Choose a file")

            # Upload button: same approach as for the stock photos
            # (HTML <label> + hidden input, intercepted by the global
            # JS listener).
            ui.html(f'''
                <label class="inline-flex items-center gap-2 cursor-pointer
                              text-blue-600 hover:underline text-sm mt-1">
                    <span>📎 {replace_label_text}</span>
                    <input type="file"
                           accept=".pdf,.doc,.docx,.txt,.md,image/*"
                           style="display:none"
                           data-stock-doc="{part_id}">
                </label>
            ''')

        # --- OK / Cancel buttons -------------------------------------
        with ui.row().classes("w-full justify-end gap-2 mt-3"):
            ui.button(_("Cancel"), on_click=dialog.close).props("flat")
            ui.button(_("Save"),
                      on_click=lambda: confirm_save()) \
                .props("color=primary")

        def confirm_save():
            ok, msg = save_stock(
                part_id,
                int(qty_input.value or 0),
                loc_input.value,
                supply_input.value
            )
            if ok:
                ui.notify(msg, type="positive")
                dialog.close()
                on_change()
            else:
                ui.notify(msg, type="negative")

        dialog.open()


# ======================================================================
#  DIALOG: LIST OF BOMs (+ creation + stock actions)
# ======================================================================
def open_boms_dialog():
    """Main BOM dialog: list, creation, and stock actions (add/remove
    N times). Clicking a row opens the sub-dialog for editing the BOM
    lines."""

    with ui.dialog() as dialog, ui.card().classes("min-w-[760px] max-w-[900px]"):
        ui.label(_("BOMs (bills of materials)")).classes("text-lg font-medium")

        list_container = ui.column() \
            .classes("w-full gap-2 max-h-[420px] overflow-y-auto")

        # --- Creation form (hidden by default) ------------------------
        with ui.column().classes("w-full gap-2 mt-2") as creation_form:
            ui.label(_("New BOM")).classes("text-sm font-medium")
            desc_input = ui.textarea(
                placeholder=_("Description (optional)")) \
                .classes("w-full").props("autogrow rows=2")
            # Project selector (optional): allows attaching the BOM to
            # an existing project directly at creation time.
            project_select = ui.select(
                options={0: _("(No project)")},  # populated in render()
                value=0, label=_("Project (optional)")
            ).classes("w-full")
            err_label = ui.label("") \
                .classes("text-red-600 text-sm min-h-[1.2em]")
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button(_("Cancel"),
                          on_click=lambda: hide_creation()) \
                    .props("flat")
                ui.button(_("Create"),
                          on_click=lambda: confirm_create()) \
                    .props("color=primary")
        creation_form.set_visibility(False)

        # --- Footer: "+ Nouvelle BOM" and "Fermer" -------------------
        with ui.row().classes("w-full justify-between gap-2 mt-2"):
            add_btn = ui.button(_("+ New BOM"),
                                 on_click=lambda: show_creation()) \
                .props("color=primary outline")
            ui.button(_("Close"), on_click=dialog.close).props("flat")

        def show_creation():
            desc_input.value = ""
            project_select.value = 0
            err_label.text = ""
            # Reload the list of projects in the selector
            options = {0: _("(No project)")}
            for proj in fetch_projects():
                options[proj["id"]] = f"{proj['code']} — {(proj['description'] or '')[:30]}"
            project_select.options = options
            project_select.update()
            creation_form.set_visibility(True)
            add_btn.set_visibility(False)

        def hide_creation():
            creation_form.set_visibility(False)
            add_btn.set_visibility(True)

        def confirm_create():
            id_proj = project_select.value or None
            if id_proj == 0:
                id_proj = None
            ok, msg, code = create_bom_db(desc_input.value or "", id_proj)
            if not ok:
                err_label.text = msg
                return
            ui.notify(msg, type="positive")
            hide_creation()
            render_boms_list()

        def render_boms_list():
            list_container.clear()
            boms = fetch_boms()
            if not boms:
                with list_container:
                    ui.label(_("No BOM. Click « + New BOM » to create one.")) \
                        .classes("text-gray-500 text-sm text-center p-4")
                return
            for bom in boms:
                with list_container:
                    render_bom_row(bom)

        def render_bom_row(bom):
            with ui.card().classes("w-full p-3"):
                with ui.row().classes("items-center gap-3 w-full no-wrap"):
                    # Code
                    ui.label(bom["code"]) \
                        .classes("text-sm font-mono font-bold "
                                  "text-blue-700 bg-blue-50 "
                                  "px-2 py-1 rounded flex-shrink-0")
                    # Description + project
                    with ui.column().classes("gap-0 flex-grow"):
                        desc = bom["description"] or _("(no description)")
                        ui.label(desc).classes("text-sm font-medium")
                        meta = _("{count} line(s)").format(count=bom['line_count'])
                        if bom["project_code"]:
                            meta += _(" • project {code}").format(code=bom['project_code'])
                        ui.label(meta).classes("text-xs text-gray-500")

                    # "Éditer" button
                    def make_edit(bid=bom["id"]):
                        def handler():
                            dialog.close()
                            open_bom_edit_dialog(bid)
                        return handler
                    ui.button(icon="edit", on_click=make_edit()) \
                        .props("flat round dense color=primary") \
                        .tooltip(_("Edit the lines"))

                    # Stock +/- (with a mini-prompt for the factor)
                    def make_stock_apply(bid=bom["id"],
                                          direction="add"):
                        def handler():
                            open_bom_stock_dialog(bid, direction,
                                                   on_done=render_boms_list)
                        return handler
                    ui.button(icon="add", on_click=make_stock_apply(
                                bid=bom["id"], direction="add")) \
                        .props("flat round dense color=positive") \
                        .tooltip(_("Add to stock"))
                    ui.button(icon="remove", on_click=make_stock_apply(
                                bid=bom["id"], direction="sub")) \
                        .props("flat round dense color=warning") \
                        .tooltip(_("Remove from stock"))

                    # Deletion (with confirmation)
                    def make_delete(bid=bom["id"], code=bom["code"]):
                        def handler():
                            confirm_delete_bom(bid, code,
                                                on_done=render_boms_list)
                        return handler
                    ui.button(icon="delete", on_click=make_delete()) \
                        .props("flat round dense color=negative") \
                        .tooltip(_("Delete this BOM"))

        render_boms_list()
        dialog.open()


def confirm_delete_bom(bom_id: int, code: str, on_done):
    return _ensure_admin(lambda: _confirm_delete_bom_inner(bom_id, code, on_done))

def _confirm_delete_bom_inner(bom_id: int, code: str, on_done):
    """Confirmation dialog for deleting a BOM."""
    with ui.dialog() as d, ui.card():
        ui.label(_("Delete BOM « {code} »?").format(code=code)) \
            .classes("text-base font-medium")
        ui.label(_("This action also deletes all its lines. "
                   "The stock of the parts is NOT modified.")) \
            .classes("text-sm text-gray-600 max-w-[400px]")
        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button(_("Cancel"), on_click=d.close).props("flat")
            def confirm():
                ok, msg = delete_bom_db(bom_id)
                ui.notify(msg, type="positive" if ok else "negative")
                d.close()
                if ok:
                    on_done()
            ui.button(_("Delete"), on_click=confirm) \
                .props("color=negative")
    d.open()


def open_bom_stock_dialog(bom_id: int, direction: str, on_done):
    """Mini-dialog that asks for the factor (how many times to apply
    the BOM) then applies it. direction='add' or 'sub'."""
    is_add = (direction == "add")
    title = _("Add to stock") if is_add else _("Remove from stock")
    verb_color = "positive" if is_add else "warning"

    detail = fetch_bom_detail(bom_id)
    if detail is None:
        ui.notify(_("BOM not found."), type="negative")
        return
    if not detail["lines"]:
        ui.notify(_("This BOM is empty."), type="warning")
        return

    with ui.dialog() as d, ui.card().classes("min-w-[440px]"):
        ui.label(_("{title} — BOM {code}").format(title=title, code=detail['code'])) \
            .classes("text-lg font-medium")
        ui.label(_("How many times?")).classes("text-sm text-gray-600")
        factor_input = ui.number(value=1, min=1, step=1, format="%d") \
            .classes("w-full")
        # Recap of the upcoming changes: we display the TOTALS per leaf
        # part after flattening the hierarchy (recursion via
        # _flatten_bom). This is what will actually be applied to the stock.
        ui.label(_("Effects on stock (leaf parts):")) \
            .classes("text-sm font-medium mt-2")
        recap = ui.column().classes("gap-1")
        def refresh_recap():
            recap.clear()
            f = int(factor_input.value or 1)
            sign = "+" if is_add else "−"
            # Computation via the server-side flatten
            import main
            with Session(main.engine) as session:
                try:
                    totals = main._flatten_bom(session, bom_id, factor=f)
                    # Pre-load the part names for display
                    parts_by_id = {
                        p.id: p.part_name for p in session.exec(
                            select(main.Parts)
                            .where(main.Parts.id.in_(totals.keys()))
                        ).all()
                    } if totals else {}
                except Exception as e:
                    with recap:
                        ui.label(_("⚠️  Error: {error}").format(error=e)) \
                            .classes("text-xs text-red-600")
                    return
            with recap:
                if not totals:
                    ui.label(_("(empty BOM)")).classes("text-xs text-gray-500")
                else:
                    for pid, delta in totals.items():
                        name = parts_by_id.get(pid, f"#{pid}")
                        ui.label(f"  {sign}{delta} × {name}") \
                            .classes("text-xs font-mono text-gray-700")
        factor_input.on("update:model-value", lambda _: refresh_recap())
        refresh_recap()

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button(_("Cancel"), on_click=d.close).props("flat")
            def confirm():
                f = int(factor_input.value or 1)
                ok, msg, shortages = bom_stock_apply(bom_id, f, direction)
                if not ok and shortages:
                    # Build a detailed message of the shortages
                    lines = [_("  • {name} : need {needed}, "
                               "available {available} (missing {missing})").format(
                                   name=s['part_name'], needed=s['needed'],
                                   available=s['available'], missing=s['missing'])
                             for s in shortages]
                    full_msg = f"{msg}\n" + "\n".join(lines)
                    ui.notify(full_msg, type="negative",
                               multi_line=True,
                               position="center", timeout=8000)
                    return
                ui.notify(msg, type="positive" if ok else "negative")
                if ok:
                    d.close()
                    on_done()
            ui.button(_("Save"), on_click=confirm).props(f"color={verb_color}")
    d.open()


# ======================================================================
#  DIALOG: EDITING THE LINES OF A BOM
# ======================================================================
def open_bom_edit_dialog(bom_id: int):
    """Dialog for editing the lines of a BOM: add, modify quantity
    (inline), delete."""
    detail = fetch_bom_detail(bom_id)
    if detail is None:
        ui.notify(_("BOM not found."), type="negative")
        return

    # Load all parts for the add selector
    parts = fetch_parts_full()

    with ui.dialog() as dialog, ui.card().classes("min-w-[640px] max-w-[800px]"):
        # Header: code + description
        header_text = _("BOM {code}").format(code=detail['code'])
        if detail["description"]:
            header_text += f" — {detail['description']}"
        ui.label(header_text).classes("text-lg font-medium")

        # List of lines
        lines_container = ui.column().classes("w-full gap-1")

        def render_lines():
            """Reload the data and redraw the lines."""
            nonlocal detail
            detail = fetch_bom_detail(bom_id)
            lines_container.clear()
            if not detail["lines"]:
                with lines_container:
                    ui.label(_("No line. Add a part below.")) \
                        .classes("text-gray-500 text-sm text-center p-3")
                return
            for line in detail["lines"]:
                with lines_container:
                    render_line_row(line)

        def render_line_row(line):
            with ui.row().classes("w-full items-center gap-3 no-wrap "
                                    "border-b border-gray-200 py-2"):
                # Name column: differs depending on the type
                if line["line_type"] == "part":
                    # Part: simple name
                    ui.label(line["part_name"]) \
                        .classes("text-sm flex-grow")
                else:
                    # Sub-BOM: clickable blue badge + description
                    sub_id = line["id_subbom"]
                    def make_open_sub(sid=sub_id):
                        def handler():
                            dialog.close()
                            open_bom_edit_dialog(sid)
                        return handler
                    with ui.row().classes("flex-grow items-center gap-2 "
                                           "cursor-pointer") \
                            .on("click", make_open_sub()):
                        ui.label(line["subbom_code"]).classes(
                            "text-xs font-mono font-bold "
                            "text-blue-700 bg-blue-100 px-2 py-0.5 rounded")
                        desc = (line["subbom_description"]
                                or _("(no description)"))
                        ui.label(desc).classes(
                            "text-sm text-blue-700 hover:underline")

                # Editable quantity (common to both types)
                qty_input = ui.number(value=line["quantity"],
                                       min=1, step=1, format="%d") \
                    .classes("w-24")
                def make_save(lid=line["id"], inp=qty_input):
                    def handler():
                        ok, msg = update_bom_line_db(lid,
                                                       int(inp.value or 1))
                        if not ok:
                            ui.notify(msg, type="negative")
                            render_lines()
                    return handler
                qty_input.on("blur", make_save())

                # Delete button
                def make_del(lid=line["id"]):
                    def handler():
                        ok, msg = delete_bom_line_db(lid)
                        ui.notify(msg, type="positive" if ok else "negative")
                        if ok:
                            render_lines()
                    return handler
                ui.button(icon="delete", on_click=make_del()) \
                    .props("flat round dense color=negative")

        # --- Add form at the bottom: toggle Part / Sub-BOM -----------
        # Load the list of other BOMs (all except the current BOM,
        # since it cannot reference itself)
        all_boms = fetch_boms()
        other_boms = [b for b in all_boms if b["id"] != bom_id]
        bom_options = {
            b["id"]: f"{b['code']} — {(b['description'] or '')[:30]}"
            for b in other_boms
        }
        def parts_for(code):
            """Part options ({id: name}) filtered by project code,
            UNASSIGNED (no project), or "" / None (all)."""
            if code == UNASSIGNED:
                sel = [p for p in parts if p["id_project"] is None]
            elif code:
                sel = [p for p in parts if p["project_code"] == code]
            else:
                sel = parts
            return {p["id"]: p["part_name"] for p in sel}

        # Project options for the part filter: only the projects that
        # actually have parts, plus "all" and (if relevant) "no project".
        part_proj_options = {"": _("All projects")}
        if any(p["id_project"] is None for p in parts):
            part_proj_options[UNASSIGNED] = _("(No project)")
        for _code in sorted({p["project_code"] for p in parts
                             if p["project_code"]}):
            part_proj_options[_code] = _code

        with ui.column().classes("w-full gap-2 mt-3 "
                                   "border-t border-gray-200 pt-3"):
            # Toggle for the type of line to add
            line_type_toggle = ui.toggle(
                {"part": _("Part"), "subbom": _("Sub-BOM")},
                value="part"
            ).props("dense")

            with ui.row().classes("w-full items-end gap-2"):
                # Optional project filter to narrow the part choice
                part_proj_filter = ui.select(
                    options=part_proj_options, value="",
                    label=_("Project")
                ).classes("w-40")
                # Part selector (visible by default)
                part_select = ui.select(
                    options=parts_for(""),
                    label=_("Part"), with_input=True
                ).classes("flex-grow")
                # Sub-BOM selector (hidden by default)
                subbom_select = ui.select(
                    options=bom_options,
                    label=_("Sub-BOM"), with_input=True
                ).classes("flex-grow")
                subbom_select.set_visibility(False)

                qty_add = ui.number(label=_("Qty"), value=1, min=1, step=1,
                                     format="%d").classes("w-24")

                def on_part_filter():
                    # Re-filter the part dropdown by the chosen project
                    part_select.options = parts_for(part_proj_filter.value)
                    part_select.value = None
                    part_select.update()
                part_proj_filter.on_value_change(on_part_filter)

                def on_type_change():
                    is_part = line_type_toggle.value == "part"
                    part_proj_filter.set_visibility(is_part)
                    part_select.set_visibility(is_part)
                    subbom_select.set_visibility(not is_part)
                    # Reset the values to avoid confusion
                    part_select.value = None
                    subbom_select.value = None
                line_type_toggle.on_value_change(on_type_change)

                def add_line():
                    qty = int(qty_add.value or 1)
                    if line_type_toggle.value == "part":
                        pid = part_select.value
                        if pid is None:
                            ui.notify(_("Select a part."), type="warning")
                            return
                        ok, msg = add_bom_line_db(bom_id, int(pid), qty)
                    else:
                        sid = subbom_select.value
                        if sid is None:
                            if not other_boms:
                                ui.notify(_("No other BOM available to be "
                                            "added as a sub-BOM."),
                                          type="warning")
                            else:
                                ui.notify(_("Select a sub-BOM."),
                                          type="warning")
                            return
                        ok, msg = add_bom_line_db(bom_id, None, qty,
                                                   subbom_id=int(sid))
                    ui.notify(msg, type="positive" if ok else "negative")
                    if ok:
                        part_select.value = None
                        subbom_select.value = None
                        qty_add.value = 1
                        render_lines()
                ui.button(_("+ Add"), on_click=add_line) \
                    .props("color=primary")

        with ui.row().classes("w-full justify-end mt-3"):
            ui.button(_("Close"), on_click=dialog.close).props("flat")

        render_lines()
        dialog.open()


# ======================================================================
#  PLUGIN SYSTEM
# ======================================================================
# Architecture:
# - A plugin is a folder in 'plugins/' containing at minimum:
#   - manifest.json: metadata (id, name, version, description, icon)
#   - plugin.py    : Python module with a register(app) function
# - At startup, we scan plugins/* and load each valid plugin.
# - A plugin registers its own routes/pages via @ui.page('/plugin/<id>').
# - The core exposes an index page /plugins that lists the installed
#   plugins as clickable cards.
#
# Strong convention: a plugin reads the database freely but writes only
# to its own tables (prefix 'plugin_<id>_*'). The core guarantees its
# tables; a plugin that crashes on load is logged and ignored, the rest
# keeps running.
import json
import importlib.util as _importlib_util
from pathlib import Path

# 'plugins/' folder at the project root (at the same level as
# frontend/ and backend/). Resolved from this file to be agnostic of
# the cwd.
def confirm_delete_project(project: dict, on_done):
    """Confirmation dialog to delete a project, behind the admin guard.
    If refused because it is not empty, displays the list."""
    def really_delete():
        with ui.dialog() as dialog, ui.card().classes("min-w-[440px]"):
            ui.label(_("Confirm project deletion")) \
                .classes("text-lg font-bold")
            ui.label(_("Permanently delete project "
                       "« {code} »?").format(code=project['code'])) \
                .classes("text-sm")
            ui.label(_("A project can only be deleted if it no longer "
                       "contains any part and any BOM.")) \
                .classes("text-sm text-gray-600")
            error_area = ui.column().classes("w-full gap-1")

            def do_delete():
                error_area.clear()
                ok, msg, blocking = delete_project_db(project["id"])
                if ok:
                    ui.notify(msg, type="positive")
                    dialog.close()
                    on_done()
                    return
                if blocking:
                    with error_area:
                        with ui.card().classes(
                                "w-full bg-red-50 border-l-4 "
                                "border-red-400 p-3 mt-2"):
                            ui.label(msg).classes(
                                "text-sm font-medium text-red-700")
                            if blocking["parts"]:
                                ui.label(_("Attached parts:")) \
                                    .classes("text-xs text-red-600 mt-1")
                                for p in blocking["parts"][:8]:
                                    ui.label(f"  • {p['part_name']}") \
                                        .classes("text-xs font-mono "
                                                  "text-red-600")
                                if len(blocking["parts"]) > 8:
                                    ui.label(_("  … and {count} "
                                               "others").format(
                                                   count=len(blocking['parts'])-8)) \
                                        .classes("text-xs text-red-600")
                            if blocking["boms"]:
                                ui.label(_("Attached BOMs:")) \
                                    .classes("text-xs text-red-600 mt-1")
                                for b in blocking["boms"][:8]:
                                    ui.label(f"  • {b['code']}") \
                                        .classes("text-xs font-mono "
                                                  "text-red-600")
                else:
                    ui.notify(msg, type="negative")

            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button(_("Cancel"), on_click=dialog.close).props("flat")
                ui.button(_("Delete"), on_click=do_delete) \
                    .props("color=negative")
        dialog.open()
    _ensure_admin(really_delete)
