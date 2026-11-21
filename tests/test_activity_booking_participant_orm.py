"""
Tests for ActivityBookingParticipant ORM & Database Schema Alignment.

Verifies:
1. All 9 expected columns are mapped in SQLAlchemy.
2. Obsolete/hallucinated column `id_or_passport` is completely removed.
3. Column string lengths match SQL Server definitions (150, 30, 1000).
4. Nullable properties precisely match the physical database table.
5. `waiver_signed` default behavior and server default are correct.
6. Check constraint name and definition are aligned with SQL Server (`CK_activity_booking_participants_age`).
7. Model relationships to ActivityBooking remain functional with proper cascading.
8. SQLAlchemy correctly compiles DDL, INSERT, and SELECT queries under the MSSQL dialect.
9. Safe, read-only live schema alignment check against CeylonGoDB_Development if accessible.
"""

from datetime import datetime
import pytest
from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    String,
    Unicode,
    insert,
    select,
)
from sqlalchemy.dialects import mssql
from sqlalchemy.schema import CreateTable

from app.models.activity_booking import ActivityBooking, ActivityBookingParticipant


def test_activity_booking_participant_columns_presence_and_absence():
    """Verify exact column inventory on ActivityBookingParticipant."""
    table = ActivityBookingParticipant.__table__
    col_names = set(table.columns.keys())

    expected_columns = {
        "id",
        "activity_booking_id",
        "full_name",
        "age",
        "emergency_contact_name",
        "emergency_contact_phone",
        "special_requirements",
        "waiver_signed",
        "created_at",
    }

    assert col_names == expected_columns, (
        f"Columns mismatch: extra={col_names - expected_columns}, "
        f"missing={expected_columns - col_names}"
    )

    # Ensure removed column is not present
    assert "id_or_passport" not in col_names
    assert not hasattr(ActivityBookingParticipant, "id_or_passport")


def test_activity_booking_participant_column_types_and_lengths():
    """Verify column types and string lengths match SQL Server schema."""
    table = ActivityBookingParticipant.__table__

    # id: INT IDENTITY PK
    id_col = table.columns["id"]
    assert isinstance(id_col.type, Integer)
    assert id_col.primary_key is True

    # activity_booking_id: INT FK
    booking_id_col = table.columns["activity_booking_id"]
    assert isinstance(booking_id_col.type, Integer)
    assert len(booking_id_col.foreign_keys) == 1
    fk = list(booking_id_col.foreign_keys)[0]
    assert fk.target_fullname == "activity_bookings.id"
    assert fk.ondelete.upper() == "CASCADE"

    # full_name: NVARCHAR(150)
    full_name_col = table.columns["full_name"]
    assert isinstance(full_name_col.type, (Unicode, String))
    assert full_name_col.type.length == 150

    # age: INT
    age_col = table.columns["age"]
    assert isinstance(age_col.type, Integer)

    # emergency_contact_name: NVARCHAR(150)
    ec_name_col = table.columns["emergency_contact_name"]
    assert isinstance(ec_name_col.type, (Unicode, String))
    assert ec_name_col.type.length == 150

    # emergency_contact_phone: NVARCHAR(30)
    ec_phone_col = table.columns["emergency_contact_phone"]
    assert isinstance(ec_phone_col.type, (Unicode, String))
    assert ec_phone_col.type.length == 30

    # special_requirements: NVARCHAR(1000)
    spec_col = table.columns["special_requirements"]
    assert isinstance(spec_col.type, (Unicode, String))
    assert spec_col.type.length == 1000

    # waiver_signed: BIT (Boolean)
    waiver_col = table.columns["waiver_signed"]
    assert isinstance(waiver_col.type, Boolean)

    # created_at: DATETIME2
    created_col = table.columns["created_at"]
    assert isinstance(created_col.type, DateTime)


def test_activity_booking_participant_nullability():
    """Verify nullability matches database schema exactly."""
    table = ActivityBookingParticipant.__table__

    assert table.columns["id"].nullable is False
    assert table.columns["activity_booking_id"].nullable is False
    assert table.columns["full_name"].nullable is False
    assert table.columns["age"].nullable is True
    assert table.columns["emergency_contact_name"].nullable is True
    assert table.columns["emergency_contact_phone"].nullable is True
    assert table.columns["special_requirements"].nullable is True
    assert table.columns["waiver_signed"].nullable is False
    assert table.columns["created_at"].nullable is False


def test_activity_booking_participant_defaults():
    """Verify default values on waiver_signed and created_at."""
    table = ActivityBookingParticipant.__table__

    # Server default on waiver_signed
    waiver_col = table.columns["waiver_signed"]
    assert waiver_col.server_default is not None
    assert "0" in str(waiver_col.server_default.arg)

    # Python column default on waiver_signed
    assert waiver_col.default is not None
    assert waiver_col.default.arg is False

    # Instantiation with default argument
    participant = ActivityBookingParticipant(full_name="Kasun Silva", waiver_signed=False)
    assert participant.waiver_signed is False

    # Server default on created_at
    created_col = table.columns["created_at"]
    assert created_col.server_default is not None
    assert "sysutcdatetime" in str(created_col.server_default.arg).lower()


