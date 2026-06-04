# PiStock plugin — Database admin (backup / restore / new / merge)
# Copyright (C) 2026 GA3Dtech — AGPLv3
#
# Admin-gated maintenance plugin. Features:
#   1. EXPORT  — copy the whole data-pistock folder (DB + uploads) to a
#                target folder (server-side path, e.g. an external disk
#                mounted on the Pi), into a timestamped subfolder.
#   2. IMPORT  — restore the database+files from another export. The
#                current data is backed up (timestamped) first. An OLDER
#                backup is accepted and AUTO-MIGRATED to the current
#                schema (missing tables/columns are added with their
#                defaults — additive only, nothing is ever dropped).
#   3. EMPTY   — back up the current data, then start over from a fresh,
#                empty database (current schema); uploads are reset too.
#   4. MERGE   — integrate another database into the current one (e.g.
#                re-sync parts you added while working on a USB key).
#                Policy (chosen by the maintainer):
#                  - parts matched by name; existing parts keep their
#                    stock, but missing PLM revisions are appended
#                    (deduped by timestamp);
#                  - new parts imported with their revisions/stock/files;
#                  - projects & BOMs imported with FRESH codes (their
#                    sequential codes collide across databases) and the
#                    part/sub-BOM links are remapped accordingly.
#
# IMPORTANT: changes NOTHING in the base app. It only reads/writes the
# database through the public `main` facade and the filesystem; it ships
# its own translations so it never touches the core locale files.
#
# All paths are SERVER-SIDE (the machine running PiStock). "External
# disk" means a disk mounted on that machine.

import os
import shutil
import sqlite3
import time
from datetime import datetime

# ----------------------------------------------------------------------
#  Expected schema (for the compatibility check). Mirrors model.py.
# ----------------------------------------------------------------------
EXPECTED_SCHEMA = {
    "parts": {"id", "part_name", "id_project", "status", "locked"},
    "plm": {"id", "id_parts", "path_2_cadfile", "path_2_thumbnail",
            "path_2_3dglb", "timestamp", "author", "version", "is_main"},
    "stock": {"id", "id_parts", "path_2_img", "quantity", "location",
              "supply", "path_2_doc"},
    "project": {"id", "code", "description"},
    "bom": {"id", "code", "description", "id_project"},
    "bom_line": {"id", "id_bom", "id_parts", "id_subbom", "quantity"},
}
DB_NAME = "pistockdatabase.sqlite3"
ADMIN_SESSION_SECONDS = 30 * 60

