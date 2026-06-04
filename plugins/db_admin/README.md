# Database Admin — plugin guide

Admin-gated maintenance tools for the PiStock database and its uploaded
files. Reach it from **Plugins → 🗄️ Database admin** (`/plugin/db_admin`).

It exposes five operations: **Export**, **Restore (import)**, **New empty
database**, **Merge**, and **Copy the FreeCAD workbench**. This page
explains exactly what each one does, when to use it, and what it touches.

---

## Key concepts (read this first)

- **What is "the data"?** Everything lives in one folder, `data-pistock/`,
  next to the app: the SQLite database file (`pistockdatabase.sqlite3`)
  **and** the `uploads/` folder (CAD files, thumbnails, 3D previews, stock
  photos, datasheets, whiteboard/fab-notes attachments). A *backup* or
  *export* always copies **both** together, so data and files stay in sync.

- **All paths are server-side.** The folder picker browses the filesystem
  of the **machine running PiStock** (the Pi). An "external disk" means a
  disk *mounted on that machine* (e.g. a USB key plugged into the Pi).

- **Destructive operations always back up first.** Restore, Merge and
  New-empty-database each write a **timestamped backup** before changing
  anything, so any of them can be undone (see *Undo / recovery* below).

- **Admin gate.** The whole page is behind the **admin password** (the same
  one used for deletions). This is separate from the whole-UI *access*
  password. The first time, you set the admin password here.

- **Reload after a change.** Restore / Empty replace the live database
  file; after them, reload the app in your browser so it reflects the new
  data.

---

## Quick comparison

| Operation       | Reads from        | Current data | Result                                   | Schema mismatch |
|-----------------|-------------------|--------------|------------------------------------------|-----------------|
| **Export**      | —                 | untouched    | a copy is written to the target folder   | n/a             |
| **Restore**     | an export folder  | **replaced** | current data becomes the backup's data   | **auto-migrated** |
| **New empty DB**| —                 | **replaced** | a brand-new empty database               | n/a             |
| **Merge**       | an export folder  | **kept + added to** | the source's data is added into the current DB | rejected (must match) |
| **Copy workbench** | repo            | untouched    | the FreeCAD workbench is copied out      | n/a             |

> **Rule of thumb:** *Export* = make a backup. *Restore* = go back to a
> backup (replaces). *Merge* = combine two databases (adds). *New empty* =
> start over. *Copy workbench* = prepare the FreeCAD side for a workstation.

---

## 1. Export (backup) 📤

**What it does.** Copies the **entire** `data-pistock/` folder (database +
all uploaded files) into a new, timestamped subfolder of the target folder
you choose:

```
<target>/pistock-export-YYYYMMDD_HHMMSS/
    ├── pistockdatabase.sqlite3
    └── uploads/...
```

**When to use it.** Routine backups; before any risky change; to move a
copy onto a USB key or external disk for safekeeping or transfer.

**Touches the current data?** No — purely a copy. Completely safe.

**Notes.** Each export goes into its own timestamped folder, so exports
never overwrite each other. The resulting folder is exactly what *Restore*
and *Merge* expect as their source.

---

## 2. Restore (import) 📥

**What it does.**

1. Backs up the current `data-pistock/` to a timestamped
   `pistock-backup-YYYYMMDD_HHMMSS/` folder (next to `data-pistock/`).
2. **Replaces** the current database with the one from the source export.
3. Copies the source's `uploads/` in (merged over the current files).
4. **Auto-migrates** the restored database to the **current** schema.

**When to use it.** To roll back to an earlier backup, or to load a
database produced by another / older PiStock install.

