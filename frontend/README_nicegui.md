# Version NiceGUI

## Structure attendue

```
pistock/
├── backend/
│   └── app/
│       ├── main.py              ← remplacer
│       └── install/init_db.py   ← inchangé
└── frontend/
    └── ui.py                    ← nouveau (remplace tout l'ancien frontend/)
```

L'ancien `frontend/` HTML/JS doit être supprimé. Tout le frontend
est désormais dans `frontend/ui.py`.

## Installation

```bash
pip install nicegui
```

(En plus de fastapi, uvicorn, sqlmodel, python-multipart déjà
installés.)

## Lancement

Comme avant, depuis `backend/app/` :

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Puis ouvrir `http://127.0.0.1:8000/` dans le navigateur.

## Architecture

- L'API REST (`/api/v1/parts/upload`, etc.) est définie dans `main.py`
  et reste **inchangée** : la macro FreeCAD continue de fonctionner.
- L'interface NiceGUI est définie dans `frontend/ui.py` et s'attache
  au même FastAPI via `ui.run_with(main.app, ...)`.
- Tout tourne sur un seul port (8000), un seul processus.

## Routes

- `/`              — dashboard (liste des pièces) [NiceGUI]
- `/part/{id}`     — viewer 3D [NiceGUI]
- `/api/v1/...`    — endpoints REST [inchangés, utilisés par la macro]
- `/uploads/...`   — fichiers statiques (vignettes, .glb)
