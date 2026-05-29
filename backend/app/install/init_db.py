import os
from datetime import datetime, timezone
from sqlmodel import SQLModel, Field, create_engine

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

    # 3. Define SQLModel Schemas (Your 3 tables)
    class Parts(SQLModel, table=True):
        __tablename__ = "parts"
        id: int | None = Field(default=None, primary_key=True)
        # Nom de la piece. 'unique=True' garantit qu'on ne peut pas
        # enregistrer deux fois la meme piece -> indispensable pour
        # pouvoir verifier "ce nom existe-t-il deja ?" cote serveur.
        part_name: str = Field(index=True, unique=True)

    class PLM(SQLModel, table=True):
        __tablename__ = "plm"
        id: int | None = Field(default=None, primary_key=True)
        # Link directly to the primary key of the parts table
        id_parts: int = Field(foreign_key="parts.id", nullable=False)
        path_2_cadfile: str | None = Field(default=None)
        path_2_thumbnail: str | None = Field(default=None)
        path_2_3dglb: str | None = Field(default=None)
        # Horodatage de l'enregistrement. Chaque export cree une
        # nouvelle ligne PLM avec un timestamp different : c'est ce
        # qui permet de tracer les revisions successives d'une piece.
        timestamp: datetime = Field(
            default_factory=lambda: datetime.now(timezone.utc)
        )
        # Auteur de cette revision : qui a poussé cet export vers
        # la base. Renseigne par le GUI de la macro FreeCAD.
        author: str | None = Field(default=None)

    class Stock(SQLModel, table=True):
        __tablename__ = "stock"
        id: int | None = Field(default=None, primary_key=True)
        # Link directly to the primary key of the parts table
        id_parts: int = Field(foreign_key="parts.id", nullable=False)
        path_2_img: str | None = Field(default=None)
        quantity: int = Field(default=0)
        location: str | None = Field(default=None)
        supply: str | None = Field(default=None)

    class Project(SQLModel, table=True):
        __tablename__ = "project"
        id: int | None = Field(default=None, primary_key=True)
        # Code alphabetique a 3 lettres majuscules, incremental :
        # AAA, AAB, ..., AAZ, ABA, ..., ZZZ. Unique car il sert
        # d'identifiant lisible (visible dans l'UI).
        code: str = Field(index=True, unique=True, max_length=3)
        # Description libre, multi-lignes. Optionnelle.
        description: str | None = Field(default=None)

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
