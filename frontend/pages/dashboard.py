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

"""Page catalogue (/) : liste des pieces en cartes, filtres, et tous
les dialogues associes (options piece, suppression, assignation de
projet, stock, gestion des BOMs).
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
from db import (fetch_parts_full, fetch_last_used_project_id, assign_project_to_part, set_part_status_db, toggle_part_lock_db, fetch_stock, save_stock, create_part_in_db, fetch_projects, create_project_in_db, fetch_boms, fetch_bom_detail, create_bom_db, delete_bom_db, delete_part_db, add_bom_line_db, update_bom_line_db, delete_bom_line_db, bom_stock_apply, delete_project_db)


# ======================================================================
#  PAGE : DASHBOARD
# ======================================================================
@ui.page("/")
def dashboard_page():
    """Page principale : liste des pieces sous forme de cartes."""
    # Applique la langue choisie par l'utilisateur AVANT de construire
    # quoi que ce soit (les premiers appels a _() en dependent).
    _apply_user_lang()
    _register_pwa()
    # Premier demarrage : pas encore de mot de passe admin -> setup
    if not _admin_configured():
        _open_admin_setup_dialog()
    # Titre de l'onglet navigateur (visible dans la barre + historique)
    ui.page_title(_("PiStock — Catalog"))

    # JavaScript injecte au <head> de la page. Comme NiceGUI 3.x
    # sanitise le contenu de ui.html() et RETIRE les attributs 'on*'
    # (onchange, onclick...), on ne peut pas mettre onchange="..."
    # inline. A la place : event delegation. Un seul listener attache
    # au document detecte tous les change sur les inputs portant
    # data-stock-upload="{part_id}" et fait l'upload.
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
        }

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

    # En-tete sombre, comme dans la version HTML
    render_app_header("PiStock — Catalog")

    # Conteneur principal centre, largeur max
    with ui.column().classes("w-full max-w-5xl mx-auto p-4 gap-4"):

        # Barre d'actions : filtre projet a gauche, boutons a droite
        with ui.row().classes("w-full items-center gap-2"):
            ui.label(_("Project:")).classes("text-sm text-gray-600")
            # Le select est rempli dynamiquement (peut etre vide si
            # aucun projet existe encore). Initialise vide ici, peuple
            # par refresh_project_filter().
            project_filter = ui.select(
                options={"": _("All projects")},
                value="",
                on_change=lambda _: refresh_list()
            ).classes("min-w-[200px]")

            # Pousse les boutons a droite
            ui.element("div").classes("flex-grow")

            ui.button(_("Project"), on_click=lambda: open_projects_dialog()) \
                .props("color=primary outline").classes("text-base")
            ui.button("BOMs", on_click=lambda: open_boms_dialog()) \
                .props("color=primary outline").classes("text-base")
            ui.button("Plugins",
                       on_click=lambda: ui.navigate.to("/plugins")) \
                .props("color=primary outline").classes("text-base")
            ui.button(_("+ New part"), on_click=lambda: open_new_part_dialog()) \
                .props("color=primary").classes("text-base")

        def refresh_project_filter():
            """Recharge les options du dropdown de filtre projet."""
            options = {"": _("All projects")}
            for proj in fetch_projects():
                options[proj["code"]] = f"{proj['code']} — {proj['description'] or '(sans description)'}"
            # Conserver la valeur actuelle si elle est encore valide
            current = project_filter.value
            project_filter.options = options
            if current not in options:
                project_filter.value = ""
            project_filter.update()

        refresh_project_filter()

        # Conteneur de la liste, rempli puis re-rempli par refresh_list()
        list_container = ui.column().classes("w-full gap-3")

        def refresh_list():
            """Vide puis re-rempli la liste depuis la base, en
            appliquant le filtre projet s'il est selectionne."""
            list_container.clear()
            code = project_filter.value or None
            parts = fetch_parts_full(project_code=code)

            if not parts:
                msg = ("Aucune pièce dans la base pour l'instant. "
                       "Cliquez sur « + Nouvelle pièce » ou exportez-en "
                       "une depuis FreeCAD.")
                if code:
                    msg = f"Aucune pièce pour le projet '{code}'."
                with list_container:
                    ui.label(msg) \
                        .classes("text-gray-500 text-center p-8")
                return

            for part in parts:
                with list_container:
                    render_part_row(part, refresh_list)

        # Premier remplissage
        refresh_list()

        # --- Dialogue "Nouvelle piece" --------------------------------
        # Construit une fois, ouvert a la demande. NiceGUI permet de
        # creer le dialogue ici et de l'afficher avec .open().
        with ui.dialog() as new_part_dialog, ui.card().classes("min-w-[360px]"):
            ui.label("Nouvelle pièce").classes("text-lg font-medium")
            name_input = ui.input("Nom de la pièce", placeholder="ex: bracket-v2") \
                .classes("w-full")
            error_label = ui.label("").classes("text-red-600 text-sm min-h-[1.2em]")
            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button("Annuler", on_click=new_part_dialog.close) \
                    .props("flat")
                ui.button("Créer",
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

            # Touche Entree dans le champ -> valide
            name_input.on("keydown.enter", lambda _: confirm_create_part())

        def open_new_part_dialog():
            name_input.value = ""
            error_label.text = ""
            new_part_dialog.open()

        # --- Dialogue "Projets" ---------------------------------------
        # Liste les projets existants + formulaire de creation inline
        # (revelable). Le code (AAA, AAB...) est genere par le serveur,
        # l'utilisateur saisit juste la description.
        with ui.dialog() as projects_dialog, \
                ui.card().classes("min-w-[480px] max-w-[600px]"):
            ui.label("Projets").classes("text-lg font-medium")

            # Conteneur scrollable pour la liste des projets.
            # Vide puis rempli par refresh_projects_list().
            projects_list_container = ui.column() \
                .classes("w-full gap-2 max-h-[400px] overflow-y-auto")

            # Formulaire de creation, masque par defaut.
            with ui.column().classes("w-full gap-2 mt-2") as creation_form:
                ui.label("Nouveau projet").classes("text-sm font-medium")
                desc_input = ui.textarea(
                    placeholder="Description (optionnelle)") \
                    .classes("w-full").props("autogrow rows=3")
                proj_error = ui.label("") \
                    .classes("text-red-600 text-sm min-h-[1.2em]")
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Annuler",
                              on_click=lambda: hide_creation_form()) \
                        .props("flat")
                    ui.button("Créer",
                              on_click=lambda: confirm_create_project()) \
                        .props("color=primary")
            creation_form.set_visibility(False)

            # Boutons du pied : "+ Nouveau projet" + "Fermer"
            with ui.row().classes("w-full justify-between gap-2 mt-2") \
                    as footer_row:
                add_btn = ui.button("+ Nouveau projet",
                                     on_click=lambda: show_creation_form()) \
                    .props("color=primary outline")
                ui.button("Fermer", on_click=projects_dialog.close) \
                    .props("flat")

            def refresh_projects_list():
                """Vide puis re-rempli la liste depuis la base."""
                projects_list_container.clear()
                projects = fetch_projects()
                if not projects:
                    with projects_list_container:
                        ui.label("Aucun projet pour l'instant. "
                                 "Cliquez sur « + Nouveau projet » "
                                 "pour en créer un.") \
                            .classes("text-gray-500 text-sm text-center p-4")
                    return
                for proj in projects:
                    with projects_list_container:
                        with ui.card().classes("w-full p-3"):
                            with ui.row().classes("items-start gap-3 no-wrap"):
                                # Code en grosse pastille
                                ui.label(proj["code"]) \
                                    .classes("text-lg font-mono font-bold "
                                              "text-blue-700 bg-blue-50 "
                                              "px-2 py-1 rounded "
                                              "flex-shrink-0")
                                # Description (ou italique si vide)
                                desc = proj["description"]
                                if desc:
                                    ui.label(desc) \
                                        .classes("text-sm text-stone-700 "
                                                  "whitespace-pre-wrap "
                                                  "flex-grow")
                                else:
                                    ui.label("(aucune description)") \
                                        .classes("text-sm text-gray-400 "
                                                  "italic flex-grow")
                                # Bouton suppression (admin + projet vide)
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
                                    .tooltip("Supprimer ce projet")

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
                # Le dropdown de filtre doit aussi connaitre le nouveau projet
                refresh_project_filter()

        def open_projects_dialog():
            # On rafraichit a chaque ouverture (au cas ou un autre
            # onglet/utilisateur aurait ajoute des projets entre-temps).
            hide_creation_form_silently()
            refresh_projects_list()
            projects_dialog.open()

        def hide_creation_form_silently():
            """Reset l'etat du formulaire sans notification."""
            creation_form.set_visibility(False)
            add_btn.set_visibility(True)