# ----------------------------------------------------------------------
#  Translations (en/fr/de), self-contained.
# ----------------------------------------------------------------------
T = {
    "en": {
        "title": "Database admin", "plugins": "Plugins", "catalog": "Catalog",
        "gate_login_title": "Admin authentication",
        "gate_login_hint": "Enter the admin password to access the database tools.",
        "gate_setup_title": "Create the admin password",
        "gate_setup_hint": "No admin account yet. Choose a password (min. 6 characters); it protects these maintenance tools.",
        "password": "Password", "confirm": "Confirm password",
        "login": "Log in", "create": "Create",
        "bad_pw": "Invalid password.", "pw_short": "Password must be at least 6 characters.",
        "pw_mismatch": "The two entries do not match.", "admin_ok": "Admin session started.",
        "export_title": "Export (backup)",
        "export_hint": "Copy the whole data-pistock (database + files) into a timestamped subfolder of the target folder.",
        "import_title": "Restore (import)",
        "import_hint": "Restore the database and files from another export. The current data is backed up first. An OLDER backup is accepted and auto-migrated to the current schema.",
        "empty_title": "New empty database",
        "empty_hint": "Back up the current data, then start over from an empty database (current schema). Uploaded files are reset (kept in the backup). The admin password is reset.",
        "run_empty": "Create empty database",
        "confirm_empty_t": "Confirm new empty database",
        "confirm_empty_b": "This BACKS UP the current data, then REPLACES it with an EMPTY database. Uploads and the admin password are reset. Continue?",
        "emptied": "Empty database created. Backup at: {path}. Reload recommended.",
        "imported_migrated": "Restored and migrated to the current schema ({n} field(s)/table(s) added). Backup at: {path}. Reload recommended.",
        "not_pistock": "This folder is not a PiStock database export.",
        "migrate_note": "⚠ Older schema detected — it will be migrated to the current version on restore.",
        "merge_title": "Merge",
        "merge_hint": "Integrate another database into the current one: new parts (with revisions/stock/files) are added, missing revisions are appended to existing parts, projects & BOMs are re-coded and relinked.",
        "dest_folder": "Target folder (server-side path)",
        "source_folder": "Source folder (a data-pistock export)",
        "run_export": "Export now", "run_import": "Import…", "run_merge": "Merge…",
        "wb_title": "FreeCAD workbench",
        "wb_hint": "Copy the ready-to-use PiStock workbench (already carrying this server's address and certificate) into the target folder, as a 'PiStock' folder. Then drop that folder into FreeCAD's Mod directory on the workstation.",
        "run_wb": "Copy workbench",
        "wb_no_src": "Workbench source folder not found in the repo.",
        "wb_done": "Workbench copied to: {path}. Drop the 'PiStock' folder into FreeCAD's Mod directory, then restart FreeCAD.",
        "wb_warn": "⚠ {files} missing — set the server address first (deployment or dev_set_location.sh).",
        "no_dest": "Target folder does not exist.",
        "no_source": "Source folder has no {db}.".format(db=DB_NAME),
        "incompatible": "Incompatible database — operation cancelled.",
        "exported": "Exported to: {path}",
        "imported": "Import done. Backup saved at: {path}. Reload recommended.",
        "merged": "Merge done.",
        "confirm_import_t": "Confirm import (replace)",
        "confirm_import_b": "This REPLACES the current database and files. A backup is made first. Continue?",
        "confirm_merge_t": "Confirm merge",
        "confirm_merge_b": "This adds the source's parts/projects/BOMs into the CURRENT database. Continue?",
        "cancel": "Cancel", "confirm_btn": "Confirm",
        "compat_ok": "Compatible database.", "check": "Check compatibility",
        "missing_tables": "Missing tables", "missing_cols": "Missing columns",
        "rep_parts": "parts added", "rep_revs": "revisions added",
        "rep_stock": "stock rows added", "rep_proj": "projects added",
        "rep_boms": "BOMs added", "rep_lines": "BOM lines added",
        "rep_files": "files copied", "logout": "Log out admin",
        "browse": "Browse…", "pick_title": "Choose a folder",
        "select_folder": "Select this folder", "parent": "Parent folder",
        "home": "Home", "no_subdirs": "(no subfolders)",
    },
    "fr": {
        "title": "Admin base de données", "plugins": "Plugins", "catalog": "Catalogue",
        "gate_login_title": "Authentification admin",
        "gate_login_hint": "Saisissez le mot de passe admin pour accéder aux outils base de données.",
        "gate_setup_title": "Créer le mot de passe admin",
        "gate_setup_hint": "Aucun compte admin. Choisissez un mot de passe (min. 6 caractères) ; il protège ces outils de maintenance.",
        "password": "Mot de passe", "confirm": "Confirmer le mot de passe",
        "login": "Se connecter", "create": "Créer",
        "bad_pw": "Mot de passe invalide.", "pw_short": "Le mot de passe doit faire au moins 6 caractères.",
        "pw_mismatch": "Les deux saisies ne correspondent pas.", "admin_ok": "Session admin démarrée.",
        "export_title": "Exporter (sauvegarde)",
        "export_hint": "Copie toute la data-pistock (base + fichiers) dans un sous-dossier horodaté du dossier cible.",
        "import_title": "Restaurer (importer)",
        "import_hint": "Restaure la base et les fichiers depuis un autre export. Les données actuelles sont sauvegardées avant. Une ancienne sauvegarde est acceptée et migrée automatiquement vers le schéma courant.",
        "empty_title": "Nouvelle base vide",
        "empty_hint": "Sauvegarde les données actuelles, puis repart d'une base vide (schéma courant). Les fichiers envoyés sont réinitialisés (conservés dans la sauvegarde). Le mot de passe admin est réinitialisé.",
        "run_empty": "Créer une base vide",
        "confirm_empty_t": "Confirmer la nouvelle base vide",
        "confirm_empty_b": "Ceci SAUVEGARDE les données actuelles, puis les REMPLACE par une base VIDE. Les fichiers et le mot de passe admin sont réinitialisés. Continuer ?",
        "emptied": "Base vide créée. Sauvegarde dans : {path}. Rechargement recommandé.",
        "imported_migrated": "Restauré et migré vers le schéma courant ({n} champ(s)/table(s) ajouté(s)). Sauvegarde : {path}. Rechargement recommandé.",
        "not_pistock": "Ce dossier n'est pas un export de base PiStock.",
        "migrate_note": "⚠ Ancien schéma détecté — il sera migré vers la version courante à la restauration.",
        "merge_title": "Fusionner",
        "merge_hint": "Intègre une autre base dans la base actuelle : les nouvelles pièces (avec révisions/stock/fichiers) sont ajoutées, les révisions manquantes sont complétées sur les pièces existantes, projets & BOMs sont ré-codés et reliés.",
        "dest_folder": "Dossier cible (chemin côté serveur)",
        "source_folder": "Dossier source (un export data-pistock)",
        "run_export": "Exporter", "run_import": "Importer…", "run_merge": "Fusionner…",
        "wb_title": "Workbench FreeCAD",
        "wb_hint": "Copie le workbench PiStock prêt à l'emploi (déjà porteur de l'adresse et du certificat de ce serveur) dans le dossier cible, sous la forme d'un dossier « PiStock ». Déposez ensuite ce dossier dans le répertoire Mod de FreeCAD sur le poste.",
        "run_wb": "Copier le workbench",
        "wb_no_src": "Dossier source du workbench introuvable dans le dépôt.",
        "wb_done": "Workbench copié vers : {path}. Déposez le dossier « PiStock » dans le répertoire Mod de FreeCAD, puis redémarrez FreeCAD.",
        "wb_warn": "⚠ {files} manquant(s) — définissez d'abord l'adresse du serveur (déploiement ou dev_set_location.sh).",
        "no_dest": "Le dossier cible n'existe pas.",
        "no_source": "Le dossier source ne contient pas {db}.".format(db=DB_NAME),
        "incompatible": "Base incompatible — opération annulée.",
        "exported": "Exporté vers : {path}",
        "imported": "Import terminé. Sauvegarde dans : {path}. Rechargement recommandé.",
        "merged": "Fusion terminée.",
        "confirm_import_t": "Confirmer l'import (remplacement)",
        "confirm_import_b": "Ceci REMPLACE la base et les fichiers actuels. Une sauvegarde est faite avant. Continuer ?",
        "confirm_merge_t": "Confirmer la fusion",
        "confirm_merge_b": "Ceci ajoute les pièces/projets/BOMs de la source dans la base ACTUELLE. Continuer ?",
        "cancel": "Annuler", "confirm_btn": "Confirmer",
        "compat_ok": "Base compatible.", "check": "Vérifier la compatibilité",
        "missing_tables": "Tables manquantes", "missing_cols": "Colonnes manquantes",
        "rep_parts": "pièces ajoutées", "rep_revs": "révisions ajoutées",
        "rep_stock": "lignes de stock ajoutées", "rep_proj": "projets ajoutés",
        "rep_boms": "BOMs ajoutées", "rep_lines": "lignes BOM ajoutées",
        "rep_files": "fichiers copiés", "logout": "Déconnecter l'admin",
        "browse": "Parcourir…", "pick_title": "Choisir un dossier",
        "select_folder": "Choisir ce dossier", "parent": "Dossier parent",
        "home": "Accueil", "no_subdirs": "(aucun sous-dossier)",
    },
    "de": {
        "title": "Datenbank-Admin", "plugins": "Plugins", "catalog": "Katalog",
        "gate_login_title": "Admin-Authentifizierung",
        "gate_login_hint": "Geben Sie das Admin-Passwort ein, um die Datenbank-Tools zu öffnen.",
        "gate_setup_title": "Admin-Passwort erstellen",
        "gate_setup_hint": "Noch kein Admin-Konto. Wählen Sie ein Passwort (mind. 6 Zeichen); es schützt diese Wartungs-Tools.",
        "password": "Passwort", "confirm": "Passwort bestätigen",
        "login": "Anmelden", "create": "Erstellen",
        "bad_pw": "Ungültiges Passwort.", "pw_short": "Das Passwort muss mindestens 6 Zeichen lang sein.",
        "pw_mismatch": "Die beiden Eingaben stimmen nicht überein.", "admin_ok": "Admin-Sitzung gestartet.",
        "export_title": "Exportieren (Sicherung)",
        "export_hint": "Kopiert die gesamte data-pistock (DB + Dateien) in einen zeitgestempelten Unterordner des Zielordners.",
        "import_title": "Wiederherstellen (importieren)",
        "import_hint": "Stellt DB und Dateien aus einem anderen Export wieder her. Die aktuellen Daten werden zuvor gesichert. Eine ÄLTERE Sicherung wird akzeptiert und automatisch auf das aktuelle Schema migriert.",
        "empty_title": "Neue leere Datenbank",
        "empty_hint": "Sichert die aktuellen Daten und beginnt dann mit einer leeren Datenbank (aktuelles Schema). Hochgeladene Dateien werden zurückgesetzt (in der Sicherung erhalten). Das Admin-Passwort wird zurückgesetzt.",
        "run_empty": "Leere Datenbank erstellen",
        "confirm_empty_t": "Neue leere Datenbank bestätigen",
        "confirm_empty_b": "Dies SICHERT die aktuellen Daten und ERSETZT sie durch eine LEERE Datenbank. Dateien und Admin-Passwort werden zurückgesetzt. Fortfahren?",
        "emptied": "Leere Datenbank erstellt. Sicherung: {path}. Neuladen empfohlen.",
        "imported_migrated": "Wiederhergestellt und auf das aktuelle Schema migriert ({n} Feld(er)/Tabelle(n) hinzugefügt). Sicherung: {path}. Neuladen empfohlen.",
        "not_pistock": "Dieser Ordner ist kein PiStock-Datenbankexport.",
        "migrate_note": "⚠ Älteres Schema erkannt — es wird bei der Wiederherstellung auf die aktuelle Version migriert.",
        "merge_title": "Zusammenführen",
        "merge_hint": "Führt eine andere DB in die aktuelle ein: neue Teile (mit Revisionen/Bestand/Dateien) werden hinzugefügt, fehlende Revisionen ergänzt, Projekte & Stücklisten neu codiert und verknüpft.",
        "dest_folder": "Zielordner (serverseitiger Pfad)",
        "source_folder": "Quellordner (ein data-pistock-Export)",
        "run_export": "Exportieren", "run_import": "Importieren…", "run_merge": "Zusammenführen…",
        "wb_title": "FreeCAD-Workbench",
        "wb_hint": "Kopiert die einsatzbereite PiStock-Workbench (mit Adresse und Zertifikat dieses Servers) in den Zielordner als Ordner 'PiStock'. Diesen Ordner dann in das Mod-Verzeichnis von FreeCAD auf der Arbeitsstation legen.",
        "run_wb": "Workbench kopieren",
        "wb_no_src": "Workbench-Quellordner im Repo nicht gefunden.",
        "wb_done": "Workbench kopiert nach: {path}. Den Ordner 'PiStock' in das Mod-Verzeichnis von FreeCAD legen und FreeCAD neu starten.",
        "wb_warn": "⚠ {files} fehlt/fehlen — zuerst die Serveradresse setzen (Deployment oder dev_set_location.sh).",
        "no_dest": "Der Zielordner existiert nicht.",
        "no_source": "Der Quellordner enthält keine {db}.".format(db=DB_NAME),
        "incompatible": "Inkompatible Datenbank — Vorgang abgebrochen.",
        "exported": "Exportiert nach: {path}",
        "imported": "Import fertig. Sicherung unter: {path}. Neuladen empfohlen.",
        "merged": "Zusammenführung fertig.",
        "confirm_import_t": "Import bestätigen (ersetzen)",
        "confirm_import_b": "Dies ERSETZT die aktuelle DB und Dateien. Vorher wird gesichert. Fortfahren?",
        "confirm_merge_t": "Zusammenführen bestätigen",
        "confirm_merge_b": "Dies fügt Teile/Projekte/Stücklisten der Quelle in die AKTUELLE DB ein. Fortfahren?",
        "cancel": "Abbrechen", "confirm_btn": "Bestätigen",
        "compat_ok": "Kompatible Datenbank.", "check": "Kompatibilität prüfen",
        "missing_tables": "Fehlende Tabellen", "missing_cols": "Fehlende Spalten",
        "rep_parts": "Teile hinzugefügt", "rep_revs": "Revisionen hinzugefügt",
        "rep_stock": "Bestandszeilen hinzugefügt", "rep_proj": "Projekte hinzugefügt",
        "rep_boms": "Stücklisten hinzugefügt", "rep_lines": "Stücklistenzeilen hinzugefügt",
        "rep_files": "Dateien kopiert", "logout": "Admin abmelden",
        "browse": "Durchsuchen…", "pick_title": "Ordner wählen",
        "select_folder": "Diesen Ordner wählen", "parent": "Übergeordneter Ordner",
        "home": "Start", "no_subdirs": "(keine Unterordner)",
    },
}


