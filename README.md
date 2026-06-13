# PiStock
A lightweight, self-hosted PLM and inventory management system running on Raspberry Pi, featuring FreeCAD integration and web-based 3D visualization.

# PiStock

PiStock is an open-source, self-hosted Product Lifecycle Management (PLM) and inventory tracking ecosystem designed specifically for makers, hackerspaces, and small engineering workshops. 

It acts as a digital bridge between your virtual CAD models and your physical inventory, running efficiently on low-power hardware like a Raspberry Pi.

> **Status:** This project is currently in early development. Architecture and APIs are subject to breaking changes. Silent development mode is active.

---

## 🎬 Overview

Two short screen-capture walkthroughs introduce PiStock and show how to install it.

> 🇫🇷 **The videos are in French.** For now, just turn on YouTube's auto-translated subtitles (or the auto-dubbed audio track). I know it's not ideal, but this is a personal project, so it'll do for now — if PiStock gains traction, I'll do better later.

| Introduction | Installation |
|:---:|:---:|
| [![PiStock — Introduction](docs/images/youtube-intro.jpg)](https://youtu.be/7C0r99gCSrI) | [![PiStock — Installation](docs/images/youtube-install.jpg)](https://youtu.be/Fmihq0oUiy8) |
| **[▶ Watch the introduction](https://youtu.be/7C0r99gCSrI)** | **[▶ Watch the installation guide](https://youtu.be/Fmihq0oUiy8)** |

---

## 🚀 Key Features (Roadmap)

* **Minimalist PLM:** Simple version control and file locking for hardware design without the overhead of enterprise ERPs.
* **Smart Inventory:** Track raw materials, purchased hardware (screws, fasteners, electronics), printed parts, and salvaged components.
* **FreeCAD Integration:** A dedicated workbench/addon to push `.FCStd` files and metadata directly from FreeCAD to your local PiStock server.
* **Web-Based 3D Viewer:** Automated conversion to `.gltf`/`.glb` for lightweight, interactive 3D assembly viewing and annotations directly in the browser (no CAD software required for non-designers).
* **Mobile Terminal Capability:** API-first design built to support companion mobile apps for quick scanning (QR codes), stock level updates, and visual logging via camera streams.
* **Plugin Architecture:** Fully modular core, allowing custom community extensions (e.g., automated component pricing APIs, pick-to-light LED shelf integrations).

---

## 🛠️ Architecture Overview

PiStock is engineered with strict decoupling to ensure modularity and long-term maintainability:

1. **PiStock Core (Backend):** A lightweight, asynchronous Python API (FastAPI) interacting with a local SQLite/PostgreSQL database, handling file storage, version histories, and item states.
2. **PiStock Web (Frontend):** A responsive web interface hosting the inventory dashboard and the embedded 3D WebGL viewer.
3. **PiStock CAD Link:** Python-based integration macro/workbench for FreeCAD handling automated headless background exports.

---

## 📦 Installation & Setup

*Documentation for deployment on Raspberry Pi / Docker environments will be provided as the core API reaches stability.*

### Development Prerequisites
- Python 3.10+
- FreeCAD (for testing the export pipeline)

---

## 🤝 Contributing & Pull Requests

Contributions are welcome once the initial core architecture is pushed! Please note:
* The core server and official web client are licensed under the **AGPLv3**.
* Bug fixes and trivial patches are integrated directly. Major feature contributions may require a Contributor License Agreement (CLA) to maintain the project's long-term sustainability and dual-licensing capabilities.

---

## 📄 License

PiStock is open-source software licensed under the **GNU Affero General Public License v3 (AGPLv3)**. See the `LICENSE` file for more details.