# ======================================================================
#  RENDU D'UNE LIGNE
# ======================================================================
def render_part_row(part: dict, on_change):
    """Rendu d'une ligne de piece. 'on_change' est appele apres une
    action qui modifie la base (upload photo, changement de projet,
    de statut, de verrou), pour rafraichir la liste."""

    part_id = part["id"]
    locked = part["locked"]

    # Couleurs du badge statut selon la valeur
    status_colors = {
        "Init":  "bg-gray-100 text-gray-700",
        "Revue": "bg-amber-100 text-amber-800",
        "Asset": "bg-green-100 text-green-800",
    }
    status_cls = status_colors.get(part["status"], status_colors["Init"])

    with ui.card().classes("w-full p-4"):
        with ui.row().classes("w-full items-center gap-3 no-wrap"):

            # --- Verrou (icone cadenas, cliquable) ------------------
            # Toggle au clic. Visuellement distinct selon l'etat.
            lock_icon = "lock" if locked else "lock_open"
            lock_color = "text-red-600" if locked else "text-gray-400"

            def make_toggle_lock(pid=part_id, is_locked=locked):
                def do_toggle():
                    ok, msg, _ = toggle_part_lock_db(pid)
                    if ok:
                        ui.notify(msg, type="info")
                        on_change()
                    else:
                        ui.notify(msg, type="negative")
                def handler():
                    # Verrouiller : libre. Deverrouiller : admin requis.
                    if is_locked:
                        _ensure_admin(do_toggle)
                    else:
                        do_toggle()
                return handler

            ui.button(icon=lock_icon, on_click=make_toggle_lock()) \
                .props(f"flat round dense") \
                .classes(f"{lock_color} flex-shrink-0") \
                .tooltip("Verrouillée — cliquer pour déverrouiller"
                          if locked else "Cliquer pour verrouiller")

            # --- Bouton "⋯" -> dialogue d'options de la piece ------
            # Point d'entree pour les actions moins frequentes :
            # suppression, et plus tard renommage / duplication / etc.
            def make_open_options(p=part):
                def handler():
                    open_part_options_dialog(p, on_change)
                return handler
            ui.button(icon="more_horiz", on_click=make_open_options()) \
                .props("flat round dense color=grey-7") \
                .classes("flex-shrink-0") \
                .tooltip("Options de la pièce")

            # --- Nom + version (a cote) -----------------------------
            with ui.column().classes("gap-0 flex-grow"):
                with ui.row().classes("items-baseline gap-2 no-wrap"):
                    ui.label(part["part_name"]) \
                        .classes("text-base font-medium")
                    if part["version"]:
                        ui.label(part["version"]) \
                            .classes("text-xs font-mono text-gray-500")

                # --- Pastille projet (cliquable -> dialogue assign) -
                with ui.row().classes("items-center gap-1 no-wrap mt-1"):
                    proj_code = part["project_code"]
                    if proj_code:
                        proj_label = ui.label(proj_code) \
                            .classes("text-xs font-mono font-bold "
                                      "text-blue-700 bg-blue-50 "
                                      "px-2 py-0.5 rounded "
                                      "cursor-pointer hover:bg-blue-100")
                    else:
                        proj_label = ui.label("aucun projet") \
                            .classes("text-xs italic text-gray-400 "
                                      "px-2 py-0.5 rounded border "
                                      "border-dashed border-gray-300 "
                                      "cursor-pointer hover:border-blue-400 "
                                      "hover:text-blue-500")
                    if not locked:
                        proj_label.on("click",
                                       lambda p=part: open_assign_project_dialog(p, on_change))
                        proj_label.tooltip("Cliquer pour changer de projet")
                    else:
                        proj_label.classes("opacity-60")
                        proj_label.tooltip("Pièce verrouillée")

                    # --- Badge statut (cliquable -> cycle) ----------
                    status_label = ui.label(part["status"]) \
                        .classes(f"text-xs font-semibold {status_cls} "
                                  f"px-2 py-0.5 rounded")
                    if not locked:
                        status_label.classes("cursor-pointer hover:brightness-95")
                        # Cycle : Init -> Revue -> Asset -> Init
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
                            f"Cliquer → {next_status[part['status']]}")
                    else:
                        status_label.classes("opacity-60")

            # --- Vignette CAO (cliquable -> viewer 3D) -------------
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
                        img.tooltip("Cliquer pour voir en 3D")
                else:
                    ui.label("Pas de vignette") \
                        .classes("text-xs text-gray-400 text-center")

            # --- Photo de stock + bouton ajout/remplacement --------
            render_stock_photo_cell(part, on_change)

            # --- Quantite ------------------------------------------
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

            # --- Bouton stock (icone "inventory", a droite) --------
            # Ouvre un dialogue d'edition (quantite, location, supply,
            # fiche composant). Le verrou ne s'applique pas au stock.
            def make_open_stock(p=part):
                return lambda: open_stock_dialog(p, on_change)
            ui.button(icon="inventory_2",
                       on_click=make_open_stock()) \
                .props("flat round dense color=primary") \
                .classes("flex-shrink-0") \
                .tooltip("Gérer le stock")