def _tr(key, **kw):
    try:
        from i18n import get_lang
        lang = get_lang()
    except Exception:
        lang = "en"
    text = T.get(lang, T["en"]).get(key, T["en"].get(key, key))
    return text.format(**kw) if kw else text


# ======================================================================
#  ADMIN GATE (mirrors the core, via the main facade; shares the same
#  app.storage.user["admin_until"] session as the main app).
# ======================================================================
def _admin_configured():
    import main
    from sqlmodel import Session, select
    with Session(main.engine) as s:
        return s.exec(select(main.Admin)).first() is not None


def _session_active():
    from nicegui import app
    try:
        until = float(app.storage.user.get("admin_until", 0) or 0)
    except (TypeError, ValueError):
        until = 0
    return time.time() < until


def _mark_session():
    from nicegui import app
    app.storage.user["admin_until"] = time.time() + ADMIN_SESSION_SECONDS


def _clear_session():
    from nicegui import app
    app.storage.user.pop("admin_until", None)


def _verify_password(password):
    import main
    from sqlmodel import Session, select
    if not password:
        return False
    with Session(main.engine) as s:
        rec = s.exec(select(main.Admin)).first()
        if rec is None:
            return False
        return main._verify_password(password, rec.salt, rec.password_hash)


def _create_admin(password):
    import main
    from sqlmodel import Session, select
    if len(password) < 6:
        return False, _tr("pw_short")
    with Session(main.engine) as s:
        if s.exec(select(main.Admin)).first() is not None:
            return False, _tr("bad_pw")
        salt = main._new_salt()
        s.add(main.Admin(salt=salt.hex(),
                         password_hash=main._hash_password(password, salt)))
        s.commit()
    return True, _tr("admin_ok")


