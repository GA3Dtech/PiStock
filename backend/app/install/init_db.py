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
        part_name: str = Field(index=True, unique=True)
        # Lien optionnel vers un projet. Nullable car une piece peut
        # exister sans projet (legacy ou pieces standalone).
        id_project: int | None = Field(default=None,
                                        foreign_key="project.id")
        # Statut de maturite de la piece : 'Init' (en cours), 'Revue'
        # (en relecture), 'Asset' (validee, prete pour usage prod).
        status: str = Field(default="Init")
        # Verrou : quand True, l'UI empeche les modifications (projet,
        # statut). N'empeche PAS les uploads de nouvelles revisions
        # via la macro FreeCAD (sinon trop restrictif pour un PLM).
        locked: bool = Field(default=False)

    class PLM(SQLModel, table=True):
        __tablename__ = "plm"
        id: int | None = Field(default=None, primary_key=True)
        id_parts: int = Field(foreign_key="parts.id", nullable=False)
        path_2_cadfile: str | None = Field(default=None)
        path_2_thumbnail: str | None = Field(default=None)
        path_2_3dglb: str | None = Field(default=None)
        timestamp: datetime = Field(
            default_factory=lambda: datetime.now(timezone.utc)
        )
        author: str | None = Field(default=None)
        # Numero de version : deux lettres minuscules, aa->zz (676 max).
        # Incremente automatiquement a chaque nouvelle revision PLM
        # POUR UNE PIECE DONNEE. Premier push d'une piece = 'aa'.
        version: str = Field(default="aa", max_length=2)

    class Stock(SQLModel, table=True):
        __tablename__ = "stock"
        id: int | None = Field(default=None, primary_key=True)
        # Link directly to the primary key of the parts table
        id_parts: int = Field(foreign_key="parts.id", nullable=False)
        path_2_img: str | None = Field(default=None)
        quantity: int = Field(default=0)
        location: str | None = Field(default=None)
        supply: str | None = Field(default=None)
        # Chemin vers la fiche composant (PDF, datasheet...) stockee
        # dans data-pistock/uploads/doc/.
        path_2_doc: str | None = Field(default=None)

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