def render_stock_photo_cell(part: dict, on_change):
    """Cellule de la photo de stock : image + bouton "Remplacer", ou
    gros bouton dashed "Ajouter" si pas encore de photo.

    APPROCHE : on utilise du HTML pur via ui.html() avec un <label>
    qui contient un <input type="file"> cache. Cliquer sur le label
    declenche le file picker natif (comportement HTML standard, marche
    partout). L'upload est ensuite poste via fetch() vers l'endpoint
    REST /api/v1/parts/{id}/stock-photo. Cette approche est plus fiable
    que ui.upload + pickFiles et permet un controle stylistique total.
    Le JS 'uploadStockPhoto' est defini dans le <head> de la page."""

    part_id = part["id"]
    # 'on_change' n'est plus utilise ici : le rafraichissement se
    # fait cote navigateur via window.location.reload() apres l'upload.
    # On garde le parametre pour compatibilite avec l'appel existant.
    _ = on_change

    if part["stock_img_url"]:
        # Photo existante : 📁 (file) ou 📷 (camera) à droite
        ui.html(f'''
            <div class="flex flex-col items-center gap-1 flex-shrink-0">
                <div class="w-20 h-20 bg-stone-100 rounded-lg flex items-center justify-center overflow-hidden">
                    <img src="{part["stock_img_url"]}"
                         alt="Photo stock"
                         class="w-full h-full object-contain">
                </div>
                <div class="flex gap-2 text-xs">
                    <label class="text-blue-600 cursor-pointer hover:underline">
                        📁
                        <input type="file" accept="image/*" style="display:none"
                               data-stock-upload="{part_id}">
                    </label>
                    <a class="text-blue-600 cursor-pointer hover:underline"
                       data-pistock-capture="{part_id}"
                       title="Prendre une photo">📷</a>
                </div>
            </div>
        ''')
    else:
        # Pas de photo : gros bouton pour fichier + petit lien camera
        ui.html(f'''
            <div class="flex flex-col items-center gap-1 flex-shrink-0">
                <label class="cursor-pointer" title="Ajouter une photo de la pièce en stock">
                    <div class="w-20 h-20 border-2 border-dashed border-stone-300 rounded-lg
                                flex flex-col items-center justify-center gap-0
                                text-stone-500 transition
                                hover:border-blue-500 hover:text-blue-500 hover:bg-blue-50">
                        <span class="text-2xl leading-none">📁</span>
                        <span class="text-xs mt-1">Fichier</span>
                    </div>
                    <input type="file" accept="image/*" style="display:none"
                           data-stock-upload="{part_id}">
                </label>
                <a class="text-xs text-blue-600 cursor-pointer hover:underline"
                   data-pistock-capture="{part_id}"
                   title="Prendre une photo avec la caméra">📷 Caméra</a>
            </div>
        ''')