# ======================================================================
#  FILESYSTEM / DATABASE OPERATIONS (testable: explicit targets)
# ======================================================================
def _compat_check(db_path):
    """Return (ok, info_dict). Verifies the expected tables/columns."""
    if not os.path.isfile(db_path):
        return False, {"error": "missing_db"}
    con = sqlite3.connect(db_path)
    try:
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        missing_tables = sorted(t for t in EXPECTED_SCHEMA if t not in names)
        missing_cols = {}
        for t, cols in EXPECTED_SCHEMA.items():
            if t not in names:
                continue
            actual = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
            miss = cols - actual
            if miss:
                missing_cols[t] = sorted(miss)
        ok = not missing_tables and not missing_cols
        return ok, {"missing_tables": missing_tables,
                    "missing_columns": missing_cols,
                    "tables": sorted(names)}
    finally:
        con.close()


def _copy_file(src_data, dst_data, relpath):
    """Copy one upload file (relative path) from src to dst data dir.
    Skips if missing at source or already present at destination."""
    if not relpath:
        return False
    src = os.path.join(src_data, relpath)
    dst = os.path.join(dst_data, relpath)
    if not os.path.isfile(src) or os.path.isfile(dst):
        return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    return True


def _stamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _unique(path):
    """Return 'path' or, if it already exists, 'path-2', 'path-3'… so two
    operations within the same second never collide."""
    if not os.path.exists(path):
        return path
    i = 2
    while os.path.exists(f"{path}-{i}"):
        i += 1
    return f"{path}-{i}"


def export_data(data_dir, dest_root):
    """Copy data_dir into dest_root/pistock-export-<ts>. Returns (ok, msg)."""
    if not dest_root or not os.path.isdir(dest_root):
        return False, _tr("no_dest")
    dest = _unique(os.path.join(dest_root, f"pistock-export-{_stamp()}"))
    shutil.copytree(data_dir, dest)
    return True, _tr("exported", path=dest)


