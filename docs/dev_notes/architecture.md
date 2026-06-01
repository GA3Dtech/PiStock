# PiStock — Architecture & Maintainability Refactor

> Developer notes. Summarizes the structure of the codebase after the
> June 2026 maintainability refactor, and what changed during it.
> Audience: contributors. Language: English.

## 1. Why this refactor

The application worked, but three large files concentrated almost
everything and hurt maintainability:

- `backend/app/main.py` (~1940 lines): FastAPI app + DB models + all
  business helpers + ~34 REST endpoints.
- `frontend/ui.py` (~3100 lines): every NiceGUI page, dialog and DB
  access helper.
- The DB schema was **duplicated** between `main.py` and
  `install/init_db.py` — and had already drifted (the `admin` table was
  missing from `init_db.py`).

The refactor split these by responsibility, made the DB schema a single
source of truth, and made the UI translatable (EN/FR/DE). No runtime
behavior changed: the 50-test suite stayed green throughout and all
pages/endpoints keep their exact paths.

## 2. What changed, by phase

| Phase | Goal | Commit |
|------|------|--------|
| 1 | Unify the DB schema into one module (`model.py`) | `ddaca79` |
| 2 | Split `main.py` into per-domain services | `f84ec4f` |
| 3 | Split `ui.py` into pages + components + DB layer | `b3923a2` |
| 4 | Translate code comments→EN, make UI translatable, add German | `ec50c1e` |

## 3. Backend structure (`backend/app/`)

`main.py` is now a thin assembler. The schema lives in `model.py`, shared
infrastructure in `config.py`, and each domain owns a module under
`services/` that exposes a FastAPI `APIRouter`.

```
backend/app/
  config.py        # paths, SQL engine, logger, _delete_file_if_exists
  model.py         # the 7 SQLModel tables — SINGLE SOURCE OF TRUTH
  main.py          # assembles routers, mounts static, loads the UI, compat facade
  services/
    codes.py       # project/BOM code + PLM version generation, _get_current_plm
    admin.py       # password hashing/verify, _require_admin + /admin/* router
    projects.py    # projects router
    boms.py        # _flatten_bom, _would_create_cycle + BOM router
    parts.py       # parts + PLM revisions + upload router
    stock.py       # _get_or_create_stock + stock router
  install/
    init_db.py     # imports model.py, then SQLModel.metadata.create_all()
```

| File | Lines | Responsibility |
|------|------:|----------------|
| `config.py` | 59 | Paths, `engine`, `logger`, file-deletion helper |
| `model.py` | 144 | `Parts, PLM, Stock, Project, Bom, BomLine, Admin` |
| `main.py` | 131 | Router assembly + static mounts + UI load + facade |
| `services/codes.py` | 160 | Human-readable identifier generation |
| `services/admin.py` | 169 | Admin auth + `/admin/*` endpoints |
| `services/projects.py` | 109 | Project endpoints |
| `services/boms.py` | 628 | BOM hierarchy logic + 10 BOM endpoints |
| `services/parts.py` | 630 | Parts, revisions, upload (13 endpoints) |
| `services/stock.py` | 216 | Stock endpoints (4) |

Dependency direction (acyclic):

```
config  <-  model  <-  codes / admin  <-  stock  <-  boms / parts  <-  main
```

### Compatibility facade

`main.py` re-exports the models and the public helpers
(`main.Parts`, `main.engine`, `main._flatten_bom`, `main.app`, …). This is
intentional: the **NiceGUI UI**, the **plugins** (e.g. `plugins/bom_tree`)
and the **test suite** all reach the core via `import main`. Keeping these
names means none of those consumers had to change.

> If you move a helper, keep (or add) its re-export in `main.py` unless you
> also update every `import main` consumer.

### Single source of truth for the schema

`model.py` defines each `table=True` class exactly once. Both the server
(`main.py`) and the installer (`install/init_db.py`) import them — so the
schema is edited in **one place**. A `table=True` class must never be
declared twice (SQLModel registers it in shared metadata and would raise
"Table already defined").

## 4. Frontend structure (`frontend/`)

`ui.py` is now a thin entry point. Pages live under `pages/`, reusable
widgets under `components/`, and all database access in a dedicated `db.py`
layer (direct SQLModel access via the `main` facade — no internal HTTP).