def test_activity_booking_participant_check_constraints():
    """Verify check constraint name and definition match SQL Server."""
    table = ActivityBookingParticipant.__table__
    constraints = {c.name: c for c in table.constraints if c.name and c.name.startswith("CK_")}

    assert "CK_activity_booking_participants_age" in constraints
    assert "CK_activity_participants_age" not in constraints

    age_constraint = constraints["CK_activity_booking_participants_age"]
    sql_text = str(age_constraint.sqltext)
    assert "age IS NULL" in sql_text
    assert "age >= 0" in sql_text


def test_activity_booking_participant_relationships():
    """Verify two-way relationship between ActivityBooking and ActivityBookingParticipant."""
    # Child -> Parent
    assert hasattr(ActivityBookingParticipant, "booking")
    # Parent -> Child
    assert hasattr(ActivityBooking, "participants_list")

    # Verify backref cascade options
    mapper = ActivityBooking.__mapper__
    prop = mapper.relationships["participants_list"]
    assert prop.cascade.delete_orphan is True
    assert prop.lazy == "select"


def test_activity_booking_participant_mssql_compilation():
    """Verify that SQLAlchemy generates valid MSSQL DDL, INSERT, and SELECT queries."""
    table = ActivityBookingParticipant.__table__
    dialect = mssql.dialect()

    # 1. DDL generation
    ddl = str(CreateTable(table).compile(dialect=dialect))
    assert "NVARCHAR(150)" in ddl
    assert "NVARCHAR(30)" in ddl
    assert "NVARCHAR(1000)" in ddl
    assert "BIT NOT NULL" in ddl
    assert "DATETIME2 NOT NULL" in ddl
    assert "CK_activity_booking_participants_age" in ddl
    assert "age >= 0" in ddl
    assert "ON DELETE CASCADE" in ddl
    assert "id_or_passport" not in ddl

    # 2. INSERT statement compilation
    stmt = insert(ActivityBookingParticipant).values(
        activity_booking_id=10,
        full_name="Kasun Silva",
        age=28,
        emergency_contact_name="Nimal Silva",
        emergency_contact_phone="0719876543",
        special_requirements="None",
        waiver_signed=True,
    )
    compiled_insert = str(stmt.compile(dialect=dialect))
    assert "INSERT INTO activity_booking_participants" in compiled_insert
    assert "emergency_contact_name" in compiled_insert
    assert "emergency_contact_phone" in compiled_insert
    assert "special_requirements" in compiled_insert
    assert "waiver_signed" in compiled_insert
    assert "id_or_passport" not in compiled_insert

    # 3. SELECT statement compilation
    query = select(ActivityBookingParticipant).where(
        ActivityBookingParticipant.activity_booking_id == 10
    )
    compiled_select = str(query.compile(dialect=dialect))
    assert "SELECT activity_booking_participants.id" in compiled_select
    assert "activity_booking_participants.emergency_contact_name" in compiled_select
    assert "activity_booking_participants.waiver_signed" in compiled_select
    assert "id_or_passport" not in compiled_select


def test_activity_booking_participant_live_db_readonly_verification():
    """
    Read-only verification directly querying sys.columns on CeylonGoDB_Development
    if the local SQL Server instance is reachable.
    Does NOT write any data to the database.
    """
    import pyodbc

    conn_str = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        "SERVER=.\\SQLEXPRESS;"
        "DATABASE=CeylonGoDB_Development;"
        "Trusted_Connection=yes;"
        "TrustServerCertificate=yes;"
    )

    try:
        conn = pyodbc.connect(conn_str, timeout=3)
    except Exception:
        pytest.skip("Local SQLEXPRESS CeylonGoDB_Development is not reachable; skipping live check.")

    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            c.name,
            t.name AS type_name,
            c.max_length,
            c.is_nullable
        FROM sys.columns c
        JOIN sys.types t ON c.user_type_id = t.user_type_id
        WHERE c.object_id = OBJECT_ID('dbo.activity_booking_participants')
    """)
    rows = cursor.fetchall()
    conn.close()

    assert len(rows) == 9, f"Expected 9 columns in live DB table, found {len(rows)}"

    live_columns = {r[0]: {"type": r[1], "max_length": r[2], "is_nullable": bool(r[3])} for r in rows}

    # Verify no id_or_passport in live DB
    assert "id_or_passport" not in live_columns

    # Verify ORM model columns exactly match live DB table
    table = ActivityBookingParticipant.__table__
    for col_name, live_meta in live_columns.items():
        assert col_name in table.columns, f"Live column {col_name} missing from ORM model"
        orm_col = table.columns[col_name]
        assert orm_col.nullable == live_meta["is_nullable"], (
            f"Nullability mismatch for {col_name}: "
            f"ORM={orm_col.nullable}, DB={live_meta['is_nullable']}"
        )

        # Length check for NVARCHAR columns (max_length in sys.columns is in bytes, so chars = max_length / 2)
        if live_meta["type"] == "nvarchar":
            expected_chars = live_meta["max_length"] // 2
            assert orm_col.type.length == expected_chars, (
                f"Length mismatch for {col_name}: "
                f"ORM={orm_col.type.length}, DB={expected_chars}"
            )