**Touches the current data?** Yes — it **replaces** it. (But the previous
state is saved in the timestamped backup first, so it's reversible.)

**Older backups are accepted — and migrated.** This is the important part.
If the backup was made by an older PiStock whose schema lacked some columns
or tables that today's code expects, Restore upgrades it automatically:

- missing **tables** are created (including tables added by plugins, e.g.
  whiteboard / fab-notes);
- missing **columns** are added with their default value (e.g. a part that
  predates the `status` field gets `status = "Init"`);
- the migration is **additive only** — *nothing is ever dropped or
  overwritten*. Existing rows keep their data; only the new structure is
  added.

**What is refused.** Only a folder that isn't a PiStock export at all
(no recognizable tables). A database **newer** than the running code is
accepted as-is, but any structures the current code doesn't know about are
simply ignored (not removed).

**Notes.** The admin password is part of the database, so after restoring
an older/other backup, the admin password becomes the one stored in **that**
backup.

---

## 3. New empty database 🆕

**What it does.**

1. Backs up the current `data-pistock/` to a timestamped
   `pistock-backup-YYYYMMDD_HHMMSS/` folder.
2. Creates a **brand-new, empty** database using the current schema.
3. **Resets the uploaded files** (`uploads/` is emptied — the files remain
   in the backup).

**When to use it.** To start a workshop from a clean slate while keeping a
full backup of everything that was there.

**Touches the current data?** Yes — it **wipes** it (after backing it up).

**Notes.** Because the database is brand new, there is **no admin password**
afterwards; you'll be asked to set one again on the next destructive action.
The whole-UI *access* password is stored in a separate file and is **not**
affected.

---

## 4. Merge 🔀

**What it does.** Integrates another database (a source export) **into the
current one** — it *adds*, it does not replace. Policy:

- **Parts** are matched **by name**:
  - a part that already exists keeps its current stock; any **PLM
    revisions** present in the source but missing locally are **appended**
    (deduplicated by timestamp);
  - a part that doesn't exist yet is **imported** with its revisions, stock
    and files.
- **Projects and BOMs** are imported with **fresh codes** (their short
  sequential codes — AAA, AAB… — would otherwise collide between two
  databases), and the part / sub-BOM links are **remapped** to those new
  codes.
- Referenced **files** (CAD, thumbnails, 3D, stock photos, datasheets) are
  copied in as needed.

**When to use it.** To bring back work done on a second instance — e.g.
parts you added while running PiStock from a USB key on another machine —
without losing what's already in the current database.

**Touches the current data?** It **keeps** everything current and **adds**
the source on top. (A backup is still recommended via *Export* first.)

**Requires a matching schema.** Unlike Restore, Merge reads the source
through the current data model, so the source **must already match the
current schema**. An older/incompatible source is rejected. If you need to
merge an older database, *Restore* it into a throwaway instance first (which
migrates it), *Export* that, then *Merge* the export.

> **Merge vs Restore.** Restore = "make the database become this backup"
> (replaces, fidelity-preserving, migrates old schemas). Merge = "fold this
> other database into mine" (adds, re-codes projects/BOMs, same-schema
> only).

---

## 5. Copy the FreeCAD workbench 🧩

**What it does.** Copies the ready-to-use PiStock FreeCAD workbench into
`<target>/PiStock`. The copy already carries **this server's address**
(`pistock_host.txt`) and **TLS certificate** (`pistock_ca.pem`), so it is
drop-in for a workstation.

**When to use it.** To deploy or update the FreeCAD side on a workstation:
copy the resulting `PiStock` folder onto a USB key, drop it into FreeCAD's
`Mod` directory, restart FreeCAD.

**Touches the current data?** No.

**Notes.** If the server address / certificate haven't been configured yet
(fresh repo never deployed), the result message warns you; run the
deployment (`deploy/install_pi.sh`) or `deploy/dev_set_location.sh` first so
the workbench trusts this server.

---

## Undo / recovery

Every operation that changes the live data writes a backup **before** acting:

- **Restore** and **New empty database** → `pistock-backup-YYYYMMDD_HHMMSS/`
  created next to `data-pistock/`.
- (**Export** itself *is* a backup.)

To undo, simply **Restore** from the relevant `pistock-backup-…` (or
`pistock-export-…`) folder — it will become your current data again. Backup
folder names are made unique automatically, so two operations in the same
second never overwrite one another.

---

## Safety summary

| Operation        | Backs up first | Replaces current data | Resets files | Resets admin password |
|------------------|:--------------:|:---------------------:|:------------:|:---------------------:|
| Export           | (is a backup)  | no                    | no           | no                    |
| Restore          | ✅             | ✅                    | merges in    | from the backup       |
| New empty DB     | ✅             | ✅                    | ✅           | ✅ (cleared)          |
| Merge            | recommended    | no (adds)             | no           | no                    |
| Copy workbench   | n/a            | no                    | no           | no                    |
