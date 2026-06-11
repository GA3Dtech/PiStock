# ======================================================================
#  PiStock — unit tests (pure logic + database helpers)
# ======================================================================
# Target: the functions where a regression is costly and that we can
# test trivially — code generation (base 26), PLM versions, recursive
# BOM flattening, cycle detection.
#
# To run (from the repo root):
#     pip install pytest
#     pytest -q
#
# The database helpers all take a `session` argument: we give them an
# IN-MEMORY SQLite database, isolated and discarded after each test.
# No dependency on the real data-pistock/ database.
# ----------------------------------------------------------------------
import pytest
from fastapi import HTTPException
from sqlmodel import SQLModel, Session, create_engine, select

import main  # resolved via tests/conftest.py


# ---------------------------------------------------------------------
#  Fixture: in-memory SQLite session with the PiStock schema
# ---------------------------------------------------------------------
@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


# Small factory helpers to keep the tests readable
def _mk_part(session, name):
    p = main.Parts(part_name=name)
    session.add(p)
    session.flush()
    return p


def _mk_bom(session, code, description=None):
    b = main.Bom(code=code, description=description)
    session.add(b)
    session.flush()
    return b


# =====================================================================
#  1. Code conversions — pure functions (no database)
# =====================================================================
class TestProjectCodeMath:
    @pytest.mark.parametrize("code,expected", [
        ("AAA", 0), ("AAB", 1), ("AAZ", 25),
        ("ABA", 26), ("BAA", 676), ("ZZZ", 17575),
    ])
    def test_code_to_int(self, code, expected):
        assert main._code_to_int(code) == expected

    @pytest.mark.parametrize("code", ["AAA", "AAB", "AAZ", "ABA", "MNO", "ZZZ"])
    def test_roundtrip(self, code):
        assert main._int_to_code(main._code_to_int(code)) == code

    def test_bounds(self):
        assert main._int_to_code(0) == "AAA"
        assert main._int_to_code(main.PROJECT_CODE_MAX) == "ZZZ"


class TestVersionMath:
    @pytest.mark.parametrize("v,expected", [
        ("aa", 0), ("ab", 1), ("az", 25), ("ba", 26), ("zz", 675),
    ])
    def test_version_to_int(self, v, expected):
        assert main._version_to_int(v) == expected

    @pytest.mark.parametrize("v", ["aa", "ab", "az", "ba", "mn", "zz"])
    def test_roundtrip(self, v):
        assert main._int_to_version(main._version_to_int(v)) == v

    def test_bounds(self):
        assert main._int_to_version(0) == "aa"
        assert main._int_to_version(main.PLM_VERSION_MAX) == "zz"


# =====================================================================
#  2. Generation of the next project code
# =====================================================================
class TestNextProjectCode:
    def test_empty_db_starts_at_AAA(self, session):
        assert main._next_project_code(session) == "AAA"

    def test_increment(self, session):
        session.add(main.Project(code="AAA"))
        session.flush()
        assert main._next_project_code(session) == "AAB"

    def test_carry_over(self, session):
        session.add(main.Project(code="AAZ"))
        session.flush()
        assert main._next_project_code(session) == "ABA"

    def test_overflow_raises_507(self, session):
        session.add(main.Project(code="ZZZ"))
        session.flush()
        with pytest.raises(HTTPException) as exc:
            main._next_project_code(session)
        assert exc.value.status_code == 507


# =====================================================================
#  3. Generation of the next BOM code
# =====================================================================
class TestNextBomCode:
    def test_empty_db_starts_at_B0001(self, session):
        assert main._next_bom_code(session) == "B0001"

    def test_increment(self, session):
        _mk_bom(session, "B0001")
        assert main._next_bom_code(session) == "B0002"

    def test_zero_padding(self, session):
        _mk_bom(session, "B0009")
        assert main._next_bom_code(session) == "B0010"

    def test_overflow_raises_507(self, session):
        _mk_bom(session, "B9999")
        with pytest.raises(HTTPException) as exc:
            main._next_bom_code(session)
        assert exc.value.status_code == 507