# ======================================================================
#  PAGE : VIEWER 3D
# ======================================================================
def open_part_options_dialog(part: dict, on_change):
    """Dialogue d'options pour une piece donnee. Contient les actions
    moins frequentes que la simple modification (suppression, et plus
    tard renommage, duplication, etc.). Le verrou n'empeche PAS
    d'acceder a ce dialogue, mais empeche la suppression d'une piece
    verrouillee (le bouton est grise dans ce cas)."""
    with ui.dialog() as dialog, ui.card().classes("min-w-[440px]"):
        # En-tete : nom + code projet + statut
        ui.label("Options de la pièce") \
            .classes("text-base font-medium text-gray-600")
        with ui.row().classes("items-center gap-2"):
            ui.label(part["part_name"]) \
                .classes("text-lg font-bold")
            if part.get("version"):
                ui.label(part["version"]) \
                    .classes("text-xs font-mono text-gray-500")
        meta_bits = []
        if part.get("project_code"):
            meta_bits.append(f"projet {part['project_code']}")
        if part.get("status"):
            meta_bits.append(f"statut « {part['status']} »")
        if part.get("locked"):
            meta_bits.append("🔒 verrouillée")
        if meta_bits:
            ui.label(" • ".join(meta_bits)) \
                .classes("text-xs text-gray-500")

        ui.separator()

        # --- Section "Zone dangereuse" : suppression ----------------
        # On garde la suppression isolee visuellement (couleur rouge,
        # alignee a droite) pour eviter les clics accidentels.
        with ui.column().classes("w-full gap-2 mt-2"):
            ui.label("⚠️ Zone dangereuse") \
                .classes("text-sm font-medium text-red-600")
            ui.label("La suppression d'une pièce efface définitivement "
                     "ses révisions PLM, son stock et ses fichiers "
                     "associés. Action irréversible.") \
                .classes("text-xs text-gray-600")

            def on_delete():
                # Lance la confirmation. Si OK, l'autre dialog se chargera
                # de l'appel API + de la notification + du refresh.
                dialog.close()
                confirm_delete_part(part, on_change)

            ui.button("🗑 Supprimer définitivement cette pièce…",
                       on_click=on_delete) \
                .props("color=negative outline") \
                .classes("self-end")

        # --- Bouton fermer ------------------------------------------
        with ui.row().classes("w-full justify-end mt-2"):
            ui.button("Fermer", on_click=dialog.close).props("flat")

    dialog.open()


def confirm_delete_part(part: dict, on_change):
    # Garde admin : on ouvre le vrai dialogue seulement apres login.
    return _ensure_admin(lambda: _confirm_delete_part_inner(part, on_change))

def _confirm_delete_part_inner(part: dict, on_change):
    """Dialogue de confirmation finale pour la suppression d'une piece.
    Affiche le nom en gras et un avertissement. Au confirmation :
    appelle delete_part_db ; si refus pour cause de BOMs, affiche
    la liste exhaustive en notification dedans le dialog."""
    with ui.dialog() as dialog, ui.card().classes("min-w-[440px]"):
        ui.label("Confirmer la suppression") \
            .classes("text-lg font-bold")
        ui.label(f"Vous êtes sur le point de supprimer définitivement "
                  f"la pièce « {part['part_name']} ».") \
            .classes("text-sm")
        ui.label("Toutes ses révisions PLM, son stock et ses fichiers "
                 "associés seront effacés. Cette opération est "
                 "irréversible.") \
            .classes("text-sm text-gray-600")

        # Zone d'erreur qui sera remplie si la pièce est dans une BOM
        error_area = ui.column().classes("w-full gap-1")

        def do_delete():
            error_area.clear()
            ok, msg, blocking = delete_part_db(part["id"])
            if ok:
                ui.notify(msg, type="positive")
                dialog.close()
                on_change()
                return
            # Echec : si c'est a cause d'une BOM, on affiche la liste
            # directement dans le dialog (pas de toast pour pouvoir
            # lire posement).
            if blocking:
                with error_area:
                    with ui.card().classes(
                            "w-full bg-red-50 border-l-4 "
                            "border-red-400 p-3 mt-2"):
                        ui.label(msg).classes("text-sm font-medium "
                                                "text-red-700")
                        ui.label("BOMs concernées :") \
                            .classes("text-xs text-red-600 mt-1")
                        for b in blocking:
                            line = f"  • {b['code']}"
                            if b['description']:
                                line += f" — {b['description'][:40]}"
                            ui.label(line) \
                                .classes("text-xs font-mono "
                                          "text-red-600")
                        ui.label("Retirez la pièce de ces BOMs "
                                 "d'abord, puis réessayez.") \
                            .classes("text-xs text-gray-600 mt-1")
            else:
                ui.notify(msg, type="negative")

        with ui.row().classes("w-full justify-end gap-2 mt-3"):
            ui.button("Annuler", on_click=dialog.close).props("flat")
            ui.button("Supprimer définitivement",
                       on_click=do_delete) \
                .props("color=negative")

    dialog.open()


