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

# The table schema lives in backend/app/model.py (SINGLE SOURCE OF
# TRUTH, shared with the main.py server). We add it to the path then
# import the models: simply importing them registers them in the
# SQLModel metadata, which is enough for create_all() to create the tables.
_APP_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)
from model import (  # noqa: E402,F401
    Parts, PLM, Stock, Project, Bom, BomLine, Admin, PartRef,
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
        os.path.join(uploads_dir, "stkimg"),  # stock photos (taken with a phone, etc.)
    ]

    # 2. Create the directories if they don't exist
    print(f"📂 Creating directory structure at: {data_dir}")
    for folder in sub_dirs:
        os.makedirs(folder, exist_ok=True)
        print(f"   ✔️  Created: ...{os.path.relpath(folder, data_dir)}")

    # 3. The table schema (Parts, PLM, Stock, Project, Bom, BomLine,
    #    Admin) is imported from model.py at the top of this file. The
    #    import alone registered the classes in the SQLModel metadata;
    #    create_all() below therefore creates all the tables.

    # 4. Initialize SQLite Database Engine
    db_path = os.path.join(data_dir, "pistockdatabase.sqlite3")

    # --- SAFETY BLOCK: check whether the system already exists ---
    if os.path.exists(db_path):
        print("\n⚠️  [WARNING] A PiStock database already exists at this location!")
        print(f"📍 Path: {db_path}")

        # Ask the user for confirmation
        choice = input("👉 Do you want to overwrite everything and reset the database? (y/N): ").strip().lower()

        if choice != 'y':
            print("\n❌ Operation cancelled. Your existing data and folders were NOT modified.")
            print("==================================================")
            return  # Stop the function cleanly here

        print("\n🔄 Overwriting allowed. Resetting the environment...")
        # We delete the old file to start from a clean schema
        # (otherwise create_all does NOT modify already-existing tables).
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