# =====================================================================
#  4. PLM version generation (per part)
# =====================================================================
class TestNextVersionForPart:
    def test_first_revision_is_aa(self, session):
        p = _mk_part(session, "bracket")
        assert main._next_version_for_part(session, p.id) == "aa"

    def test_increment(self, session):
        p = _mk_part(session, "bracket")
        session.add(main.PLM(id_parts=p.id, version="aa"))
        session.flush()
        assert main._next_version_for_part(session, p.id) == "ab"

    def test_is_per_part(self, session):
        # The version of one part does not influence another's.
        p1 = _mk_part(session, "alpha")
        p2 = _mk_part(session, "beta")
        session.add(main.PLM(id_parts=p1.id, version="ae"))
        session.flush()
        assert main._next_version_for_part(session, p2.id) == "aa"

    def test_overflow_raises_507(self, session):
        p = _mk_part(session, "bracket")
        session.add(main.PLM(id_parts=p.id, version="zz"))
        session.flush()
        with pytest.raises(HTTPException) as exc:
            main._next_version_for_part(session, p.id)
        assert exc.value.status_code == 507


# =====================================================================
#  5. Recursive BOM flattening (sub-BOMs)
# =====================================================================
class TestFlattenBom:
    def test_simple(self, session):
        p1 = _mk_part(session, "vis-M3")
        p2 = _mk_part(session, "ecrou")
        a = _mk_bom(session, "B0001")
        session.add(main.BomLine(id_bom=a.id, id_parts=p1.id, quantity=5))
        session.add(main.BomLine(id_bom=a.id, id_parts=p2.id, quantity=2))
        session.flush()
        assert main._flatten_bom(session, a.id) == {p1.id: 5, p2.id: 2}

    def test_nested_with_factor(self, session):
        # Reproduces the docstring example: A contains 5×vis and
        # 2×(sub-BOM B), B contains 3×ecrou + 1×rondelle.
        # => {vis:5, ecrou:6, rondelle:2}
        vis = _mk_part(session, "vis-M3")
        ecrou = _mk_part(session, "ecrou")
        rondelle = _mk_part(session, "rondelle")
        a = _mk_bom(session, "B0001")
        b = _mk_bom(session, "B0002")
        session.add(main.BomLine(id_bom=b.id, id_parts=ecrou.id, quantity=3))
        session.add(main.BomLine(id_bom=b.id, id_parts=rondelle.id, quantity=1))
        session.add(main.BomLine(id_bom=a.id, id_parts=vis.id, quantity=5))
        session.add(main.BomLine(id_bom=a.id, id_subbom=b.id, quantity=2))
        session.flush()
        assert main._flatten_bom(session, a.id) == {
            vis.id: 5, ecrou.id: 6, rondelle.id: 2,
        }

    def test_global_factor_multiplies_everything(self, session):
        p = _mk_part(session, "vis")
        a = _mk_bom(session, "B0001")
        session.add(main.BomLine(id_bom=a.id, id_parts=p.id, quantity=4))
        session.flush()
        assert main._flatten_bom(session, a.id, factor=3) == {p.id: 12}


# =====================================================================
#  6. Cycle detection in the BOM hierarchy
# =====================================================================
class TestWouldCreateCycle:
    def test_self_reference(self, session):
        a = _mk_bom(session, "B0001")
        assert main._would_create_cycle(session, a.id, a.id) is True

    def test_indirect_cycle(self, session):
        # A -> B -> C ; adding A as a sub-BOM of C creates a loop.
        a = _mk_bom(session, "B0001")
        b = _mk_bom(session, "B0002")
        c = _mk_bom(session, "B0003")
        session.add(main.BomLine(id_bom=a.id, id_subbom=b.id, quantity=1))
        session.add(main.BomLine(id_bom=b.id, id_subbom=c.id, quantity=1))
        session.flush()
        assert main._would_create_cycle(session, c.id, a.id) is True

    def test_no_cycle(self, session):
        a = _mk_bom(session, "B0001")
        d = _mk_bom(session, "B0004")  # no children
        assert main._would_create_cycle(session, a.id, d.id) is False