def _workbench_dir():
    """Absolute path to the FreeCAD workbench shipped in the repo (the
    folder dropped into FreeCAD's Mod directory). Resolved from this
    plugin file: plugins/db_admin/plugin.py -> repo root."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    return os.path.join(root, "backend", "CAD-extensions", "pistock-freecad")


def export_workbench(dest_root):
    """Copy the ready-to-use FreeCAD workbench into dest_root/PiStock, so
    it can be dropped straight into FreeCAD's Mod directory on a
    workstation. The repo copy already carries this server's address
    (pistock_host.txt) and certificate (pistock_ca.pem), injected at
    deployment. Returns (ok, msg)."""
    if not dest_root or not os.path.isdir(dest_root):
        return False, _tr("no_dest")
    src = _workbench_dir()
    if not os.path.isdir(src):
        return False, _tr("wb_no_src")
    dest = os.path.join(dest_root, "PiStock")
    shutil.copytree(src, dest, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # Non-blocking warning if the server address / certificate are absent
    # (e.g. workbench never configured by deploy/dev_set_location.sh).
    wb = os.path.join(src, "freecad", "pistock_workbench")
    missing = [f for f in ("pistock_host.txt", "pistock_ca.pem")
               if not os.path.isfile(os.path.join(wb, f))]
    msg = _tr("wb_done", path=dest)
    if missing:
        msg += " " + _tr("wb_warn", files=", ".join(missing))
    return True, msg


def _is_pistock_db(info):
    """True if the source looks like a PiStock database (at least one of
    the expected tables is present)."""
    present = set(EXPECTED_SCHEMA) - set(info.get("missing_tables", []))
    return bool(present)


def _add_column_sql(table, col, dialect):
    """Build a safe 'ALTER TABLE ADD COLUMN' for a missing column. We emit
    only the type (never NOT NULL) so SQLite adds it as nullable on
    existing rows; a scalar default, if any, backfills those rows."""
    typ = col.type.compile(dialect=dialect)
    sql = f'ALTER TABLE "{table}" ADD COLUMN "{col.name}" {typ}'
    d = col.default
    if d is not None and getattr(d, "is_scalar", False):
        val = d.arg
        if isinstance(val, bool):
            lit = "1" if val else "0"
        elif isinstance(val, (int, float)):
            lit = str(val)
        else:
            lit = "'" + str(val).replace("'", "''") + "'"
        sql += f" DEFAULT {lit}"
    return sql


def _migrate_db(db_path):
    """Bring an (older) PiStock database up to the CURRENT schema, in
    place. Additive only: create missing tables (including loaded plugin
    tables) and add missing columns with their default. Never drops
    anything. Returns the list of applied column additions."""
    import main  # noqa: F401 — ensures models + plugin tables are imported
    from sqlmodel import SQLModel, create_engine
    eng = create_engine(f"sqlite:///{db_path}")
    applied = []
    try:
        # 1. Missing tables (additive, leaves existing tables untouched).
        SQLModel.metadata.create_all(eng)
        # 2. Missing columns on tables that already existed.
        raw = eng.raw_connection()
        try:
            cur = raw.cursor()
            for tname, table in SQLModel.metadata.tables.items():
                cur.execute(f'PRAGMA table_info("{tname}")')
                existing = {r[1] for r in cur.fetchall()}
                if not existing:
                    continue  # just created by create_all -> already complete
                for col in table.columns:
                    if col.name not in existing:
                        cur.execute(_add_column_sql(tname, col, eng.dialect))
                        applied.append(f"{tname}.{col.name}")
            raw.commit()
        finally:
            raw.close()
    finally:
        eng.dispose()
    return applied


def import_data(data_dir, source_dir):
    """Backup data_dir, then RESTORE its DB + uploads from source_dir. An
    older backup is accepted and auto-migrated to the current schema.
    Returns (ok, msg)."""
    import main
    src_db = os.path.join(source_dir, DB_NAME)
    if not os.path.isfile(src_db):
        return False, _tr("no_source")
    _ok, info = _compat_check(src_db)
    if not _is_pistock_db(info):
        return False, _tr("not_pistock")
    backup = _unique(os.path.join(os.path.dirname(os.path.abspath(data_dir)),
                                  f"pistock-backup-{_stamp()}"))
    shutil.copytree(data_dir, backup)
    main.engine.dispose()  # release the current DB file before replacing it
    shutil.copy2(src_db, os.path.join(data_dir, DB_NAME))
    src_up = os.path.join(source_dir, "uploads")
    if os.path.isdir(src_up):
        shutil.copytree(src_up, os.path.join(data_dir, "uploads"),
                        dirs_exist_ok=True)
    applied = _migrate_db(os.path.join(data_dir, DB_NAME))
    main.engine.dispose()  # reconnect to the freshly restored DB
    if applied:
        return True, _tr("imported_migrated", path=backup, n=len(applied))
    return True, _tr("imported", path=backup)


def recreate_empty(data_dir):
    """Back up data_dir, then reset to a brand-new empty database (current
    schema). Uploaded files are reset too (preserved in the backup).
    Returns (ok, msg)."""
    import main
    from sqlmodel import SQLModel
    backup = _unique(os.path.join(os.path.dirname(os.path.abspath(data_dir)),
                                  f"pistock-backup-{_stamp()}"))
    shutil.copytree(data_dir, backup)
    db = os.path.join(data_dir, DB_NAME)
    main.engine.dispose()
    if os.path.isfile(db):
        os.remove(db)
    SQLModel.metadata.create_all(main.engine)  # new file + current schema
    up = os.path.join(data_dir, "uploads")
    if os.path.isdir(up):
        shutil.rmtree(up)
    os.makedirs(up, exist_ok=True)
    main.engine.dispose()
    return True, _tr("emptied", path=backup)


def _read_source(source_db):
    """Read all rows from a source database into plain dicts (detached
    from any session). Uses the shared model classes from main."""
    import main
    from sqlmodel import create_engine, Session, select
    eng = create_engine(f"sqlite:///{source_db}")
    try:
        with Session(eng) as s:
            data = {
                "projects": [dict(id=p.id, code=p.code,
                                  description=p.description)
                             for p in s.exec(select(main.Project)).all()],
                "parts": [dict(id=p.id, part_name=p.part_name,
                               id_project=p.id_project, status=p.status,
                               locked=p.locked)
                          for p in s.exec(select(main.Parts)).all()],
                "plm": [dict(id=r.id, id_parts=r.id_parts,
                             cad=r.path_2_cadfile, thumb=r.path_2_thumbnail,
                             glb=r.path_2_3dglb, timestamp=r.timestamp,
                             author=r.author, version=r.version,
                             is_main=r.is_main)
                        for r in s.exec(select(main.PLM)).all()],
                "stock": [dict(id=r.id, id_parts=r.id_parts,
                               img=r.path_2_img, quantity=r.quantity,
                               location=r.location, supply=r.supply,
                               doc=r.path_2_doc)
                          for r in s.exec(select(main.Stock)).all()],
                "boms": [dict(id=b.id, code=b.code, description=b.description,
                              id_project=b.id_project)
                         for b in s.exec(select(main.Bom)).all()],
                "bom_lines": [dict(id=l.id, id_bom=l.id_bom,
                                   id_parts=l.id_parts, id_subbom=l.id_subbom,
                                   quantity=l.quantity)
                              for l in s.exec(select(main.BomLine)).all()],
            }
        return data
    finally:
        eng.dispose()


def merge_into(target_engine, target_data_dir, source_dir):
    """Merge the source database (source_dir/<DB_NAME> + source_dir/uploads)
    into the target. Returns a report dict. Policy: see module header."""
    import main
    from sqlmodel import Session, select

    source_db = os.path.join(source_dir, DB_NAME)
    source_data = source_dir  # files are at <source_dir>/uploads/...
    src = _read_source(source_db)

    rep = {"rep_parts": 0, "rep_revs": 0, "rep_stock": 0, "rep_proj": 0,
           "rep_boms": 0, "rep_lines": 0, "rep_files": 0}

    proj_map = {}   # source project id -> target project id (lazy)
    part_map = {}   # source part id -> target part id (existing or new)

    src_proj_by_id = {p["id"]: p for p in src["projects"]}
    plm_by_part = {}
    for r in src["plm"]:
        plm_by_part.setdefault(r["id_parts"], []).append(r)
    stock_by_part = {r["id_parts"]: r for r in src["stock"]}

    with Session(target_engine) as ts:
        existing = {p.part_name: p
                    for p in ts.exec(select(main.Parts)).all()}

        def target_project(src_pid):
            if src_pid is None:
                return None
            if src_pid in proj_map:
                return proj_map[src_pid]
            sp = src_proj_by_id.get(src_pid)
            if sp is None:
                return None
            np = main.Project(code=main._next_project_code(ts),
                              description=sp["description"])
            ts.add(np); ts.flush()
            proj_map[src_pid] = np.id
            rep["rep_proj"] += 1
            return np.id

        def copy_files(*relpaths):
            for rp in relpaths:
                if _copy_file(source_data, target_data_dir, rp):
                    rep["rep_files"] += 1

        # --- Parts (+ revisions + stock) ---
        for sp in src["parts"]:
            srevs = sorted(plm_by_part.get(sp["id"], []),
                           key=lambda r: (r["timestamp"] or ""))
            if sp["part_name"] in existing:
                tp = existing[sp["part_name"]]
                part_map[sp["id"]] = tp.id
                # Append revisions missing in target (dedup by timestamp)
                have_ts = {r.timestamp for r in ts.exec(
                    select(main.PLM).where(main.PLM.id_parts == tp.id)).all()}
                for r in srevs:
                    if r["timestamp"] in have_ts:
                        continue
                    copy_files(r["cad"], r["thumb"], r["glb"])
                    ts.add(main.PLM(
                        id_parts=tp.id, path_2_cadfile=r["cad"],
                        path_2_thumbnail=r["thumb"], path_2_3dglb=r["glb"],
                        timestamp=r["timestamp"], author=r["author"],
                        version=main._next_version_for_part(ts, tp.id),
                        is_main=False))
                    rep["rep_revs"] += 1
            else:
                np = main.Parts(part_name=sp["part_name"], status=sp["status"],
                                locked=sp["locked"],
                                id_project=target_project(sp["id_project"]))
                ts.add(np); ts.flush()
                part_map[sp["id"]] = np.id
                rep["rep_parts"] += 1
                for r in srevs:
                    copy_files(r["cad"], r["thumb"], r["glb"])
                    ts.add(main.PLM(
                        id_parts=np.id, path_2_cadfile=r["cad"],
                        path_2_thumbnail=r["thumb"], path_2_3dglb=r["glb"],
                        timestamp=r["timestamp"], author=r["author"],
                        version=main._next_version_for_part(ts, np.id),
                        is_main=bool(r["is_main"])))
                    rep["rep_revs"] += 1
                st = stock_by_part.get(sp["id"])
                if st:
                    copy_files(st["img"], st["doc"])
                    ts.add(main.Stock(
                        id_parts=np.id, path_2_img=st["img"],
                        quantity=st["quantity"], location=st["location"],
                        supply=st["supply"], path_2_doc=st["doc"]))
                    rep["rep_stock"] += 1
        ts.commit()

        # --- BOMs (fresh codes) + lines (remapped) ---
        bom_map = {}
        for b in src["boms"]:
            nb = main.Bom(code=main._next_bom_code(ts),
                          description=b["description"],
                          id_project=target_project(b["id_project"]))
            ts.add(nb); ts.flush()
            bom_map[b["id"]] = nb.id
            rep["rep_boms"] += 1
        for l in src["bom_lines"]:
            tgt_bom = bom_map.get(l["id_bom"])
            if tgt_bom is None:
                continue
            tgt_part = (part_map.get(l["id_parts"])
                        if l["id_parts"] is not None else None)
            tgt_sub = (bom_map.get(l["id_subbom"])
                       if l["id_subbom"] is not None else None)
            if tgt_part is None and tgt_sub is None:
                continue
            ts.add(main.BomLine(id_bom=tgt_bom, id_parts=tgt_part,
                                id_subbom=tgt_sub, quantity=l["quantity"]))
            rep["rep_lines"] += 1
        ts.commit()

    return rep


# ======================================================================
#  SERVER-SIDE FOLDER PICKER (graphical directory browser)
# ======================================================================
def _list_dirs(path):
    """Sorted list of visible subdirectory names of 'path' (robust to
    permission / not-a-dir errors)."""
    out = []
    try:
        for e in os.scandir(path):
            try:
                if e.is_dir(follow_symlinks=True) and not e.name.startswith("."):
                    out.append(e.name)
            except OSError:
                continue
    except (PermissionError, FileNotFoundError, NotADirectoryError):
        pass
    return sorted(out, key=str.lower)


def _open_folder_picker(target_input):
    """Open a dialog to browse the SERVER's filesystem and pick a folder.
    On confirm, writes the chosen absolute path into target_input."""
    from nicegui import ui

    start = (target_input.value or "").strip()
    if not os.path.isdir(start):
        start = os.path.expanduser("~")
        if not os.path.isdir(start):
            start = "/"
    state = {"path": os.path.abspath(start)}

    with ui.dialog() as dlg, ui.card().classes("min-w-[520px] max-w-[640px]"):
        ui.label(_tr("pick_title")).classes("text-lg font-bold")

        # Quick links (handy for external disks mounted under /media, /mnt)
        with ui.row().classes("w-full gap-1 flex-wrap"):
            for label, p in [("🏠 " + _tr("home"), os.path.expanduser("~")),
                             ("/", "/"), ("/media", "/media"), ("/mnt", "/mnt")]:
                if os.path.isdir(p):
                    ui.button(label, on_click=lambda pp=p: _go(pp)) \
                        .props("flat dense").classes("text-xs")

        path_label = ui.label("").classes(
            "text-xs font-mono text-gray-600 break-all")
        listing = ui.column().classes(
            "w-full gap-0 max-h-[50vh] overflow-auto "
            "border border-gray-200 rounded p-1")

        def _render():
            path_label.text = state["path"]
            listing.clear()
            with listing:
                parent = os.path.dirname(state["path"].rstrip("/")) or "/"
                if parent != state["path"]:
                    with ui.row().classes(
                            "items-center gap-2 cursor-pointer w-full p-1 "
                            "rounded hover:bg-gray-100").on(
                            "click", lambda pp=parent: _go(pp)):
                        ui.icon("drive_folder_upload").classes("text-gray-500")
                        ui.label(_tr("parent")).classes("text-sm")
                dirs = _list_dirs(state["path"])
                if not dirs:
                    ui.label(_tr("no_subdirs")) \
                        .classes("text-sm text-gray-400 italic p-1")
                for d in dirs:
                    full = os.path.join(state["path"], d)
                    has_db = os.path.isfile(os.path.join(full, DB_NAME))
                    with ui.row().classes(
                            "items-center gap-2 cursor-pointer w-full p-1 "
                            "rounded hover:bg-blue-50 no-wrap").on(
                            "click", lambda f=full: _go(f)):
                        ui.icon("folder").classes("text-amber-500")
                        ui.label(d).classes("text-sm truncate")
                        if has_db:
                            ui.label("🗄️").classes("text-xs") \
                                .tooltip(DB_NAME)

        def _go(p):
            if os.path.isdir(p):
                state["path"] = os.path.abspath(p)
                _render()

        _render()
        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button(_tr("cancel"), on_click=dlg.close).props("flat")

            def _choose():
                target_input.value = state["path"]
                target_input.update()
                dlg.close()
            ui.button(_tr("select_folder"), on_click=_choose) \
                .props("color=primary")
    dlg.open()


# ======================================================================
#  PAGE
# ======================================================================
def register(app):
    from nicegui import ui

    @ui.page("/plugin/db_admin")
    def db_admin_page():
        with ui.header().classes("bg-stone-800 text-white shadow"):
            with ui.row().classes("w-full items-center gap-3"):
                ui.label("🗄️ " + _tr("title")).classes("text-xl font-medium")
                ui.element("div").classes("flex-grow")
                if _session_active():
                    ui.button(_tr("logout"), on_click=lambda: (
                        _clear_session(), ui.navigate.reload())) \
                        .props("flat color=white").classes("text-sm")
                ui.button("← " + _tr("plugins"),
                          on_click=lambda: ui.navigate.to("/plugins")) \
                    .props("flat color=white").classes("text-sm")
                ui.button("🏠 " + _tr("catalog"),
                          on_click=lambda: ui.navigate.to("/")) \
                    .props("flat color=white").classes("text-sm")

        if not _session_active():
            _render_gate()
            return
        _render_tools()

    def _render_gate():
        from nicegui import ui
        configured = _admin_configured()
        with ui.column().classes("max-w-md mx-auto p-4 w-full gap-2 mt-8"):
            with ui.card().classes("w-full p-5 gap-2"):
                if configured:
                    ui.label(_tr("gate_login_title")).classes("text-lg font-bold")
                    ui.label(_tr("gate_login_hint")).classes("text-sm text-gray-600")
                    pw = ui.input(_tr("password"), password=True,
                                  password_toggle_button=True).classes("w-full")
                    err = ui.label("").classes("text-sm text-red-600")

                    def do_login():
                        if _verify_password(pw.value or ""):
                            _mark_session()
                            ui.navigate.reload()
                        else:
                            err.text = _tr("bad_pw")
                            pw.value = ""
                    pw.on("keydown.enter", lambda _e: do_login())
                    ui.button(_tr("login"), on_click=do_login) \
                        .props("color=primary")
                else:
                    ui.label(_tr("gate_setup_title")).classes("text-lg font-bold")
                    ui.label(_tr("gate_setup_hint")).classes("text-sm text-gray-600")
                    p1 = ui.input(_tr("password"), password=True,
                                  password_toggle_button=True).classes("w-full")
                    p2 = ui.input(_tr("confirm"), password=True,
                                  password_toggle_button=True).classes("w-full")
                    err = ui.label("").classes("text-sm text-red-600")

                    def do_create():
                        v1, v2 = p1.value or "", p2.value or ""
                        if len(v1) < 6:
                            err.text = _tr("pw_short"); return
                        if v1 != v2:
                            err.text = _tr("pw_mismatch"); return
                        ok, msg = _create_admin(v1)
                        if not ok:
                            err.text = msg; return
                        _mark_session()
                        ui.navigate.reload()
                    ui.button(_tr("create"), on_click=do_create) \
                        .props("color=primary")

    def _render_tools():
        from nicegui import ui
        import main

        with ui.column().classes("max-w-3xl mx-auto p-4 w-full gap-4"):
            ui.label("data-pistock : " + main.DATA_DIR) \
                .classes("text-xs font-mono text-gray-400")

            # --- EXPORT ---
            with ui.card().classes("w-full p-4 gap-2"):
                ui.label("📤 " + _tr("export_title")).classes("text-lg font-medium")
                ui.label(_tr("export_hint")).classes("text-sm text-gray-600")
                with ui.row().classes("w-full items-end gap-2 no-wrap"):
                    dest = ui.input(_tr("dest_folder")).props("dense").classes("flex-grow")
                    ui.button(icon="folder_open",
                              on_click=lambda: _open_folder_picker(dest)) \
                        .props("flat dense").tooltip(_tr("browse"))
                res = ui.label("").classes("text-sm")

                def do_export():
                    try:
                        ok, msg = export_data(main.DATA_DIR, (dest.value or "").strip())
                    except Exception as e:  # noqa: BLE001
                        ok, msg = False, str(e)
                    res.text = msg
                    res.classes(replace="text-sm " + ("text-green-700" if ok else "text-red-600"))
                    ui.notify(msg, type="positive" if ok else "negative")
                ui.button(_tr("run_export"), on_click=do_export).props("color=primary")

            # --- IMPORT ---
            with ui.card().classes("w-full p-4 gap-2"):
                ui.label("📥 " + _tr("import_title")).classes("text-lg font-medium")
                ui.label(_tr("import_hint")).classes("text-sm text-gray-600")
                with ui.row().classes("w-full items-end gap-2 no-wrap"):
                    isrc = ui.input(_tr("source_folder")).props("dense").classes("flex-grow")
                    ui.button(icon="folder_open",
                              on_click=lambda: _open_folder_picker(isrc)) \
                        .props("flat dense").tooltip(_tr("browse"))
                ires = ui.label("").classes("text-sm")

                def do_import():
                    src = (isrc.value or "").strip()
                    ok, info = _compat_check(os.path.join(src, DB_NAME))
                    if not _is_pistock_db(info):
                        ires.text = _tr("not_pistock")
                        ires.classes(replace="text-sm text-red-600")
                        ui.notify(_tr("not_pistock"), type="negative")
                        return
                    # Older schema -> accepted, will be auto-migrated. Warn.
                    body = _tr("confirm_import_b")
                    if not ok:
                        body += "\n\n" + _tr("migrate_note")

                    def really():
                        try:
                            ok2, msg = import_data(main.DATA_DIR, src)
                        except Exception as e:  # noqa: BLE001
                            ok2, msg = False, str(e)
                        ires.text = msg
                        ires.classes(replace="text-sm " + ("text-green-700" if ok2 else "text-red-600"))
                        ui.notify(msg, type="positive" if ok2 else "negative")
                        dlg.close()
                    dlg = _confirm_dialog(_tr("confirm_import_t"), body, really)
                ui.button(_tr("run_import"), on_click=do_import).props("color=warning")

            # --- NEW EMPTY DATABASE ---
            with ui.card().classes("w-full p-4 gap-2"):
                ui.label("🆕 " + _tr("empty_title")).classes("text-lg font-medium")
                ui.label(_tr("empty_hint")).classes("text-sm text-gray-600")
                eres = ui.label("").classes("text-sm")

                def do_empty():
                    def really():
                        try:
                            ok2, msg = recreate_empty(main.DATA_DIR)
                        except Exception as e:  # noqa: BLE001
                            ok2, msg = False, str(e)
                        eres.text = msg
                        eres.classes(replace="text-sm " + ("text-green-700" if ok2 else "text-red-600"))
                        ui.notify(msg, type="positive" if ok2 else "negative")
                        dlg.close()
                    dlg = _confirm_dialog(_tr("confirm_empty_t"),
                                          _tr("confirm_empty_b"), really)
                ui.button(_tr("run_empty"), on_click=do_empty).props("color=negative")

            # --- MERGE ---
            with ui.card().classes("w-full p-4 gap-2"):
                ui.label("🔀 " + _tr("merge_title")).classes("text-lg font-medium")
                ui.label(_tr("merge_hint")).classes("text-sm text-gray-600")
                with ui.row().classes("w-full items-end gap-2 no-wrap"):
                    msrc = ui.input(_tr("source_folder")).props("dense").classes("flex-grow")
                    ui.button(icon="folder_open",
                              on_click=lambda: _open_folder_picker(msrc)) \
                        .props("flat dense").tooltip(_tr("browse"))
                mres = ui.column().classes("w-full gap-0")

                def do_merge():
                    src = (msrc.value or "").strip()
                    ok, info = _compat_check(os.path.join(src, DB_NAME))
                    if not ok:
                        mres.clear()
                        with mres:
                            ui.label(_tr("incompatible") + " " + _format_info(info)) \
                                .classes("text-sm text-red-600")
                        ui.notify(_tr("incompatible"), type="negative")
                        return

                    def really():
                        try:
                            rep = merge_into(main.engine, main.DATA_DIR, src)
                            msg, ok2 = _tr("merged"), True
                        except Exception as e:  # noqa: BLE001
                            rep, msg, ok2 = {}, str(e), False
                        mres.clear()
                        with mres:
                            ui.label(msg).classes(
                                "text-sm " + ("text-green-700" if ok2 else "text-red-600"))
                            for k in ("rep_parts", "rep_revs", "rep_stock",
                                      "rep_proj", "rep_boms", "rep_lines",
                                      "rep_files"):
                                if rep.get(k):
                                    ui.label(f"• {rep[k]} {_tr(k)}") \
                                        .classes("text-xs text-gray-600")
                        ui.notify(msg, type="positive" if ok2 else "negative")
                        dlg.close()
                    dlg = _confirm_dialog(_tr("confirm_merge_t"),
                                          _tr("confirm_merge_b"), really)
                ui.button(_tr("run_merge"), on_click=do_merge).props("color=primary")

            # --- FREECAD WORKBENCH ---
            with ui.card().classes("w-full p-4 gap-2"):
                ui.label("🧩 " + _tr("wb_title")).classes("text-lg font-medium")
                ui.label(_tr("wb_hint")).classes("text-sm text-gray-600")
                with ui.row().classes("w-full items-end gap-2 no-wrap"):
                    wdest = ui.input(_tr("dest_folder")).props("dense").classes("flex-grow")
                    ui.button(icon="folder_open",
                              on_click=lambda: _open_folder_picker(wdest)) \
                        .props("flat dense").tooltip(_tr("browse"))
                wres = ui.label("").classes("text-sm")

                def do_wb():
                    try:
                        ok, msg = export_workbench((wdest.value or "").strip())
                    except Exception as e:  # noqa: BLE001
                        ok, msg = False, str(e)
                    wres.text = msg
                    wres.classes(replace="text-sm " + ("text-green-700" if ok else "text-red-600"))
                    ui.notify(msg, type="positive" if ok else "negative")
                ui.button(_tr("run_wb"), on_click=do_wb).props("color=primary")

    def _confirm_dialog(title, body, on_confirm):
        from nicegui import ui
        with ui.dialog() as dlg, ui.card().classes("min-w-[420px]"):
            ui.label(title).classes("text-lg font-bold")
            ui.label(body).classes("text-sm text-gray-700 whitespace-pre-line")
            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button(_tr("cancel"), on_click=dlg.close).props("flat")
                ui.button(_tr("confirm_btn"), on_click=on_confirm) \
                    .props("color=negative")
        dlg.open()
        return dlg

    def _format_info(info):
        bits = []
        if info.get("missing_tables"):
            bits.append(f'{_tr("missing_tables")}: {", ".join(info["missing_tables"])}')
        if info.get("missing_columns"):
            bits.append(f'{_tr("missing_cols")}: {info["missing_columns"]}')
        return " — ".join(bits)