# ======================================================================
#  DIALOGUE : ASSIGNATION DE PROJET
# ======================================================================
def open_assign_project_dialog(part: dict, on_change):
    projects = fetch_projects()
    last_used_id = fetch_last_used_project_id()
    current_id = part["id_project"]
    part_id = part["id"]
    part_name = part["part_name"]

    # Construit le dialogue. On le ferme et le detruit apres usage
    # pour eviter d'accumuler des dialogues a chaque ouverture.
    with ui.dialog() as dialog, ui.card().classes("min-w-[440px] max-w-[600px]"):
        ui.label(f"Assigner un projet à « {part_name} »") \
            .classes("text-lg font-medium")

        list_container = ui.column() \
            .classes("w-full gap-2 max-h-[360px] overflow-y-auto")

        # Formulaire de creation de projet, masque par defaut
        with ui.column().classes("w-full gap-2 mt-2") as creation_form:
            ui.label("Nouveau projet").classes("text-sm font-medium")
            desc_input = ui.textarea(
                placeholder="Description (optionnelle)") \
                .classes("w-full").props("autogrow rows=2")
            err_label = ui.label("") \
                .classes("text-red-600 text-sm min-h-[1.2em]")
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Annuler",
                          on_click=lambda: hide_creation()) \
                    .props("flat")
                ui.button("Créer et assigner",
                          on_click=lambda: confirm_create_and_assign()) \
                    .props("color=primary")
        creation_form.set_visibility(False)

        # Pied : "+ Nouveau projet" / Dissocier / Fermer
        with ui.row().classes("w-full justify-between gap-2 mt-2"):
            add_btn = ui.button("+ Nouveau projet",
                                 on_click=lambda: show_creation()) \
                .props("color=primary outline")
            with ui.row().classes("gap-2"):
                if current_id is not None:
                    ui.button("Dissocier",
                              on_click=lambda: do_assign(None)) \
                        .props("flat color=negative")
                ui.button("Fermer", on_click=dialog.close).props("flat")

        def render_options():
            list_container.clear()
            if not projects:
                with list_container:
                    ui.label("Aucun projet pour l'instant. "
                             "Créez-en un avec « + Nouveau projet ».") \
                        .classes("text-gray-500 text-sm text-center p-4")
                return
            for proj in projects:
                with list_container:
                    is_current = (proj["id"] == current_id)
                    is_last = (proj["id"] == last_used_id and not is_current)
                    # Bordure speciale si projet courant ou dernier utilise
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
                                desc = proj["description"] or "(aucune description)"
                                ui.label(desc) \
                                    .classes("text-sm text-stone-700 "
                                              "whitespace-pre-wrap")
                                if is_current:
                                    ui.label("Projet actuel") \
                                        .classes("text-xs text-blue-600 font-medium")
                                elif is_last:
                                    ui.label("Dernier utilisé") \
                                        .classes("text-xs text-amber-600")
                    # Clic sur la carte = assigner
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
            # Cree le projet puis l'assigne immediatement a la piece
            ok, msg, code = create_project_in_db(desc_input.value or "")
            if not ok:
                err_label.text = msg
                return
            # Le projet vient d'etre cree : on retrouve son id en
            # cherchant par code (unique).
            import main
            with Session(main.engine) as s:
                proj = s.exec(
                    select(main.Project).where(main.Project.code == code)
                ).first()
                new_id = proj.id if proj else None
            if new_id is None:
                err_label.text = "Projet créé mais introuvable, abandon."
                return
            ok2, msg2 = assign_project_to_part(part_id, new_id)
            if ok2:
                ui.notify(f"Projet {code} créé et assigné.",
                          type="positive")
                dialog.close()
                on_change()
            else:
                ui.notify(msg2, type="negative")

        render_options()
        dialog.open()