```
frontend/
  ui.py                 # imports the pages (registers @ui.page), loads plugins, ui.run_with
  app_core.py           # _apply_user_lang, _register_pwa, SOURCE_CODE_URL
  i18n.py               # gettext-style _(), .po loader, AVAILABLE_LANGS
  db.py                 # all fetch_* / save_* / *_db helpers
  components/
    header.py           # render_app_header (shared by every page)
    admin.py            # admin session state + login/setup/change dialogs + _ensure_admin
  pages/
    dashboard.py        # "/"            catalog + part/project/BOM/stock dialogs
    part.py             # "/part/{id}"   3D viewer + PLM revisions
    plugins.py          # "/plugins"     plugin loader + index page
  locales/{en,fr,de}/LC_MESSAGES/messages.po
```

| File | Lines | Responsibility |
|------|------:|----------------|
| `ui.py` | 66 | Entry point: registers pages, loads plugins, `ui.run_with` |
| `app_core.py` | 74 | Per-page bootstrap (language, PWA), source link |
| `db.py` | 781 | DB access layer (no `ui` dependency) |
| `components/header.py` | 110 | Common page header |
| `components/admin.py` | 206 | Admin session + dialogs + `_ensure_admin` |
| `pages/dashboard.py` | 1652 | Catalog page + most dialogs |
| `pages/part.py` | 265 | Part detail / 3D viewer + revisions |
| `pages/plugins.py` | 138 | Plugin loading + index page |

Dependency direction (acyclic):

```
app_core  <-  components.admin  <-  components.header / db  <-  pages.*  <-  ui
```

The backend is reached **lazily** (`import main` inside functions) to avoid
an import cycle, because `main.py` imports `ui` at the end of its own load.

## 5. Internationalization (i18n)

User-facing strings are wrapped in `_("English text")`. The msgid is the
**English** source string; translations live in per-language `.po` files
under `frontend/locales/`. `i18n.py` loads `.mo` if present, otherwise parses
the `.po` directly (no compile step needed in dev).

- **Languages:** English (source), French, German — declared in
  `AVAILABLE_LANGS` in `i18n.py`; switchable from the header toggle.
- **Catalog size:** 167 msgids, present in all three `.po` files.
- **Interpolation:** use named placeholders, e.g.
  `_("The part '{name}' is locked.").format(name=...)`. A translation must
  keep the **same** `{placeholders}` as its msgid (mismatch → `KeyError`).

### Adding a translatable string

1. Wrap it: `ui.label(_("My text"))`.
2. Add the same `msgid`/`msgstr` pair to `fr` and `de` `.po` files
   (and to `en`, where `msgstr == msgid`).
3. No build step required in development.

### Gotcha: `_` is the translation function

Do **not** rebind `_` as a throwaway variable in any scope that also calls
`_("…")`. The classic idioms collide:

```python
_ = on_change            # BAD: shadows the translation function
ok, msg, _ = func()      # BAD if _("…") is used later in the same scope
```

Use a named variable instead (`del on_change`, `_new_locked`, …). This
exact bug caused a render-time `HTTP 500` during the refactor and is not
caught by import/compile/unit tests — only by actually rendering the page.

## 6. Comments & documentation language

All Python code comments and docstrings are in **English**. Note that some
strings are intentionally **not** translated yet:

- Backend `HTTPException(detail=…)` and `logger.*` messages are still in
  French (they are API/log output consumed by the FreeCAD macro and logs,
  not UI).
- FreeCAD macros (`backend/CAD-extensions/freecad/*.FCMacro`) and shell
  scripts still contain French comments.

## 7. Testing & verification

- Unit tests: `pytest -q` from the repo root (50 tests). They import the
  core via `import main` and run against an in-memory SQLite DB.
- Smoke test: `uvicorn main:app` (from `backend/app/`) and check that `/`,
  `/part/{id}`, `/plugins`, `/plugin/bom_tree` and `/api/v1/...` return 200.
  This is the only check that catches render-time issues (see the `_`
  gotcha above).

## 8. Conventions checklist for contributors

- Edit the DB schema **only** in `model.py`.
- New REST endpoints go in the relevant `services/*.py` router; if they need
  a new public helper consumed by the UI/plugins/tests, re-export it from
  `main.py`.
- New UI goes in `pages/` (a page) or `components/` (a reusable widget);
  database access goes through `db.py`, never inline in a page.
- Wrap every user-facing string in `_()` and add its `fr`/`de` translations.
- Never shadow `_`.
- Run `pytest` **and** a server smoke test before committing UI changes.