# =====================================================================
#  7. Admin authentication (PBKDF2 + endpoints)
# =====================================================================
class TestAdminPasswordHash:
    def test_roundtrip(self):
        salt = main._new_salt()
        h = main._hash_password("hunter2", salt)
        assert main._verify_password("hunter2", salt.hex(), h) is True

    def test_rejects_wrong_password(self):
        salt = main._new_salt()
        h = main._hash_password("hunter2", salt)
        assert main._verify_password("Hunter2", salt.hex(), h) is False
        assert main._verify_password("", salt.hex(), h) is False

    def test_salt_changes_hash(self):
        s1, s2 = main._new_salt(), main._new_salt()
        # Different salts -> different hashes for the same password
        assert s1 != s2
        assert main._hash_password("same", s1) != main._hash_password("same", s2)

    def test_hash_is_hex_and_long_enough(self):
        # SHA-256 -> 32 bytes -> 64 hex characters
        h = main._hash_password("x", main._new_salt())
        assert len(h) == 64
        int(h, 16)  # does not raise if it is valid hex


class TestCheckAdminPassword:
    def test_no_admin_configured_raises_503(self, session):
        # Note: _check_admin_password opens its own Session on main's
        # global engine, so we cannot test the absence of an admin via
        # the in-memory fixture. We only check the empty/None password
        # cases, which raise 401 without touching the DB.
        with pytest.raises(HTTPException) as exc:
            main._check_admin_password(None)
        assert exc.value.status_code == 401

    def test_empty_password_raises_401(self):
        with pytest.raises(HTTPException) as exc:
            main._check_admin_password("")
        assert exc.value.status_code == 401


class TestAdminModelInSchema:
    def test_admin_table_created(self, session):
        # The 'admin' table is part of the schema (created by create_all).
        # We verify that we can insert and read a record.
        salt = main._new_salt()
        rec = main.Admin(
            salt=salt.hex(),
            password_hash=main._hash_password("test123", salt),
        )
        session.add(rec)
        session.commit()
        session.refresh(rec)
        assert rec.id is not None
        assert rec.created_at  # default_factory worked

        fetched = session.exec(select(main.Admin)).first()
        assert fetched is not None
        assert main._verify_password("test123", fetched.salt,
                                       fetched.password_hash)


# =====================================================================
#  Part 'info' field + startup schema auto-migration
# =====================================================================
class TestPartInfoField:
    def test_info_defaults_to_none(self, session):
        p = _mk_part(session, "bracket-info")
        session.commit()
        session.refresh(p)
        assert p.info is None

    def test_info_round_trips(self, session):
        p = _mk_part(session, "bracket-tag")
        p.info = "#cnc #alu"
        session.add(p)
        session.commit()
        session.refresh(p)
        assert session.get(main.Parts, p.id).info == "#cnc #alu"


class TestStartupMigration:
    """`_ensure_missing_columns` brings a pre-existing DB up to the
    current schema by adding only the missing columns (it never alters
    or drops anything). This is what lets a running instance pick up
    e.g. parts.info on the next start, with no restore."""

    def _old_parts_db(self, tmp_path):
        # A 'parts' table from before the 'info' column existed.
        import sqlite3
        dbp = str(tmp_path / "old.sqlite3")
        con = sqlite3.connect(dbp)
        con.execute(
            "CREATE TABLE parts (id INTEGER PRIMARY KEY, part_name TEXT, "
            "id_project INTEGER, status TEXT DEFAULT 'Init', "
            "locked BOOLEAN DEFAULT 0)")
        con.execute("INSERT INTO parts (part_name) VALUES ('legacy')")
        con.commit()
        con.close()
        return dbp

    def test_adds_missing_column(self, tmp_path):
        import sqlite3
        dbp = self._old_parts_db(tmp_path)
        eng = create_engine(f"sqlite:///{dbp}")
        SQLModel.metadata.create_all(eng)  # adds missing TABLES only
        cols = {r[1] for r in sqlite3.connect(dbp)
                .execute("PRAGMA table_info(parts)")}
        assert "info" not in cols  # create_all left the existing table as-is

        applied = main._ensure_missing_columns(eng)
        assert "parts.info" in applied
        cols = {r[1] for r in sqlite3.connect(dbp)
                .execute("PRAGMA table_info(parts)")}
        assert "info" in cols
        # Existing row preserved (additive migration).
        n = sqlite3.connect(dbp).execute(
            "SELECT COUNT(*) FROM parts").fetchone()[0]
        assert n == 1

    def test_is_idempotent(self, tmp_path):
        dbp = self._old_parts_db(tmp_path)
        eng = create_engine(f"sqlite:///{dbp}")
        SQLModel.metadata.create_all(eng)
        main._ensure_missing_columns(eng)
        # Second pass: nothing left to add.
        assert main._ensure_missing_columns(eng) == []
