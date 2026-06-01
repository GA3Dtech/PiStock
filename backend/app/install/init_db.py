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

import os
import sys

from sqlmodel import SQLModel, create_engine

# Le schema des tables vit dans backend/app/model.py (SOURCE UNIQUE DE
# VERITE, partagee avec le serveur main.py). On l'ajoute au path puis on
# importe les modeles : leur simple import les enregistre dans la
# metadata SQLModel, ce qui suffit a create_all() pour creer les tables.
_APP_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)
from model import (  # noqa: E402,F401
    Parts, PLM, Stock, Project, Bom, BomLine, Admin,
)


def setup_pistock_environment():
    print("==================================================")
    print("🛠️  Initializing PiStock Storage & Database...")
    print("==================================================")

    # 1. Resolve absolute paths relative to this script's location
    # Script is at: pistock/backend/app/install/init_db.py
    # Target path:  pistock/data/
    current_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.abspath(os.path.join(current_dir, "../../../../data-pistock"))

    uploads_dir = os.path.join(data_dir, "uploads")
    sub_dirs = [
        os.path.join(uploads_dir, "cad"),
        os.path.join(uploads_dir, "img"),
        os.path.join(uploads_dir, "doc"),
        os.path.join(uploads_dir, "stkimg"),  # photos de stock (prises au telephone, etc.)
    ]

    # 2. Create the directories if they don't exist
    print(f"📂 Creating directory structure at: {data_dir}")
    for folder in sub_dirs:
        os.makedirs(folder, exist_ok=True)
        print(f"   ✔️  Created: ...{os.path.relpath(folder, data_dir)}")

    # 3. Le schema des tables (Parts, PLM, Stock, Project, Bom, BomLine,
    #    Admin) est importe depuis model.py en haut de ce fichier. Le
    #    simple import a enregistre les classes dans la metadata
    #    SQLModel ; create_all() ci-dessous cree donc toutes les tables.

    # 4. Initialize SQLite Database Engine
    db_path = os.path.join(data_dir, "pistockdatabase.sqlite3")

    # --- BLOC DE SÉCURITÉ : Vérification de l'existence du système ---
    if os.path.exists(db_path):
        print("\n⚠️  [WARNING] A PiStock database already exists at this location!")
        print(f"📍 Path: {db_path}")

        # Demande de confirmation à l'utilisateur
        choice = input("👉 Do you want to overwrite everything and reset the database? (y/N): ").strip().lower()

        if choice != 'y':
            print("\n❌ Operation cancelled. Your existing data and folders were NOT modified.")
            print("==================================================")
            return  # Arrête la fonction proprement ici

        print("\n🔄 Overwriting allowed. Resetting the environment...")
        # On supprime l'ancien fichier pour repartir d'un schema propre
        # (sinon create_all ne modifie PAS les tables deja existantes).
        os.remove(db_path)
    # -----------------------------------------------------------------

    sqlite_url = f"sqlite:///{db_path}"
    engine = create_engine(sqlite_url, echo=True)


    print(f"\n🗄️  Creating database file and tables at: {db_path}")

    # This command reads your SQLModel classes and generates the tables in SQLite
    SQLModel.metadata.create_all(engine)

    print("==================================================")
    print("✅ Initialization complete! Your sandbox is ready.")
    print("==================================================")

if __name__ == "__main__":
    setup_pistock_environment()
