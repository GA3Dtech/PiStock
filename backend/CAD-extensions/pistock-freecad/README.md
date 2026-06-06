# PiStock — FreeCAD workbench

A FreeCAD workbench that wraps the three PiStock macros into a toolbar +
menu, so there is nothing to wire up by hand. It uses the standard
namespace-package layout (like the FreeCAD workbench starter kit):

```
pistock-freecad/
├── package.xml
└── freecad/
    └── pistock_workbench/
        ├── init_gui.py            ← registers the workbench
        ├── __init__.py
        ├── pistock_*.FCMacro      ← the 3 macros
        ├── pistock_host.txt(.example)   ← server address (filled at deploy)
        ├── pistock_ca.pem         ← local root CA (added at deploy)
        └── resources/icons/*.svg
```

| Command | What it does |
|---------|--------------|
| **Export part** | Send the active part (CAD + thumbnail + 3D) to PiStock |
| **Browse catalog** | Browse and open parts stored in PiStock |
| **BOM from assembly** | Build a PiStock BOM from the active assembly |

## Install (USB-stick friendly)

1. Copy this whole `pistock-freecad` folder into FreeCAD's `Mod`
   directory and rename it `PiStock`:
   - Windows: `%APPDATA%\FreeCAD\Mod\PiStock`
   - Linux: `~/.local/share/FreeCAD/Mod/PiStock`
   - macOS: `~/Library/Application Support/FreeCAD/Mod/PiStock`
   (FreeCAD discovers `freecad/pistock_workbench` inside it.)
2. Restart FreeCAD → a **PiStock** workbench appears in the selector.

## Server address & certificate

The commands read two files inside `freecad/pistock_workbench/`:

- `pistock_host.txt` — the server IP/host on one line
  (e.g. `192.168.1.50:8000`). Copy `pistock_host.txt.example` if missing.
- `pistock_ca.pem` — the PiStock **local root CA**, so the server's
  LAN certificate is trusted (strict verification). Because the server
  leaf is re-signed by this CA, rotating or changing the server cert
  does **not** require redistributing this file. With a real certificate
  (e.g. Let's Encrypt) this file is not needed.

**The PiStock installer pre-fills both** (`deploy/install_pi.sh`). So
after deployment: copy this folder to a USB stick → drop it in
`Mod/PiStock` → restart FreeCAD → done.