# ======================================================================
#  DIALOGUE : EDITION DU STOCK D'UNE PIECE
# ======================================================================
# Ouvre un dialogue avec : quantite (number), location (input), supply
# (textarea), et un bouton d'upload de fiche composant. La fiche
# uploadee va dans /data-pistock/uploads/doc/ via l'endpoint REST
# /api/v1/parts/{id}/stock-doc (cf. JS listener "data-stock-doc").
def open_stock_dialog(part: dict, on_change):
    part_id = part["id"]
    part_name = part["part_name"]
    # Etat courant lu depuis la base (le 'part' passe peut etre stale
    # si le user a modifie le stock dans un autre onglet).
    stock = fetch_stock(part_id)

    with ui.dialog() as dialog, ui.card().classes("min-w-[480px] max-w-[600px]"):
        ui.label(f"Stock — « {part_name} »") \
            .classes("text-lg font-medium")

        # --- Champs editables -----------------------------------------
        qty_input = ui.number(label="Quantité",
                               value=stock["quantity"] or 0,
                               min=0, step=1, format="%d") \
            .classes("w-full")
        loc_input = ui.input(label="Location",
                              value=stock["location"] or "",
                              placeholder="ex: Tiroir A3, étagère 2") \
            .classes("w-full")
        supply_input = ui.textarea(
                label="Supply",
                value=stock["supply"] or "",
                placeholder="URL d'approvisionnement, fournisseur, "
                            "notes...") \
            .classes("w-full").props("autogrow rows=3")

        # --- Fiche composant -----------------------------------------
        # Si une fiche existe deja, on affiche un lien pour la
        # consulter. Le bouton "Choisir un fichier" ouvre le file
        # picker et l'upload se declenche automatiquement via le
        # listener JS global (data-stock-doc).
        with ui.column().classes("w-full mt-2"):
            ui.label("Fiche composant").classes("text-sm text-gray-600")
            doc_url = stock["doc_url"]
            if doc_url:
                # Lien vers la fiche actuelle (extrait juste le nom
                # affiche en retirant le repertoire et le prefixe).
                doc_name = doc_url.split("/")[-1]
                # On retire le suffixe _YYYYMMDD_HHMMSS pour l'affichage
                import re
                display_name = re.sub(r"_\d{8}_\d{6}", "", doc_name)
                with ui.row().classes("items-center gap-2"):
                    ui.html(
                        f'<a href="{doc_url}" target="_blank" '
                        f'class="text-blue-600 hover:underline text-sm">'
                        f'📄 {display_name}</a>'
                    )
                replace_label_text = "Remplacer la fiche"
            else:
                ui.label("(aucune fiche enregistrée)") \
                    .classes("text-sm text-gray-400 italic")
                replace_label_text = "Choisir un fichier"

            # Bouton d'upload : meme approche que pour les photos de
            # stock (HTML <label> + input cache, intercepte par le
            # listener JS global).
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

        # --- Boutons OK / Annuler ------------------------------------
        with ui.row().classes("w-full justify-end gap-2 mt-3"):
            ui.button("Annuler", on_click=dialog.close).props("flat")
            ui.button("Enregistrer",
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
#  DIALOGUE : LISTE DES BOMs (+ création + actions stock)
# ======================================================================
def open_boms_dialog():
    """Dialogue principal des BOMs : liste, création, et actions de
    stock (ajouter/retirer N fois). Cliquer sur une ligne ouvre le
    sous-dialogue d'édition des lignes de la BOM."""

    with ui.dialog() as dialog, ui.card().classes("min-w-[760px] max-w-[900px]"):
        ui.label("BOMs (nomenclatures)").classes("text-lg font-medium")

        list_container = ui.column() \
            .classes("w-full gap-2 max-h-[420px] overflow-y-auto")

        # --- Formulaire de création (masqué par défaut) ---------------
        with ui.column().classes("w-full gap-2 mt-2") as creation_form:
            ui.label("Nouvelle BOM").classes("text-sm font-medium")
            desc_input = ui.textarea(
                placeholder="Description (optionnelle)") \
                .classes("w-full").props("autogrow rows=2")
            # Sélecteur projet (optionnel) : permet de rattacher la
            # BOM à un projet existant directement à la création.
            project_select = ui.select(
                options={0: "(Sans projet)"},  # peuplé dans render()
                value=0, label="Projet (optionnel)"
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

        # --- Pied : "+ Nouvelle BOM" et "Fermer" ---------------------
        with ui.row().classes("w-full justify-between gap-2 mt-2"):
            add_btn = ui.button("+ Nouvelle BOM",
                                 on_click=lambda: show_creation()) \
                .props("color=primary outline")
            ui.button(_("Close"), on_click=dialog.close).props("flat")

        def show_creation():
            desc_input.value = ""
            project_select.value = 0
            err_label.text = ""
            # Recharge la liste des projets dans le selecteur
            options = {0: "(Sans projet)"}
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
                    ui.label("Aucune BOM. Cliquez sur « + Nouvelle BOM »"
                             " pour en créer une.") \
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
                    # Description + projet
                    with ui.column().classes("gap-0 flex-grow"):
                        desc = bom["description"] or "(sans description)"
                        ui.label(desc).classes("text-sm font-medium")
                        meta = f"{bom['line_count']} ligne(s)"
                        if bom["project_code"]:
                            meta += f" • projet {bom['project_code']}"
                        ui.label(meta).classes("text-xs text-gray-500")

                    # Bouton "Éditer"
                    def make_edit(bid=bom["id"]):
                        def handler():
                            dialog.close()
                            open_bom_edit_dialog(bid)
                        return handler
                    ui.button(icon="edit", on_click=make_edit()) \
                        .props("flat round dense color=primary") \
                        .tooltip("Éditer les lignes")

                    # Stock +/- (avec mini-prompt pour le facteur)
                    def make_stock_apply(bid=bom["id"],
                                          direction="add"):
                        def handler():
                            open_bom_stock_dialog(bid, direction,
                                                   on_done=render_boms_list)
                        return handler
                    ui.button(icon="add", on_click=make_stock_apply(
                                bid=bom["id"], direction="add")) \
                        .props("flat round dense color=positive") \
                        .tooltip("Ajouter au stock")
                    ui.button(icon="remove", on_click=make_stock_apply(
                                bid=bom["id"], direction="sub")) \
                        .props("flat round dense color=warning") \
                        .tooltip("Retirer du stock")

                    # Suppression (avec confirmation)
                    def make_delete(bid=bom["id"], code=bom["code"]):
                        def handler():
                            confirm_delete_bom(bid, code,
                                                on_done=render_boms_list)
                        return handler
                    ui.button(icon="delete", on_click=make_delete()) \
                        .props("flat round dense color=negative") \
                        .tooltip("Supprimer cette BOM")

        render_boms_list()
        dialog.open()


def confirm_delete_bom(bom_id: int, code: str, on_done):
    return _ensure_admin(lambda: _confirm_delete_bom_inner(bom_id, code, on_done))

def _confirm_delete_bom_inner(bom_id: int, code: str, on_done):
    """Dialogue de confirmation pour la suppression d'une BOM."""
    with ui.dialog() as d, ui.card():
        ui.label(f"Supprimer la BOM « {code} » ?") \
            .classes("text-base font-medium")
        ui.label("Cette action supprime aussi toutes ses lignes. "
                  "Le stock des pièces n'est PAS modifié.") \
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
    """Mini-dialogue qui demande le facteur (combien de fois appliquer
    la BOM) puis applique. direction='add' ou 'sub'."""
    is_add = (direction == "add")
    title = "Ajouter au stock" if is_add else "Retirer du stock"
    verb_color = "positive" if is_add else "warning"

    detail = fetch_bom_detail(bom_id)
    if detail is None:
        ui.notify("BOM introuvable.", type="negative")
        return
    if not detail["lines"]:
        ui.notify("Cette BOM est vide.", type="warning")
        return

    with ui.dialog() as d, ui.card().classes("min-w-[440px]"):
        ui.label(f"{title} — BOM {detail['code']}") \
            .classes("text-lg font-medium")
        ui.label("Combien de fois ?").classes("text-sm text-gray-600")
        factor_input = ui.number(value=1, min=1, step=1, format="%d") \
            .classes("w-full")
        # Récap des changements à venir : on affiche les TOTAUX par
        # piece feuille apres aplatissement de la hierarchie (recursion
        # via _flatten_bom). C'est ce qui sera vraiment applique au stock.
        ui.label("Conséquences sur le stock (pièces feuilles) :") \
            .classes("text-sm font-medium mt-2")
        recap = ui.column().classes("gap-1")
        def refresh_recap():
            recap.clear()
            f = int(factor_input.value or 1)
            sign = "+" if is_add else "−"
            # Calcul via le flatten serveur
            import main
            with Session(main.engine) as session:
                try:
                    totals = main._flatten_bom(session, bom_id, factor=f)
                    # Pre-charge les noms de pieces pour l'affichage
                    parts_by_id = {
                        p.id: p.part_name for p in session.exec(
                            select(main.Parts)
                            .where(main.Parts.id.in_(totals.keys()))
                        ).all()
                    } if totals else {}
                except Exception as e:
                    with recap:
                        ui.label(f"⚠️  Erreur : {e}") \
                            .classes("text-xs text-red-600")
                    return
            with recap:
                if not totals:
                    ui.label("(BOM vide)").classes("text-xs text-gray-500")
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
                    # Construit un message detaille des manques
                    lines = [f"  • {s['part_name']} : besoin {s['needed']}, "
                             f"dispo {s['available']} (manque {s['missing']})"
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
#  DIALOGUE : ÉDITION DES LIGNES D'UNE BOM
# ======================================================================
def open_bom_edit_dialog(bom_id: int):
    """Dialogue d'édition des lignes d'une BOM : ajouter, modifier
    quantité (inline), supprimer."""
    detail = fetch_bom_detail(bom_id)
    if detail is None:
        ui.notify("BOM introuvable.", type="negative")
        return

    # Charger toutes les pieces pour le selecteur d'ajout
    parts = fetch_parts_full()

    with ui.dialog() as dialog, ui.card().classes("min-w-[640px] max-w-[800px]"):
        # En-tête : code + description
        header_text = f"BOM {detail['code']}"
        if detail["description"]:
            header_text += f" — {detail['description']}"
        ui.label(header_text).classes("text-lg font-medium")

        # Liste des lignes
        lines_container = ui.column().classes("w-full gap-1")

        def render_lines():
            """Recharge les données et redessine les lignes."""
            nonlocal detail
            detail = fetch_bom_detail(bom_id)
            lines_container.clear()
            if not detail["lines"]:
                with lines_container:
                    ui.label("Aucune ligne. Ajoutez une pièce ci-dessous.") \
                        .classes("text-gray-500 text-sm text-center p-3")
                return
            for line in detail["lines"]:
                with lines_container:
                    render_line_row(line)

        def render_line_row(line):
            with ui.row().classes("w-full items-center gap-3 no-wrap "
                                    "border-b border-gray-200 py-2"):
                # Colonne nom : different selon le type
                if line["line_type"] == "part":
                    # Piece : nom simple
                    ui.label(line["part_name"]) \
                        .classes("text-sm flex-grow")
                else:
                    # Sous-BOM : pastille bleue cliquable + description
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
                                or "(sans description)")
                        ui.label(desc).classes(
                            "text-sm text-blue-700 hover:underline")

                # Quantité éditable (commune aux deux types)
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

                # Bouton suppression
                def make_del(lid=line["id"]):
                    def handler():
                        ok, msg = delete_bom_line_db(lid)
                        ui.notify(msg, type="positive" if ok else "negative")
                        if ok:
                            render_lines()
                    return handler
                ui.button(icon="delete", on_click=make_del()) \
                    .props("flat round dense color=negative")

        # --- Formulaire d'ajout en bas : toggle Pièce / Sous-BOM -----
        # Charge la liste des autres BOMs (toutes sauf la BOM courante,
        # car on ne peut pas s'auto-référencer)
        all_boms = fetch_boms()
        other_boms = [b for b in all_boms if b["id"] != bom_id]
        bom_options = {
            b["id"]: f"{b['code']} — {(b['description'] or '')[:30]}"
            for b in other_boms
        }
        part_options = {p["id"]: p["part_name"] for p in parts}

        with ui.column().classes("w-full gap-2 mt-3 "
                                   "border-t border-gray-200 pt-3"):
            # Toggle de type de ligne à ajouter
            line_type_toggle = ui.toggle(
                {"part": "Pièce", "subbom": "Sous-BOM"},
                value="part"
            ).props("dense")

            with ui.row().classes("w-full items-end gap-2"):
                # Sélecteur pièce (visible par défaut)
                part_select = ui.select(
                    options=part_options,
                    label="Pièce", with_input=True
                ).classes("flex-grow")
                # Sélecteur sous-BOM (masqué par défaut)
                subbom_select = ui.select(
                    options=bom_options,
                    label="Sous-BOM", with_input=True
                ).classes("flex-grow")
                subbom_select.set_visibility(False)

                qty_add = ui.number(label="Qté", value=1, min=1, step=1,
                                     format="%d").classes("w-24")

                def on_type_change():
                    is_part = line_type_toggle.value == "part"
                    part_select.set_visibility(is_part)
                    subbom_select.set_visibility(not is_part)
                    # Reset des valeurs pour éviter la confusion
                    part_select.value = None
                    subbom_select.value = None
                line_type_toggle.on_value_change(on_type_change)

                def add_line():
                    qty = int(qty_add.value or 1)
                    if line_type_toggle.value == "part":
                        pid = part_select.value
                        if pid is None:
                            ui.notify("Sélectionnez une pièce.", type="warning")
                            return
                        ok, msg = add_bom_line_db(bom_id, int(pid), qty)
                    else:
                        sid = subbom_select.value
                        if sid is None:
                            if not other_boms:
                                ui.notify("Aucune autre BOM disponible pour "
                                          "être ajoutée comme sous-BOM.",
                                          type="warning")
                            else:
                                ui.notify("Sélectionnez une sous-BOM.",
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
                ui.button("+ Ajouter", on_click=add_line) \
                    .props("color=primary")

        with ui.row().classes("w-full justify-end mt-3"):
            ui.button(_("Close"), on_click=dialog.close).props("flat")

        render_lines()
        dialog.open()


# ======================================================================
#  SYSTEME DE PLUGINS
# ======================================================================
# Architecture :
# - Un plugin est un dossier dans 'plugins/' contenant a minima :
#   - manifest.json : metadonnees (id, name, version, description, icon)
#   - plugin.py    : module Python avec une fonction register(app)
# - Au demarrage, on scanne plugins/* et on charge chaque plugin valide.
# - Un plugin enregistre ses propres routes/pages via @ui.page('/plugin/<id>').
# - Le noyau expose une page d'index /plugins qui liste les plugins
#   installes sous forme de cartes cliquables.
#
# Convention forte : un plugin lit librement la base mais n'ecrit que
# dans ses propres tables (prefixe 'plugin_<id>_*'). Le noyau garantit
# ses tables ; un plugin qui plante au chargement est log et ignore,
# le reste continue a tourner.
import json
import importlib.util as _importlib_util
from pathlib import Path

# Dossier 'plugins/' a la racine du projet (au meme niveau que
# frontend/ et backend/). Resolu depuis ce fichier pour etre agnostique
# du cwd.
def confirm_delete_project(project: dict, on_done):
    """Dialogue de confirmation pour supprimer un projet, derriere
    la garde admin. Au refus pour cause de non-vide, affiche la liste."""
    def really_delete():
        with ui.dialog() as dialog, ui.card().classes("min-w-[440px]"):
            ui.label("Confirmer la suppression du projet") \
                .classes("text-lg font-bold")
            ui.label(f"Supprimer définitivement le projet "
                      f"« {project['code']} » ?") \
                .classes("text-sm")
            ui.label("Un projet ne peut être supprimé que s\'il ne "
                      "contient plus aucune pièce et aucune BOM.") \
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
                                ui.label("Pièces rattachées :") \
                                    .classes("text-xs text-red-600 mt-1")
                                for p in blocking["parts"][:8]:
                                    ui.label(f"  • {p['part_name']}") \
                                        .classes("text-xs font-mono "
                                                  "text-red-600")
                                if len(blocking["parts"]) > 8:
                                    ui.label(f"  … et "
                                              f"{len(blocking['parts'])-8} "
                                              f"autres") \
                                        .classes("text-xs text-red-600")
                            if blocking["boms"]:
                                ui.label("BOMs rattachées :") \
                                    .classes("text-xs text-red-600 mt-1")
                                for b in blocking["boms"][:8]:
                                    ui.label(f"  • {b['code']}") \
                                        .classes("text-xs font-mono "
                                                  "text-red-600")
                else:
                    ui.notify(msg, type="negative")

            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button("Annuler", on_click=dialog.close).props("flat")
                ui.button("Supprimer", on_click=do_delete) \
                    .props("color=negative")
        dialog.open()
    _ensure_admin(really_delete)
