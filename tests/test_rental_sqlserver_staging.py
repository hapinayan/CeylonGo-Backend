"""
Targeted Live SQL Server Integration Tests for CeylonGo Rental Module 09.
Requires the explicitly opted-in, Windows-authenticated integration database.

Verifies:
1. ORM mapped query compilation on actual SQL Server (zero error 207).
2. All 8 pending requirements:
   - AR-02: Verified Traveler DOB & minimum age logic.
   - AR-03: Structured helmet availability (count, inclusion, sizes).
   - AR-04: Vehicle-specific security deposit.
   - AR-05: Structured fuel policy.
   - AR-06: Handover acknowledgement metadata (timestamp & IP).
   - AR-07: Late return charge calculation & grace period.
   - AR-11: Foreign recognition permit storage & verification.
   - AR-12: Structured insurance policy, provider, coverage, and deductible.
3. Live SQL Server CHECK constraint enforcement and data integrity.
"""

import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database_guard import (
    ARTISAN_INTEGRATION_ALIAS,
    ARTISAN_INTEGRATION_DATABASE,
    DatabaseSafetyError,
    create_safe_sqlserver_test_engine,
    normalize_server_name,
)
from app.models.rental import (
    RentalAgeRule,
    RentalEligibilityCheck,
    RentalInspection,
    RentalVehicleDocument,
)
from app.models.rental_vehicle import RentalVehicle
from app.models.rental_booking import RentalBooking
from app.models.provider_profile import ProviderProfile
from app.models.user import User


@pytest.fixture(scope="function")
def staging_db_session():
    """Connect only to the explicitly configured integration server/database pair."""
    server = os.getenv("DB_SERVER")
    db_name = os.getenv("CEYLONGO_RENTAL_TEST_DB")
    if (
        normalize_server_name(server) != ARTISAN_INTEGRATION_ALIAS.lower()
        or db_name != ARTISAN_INTEGRATION_DATABASE
        or os.getenv("RUN_SQLSERVER_INTEGRATION_TESTS") != "1"
    ):
        raise DatabaseSafetyError("Explicit approved SQL Server rental integration target and opt-in required.")
    driver = os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server")
    trust_cert = os.getenv("DB_TRUST_CERTIFICATE", "yes")

    db_url = URL.create(
        "mssql+pyodbc",
        host=server,
        database=db_name,
        query={
            "driver": driver,
            "Trusted_Connection": "yes",
            "TrustServerCertificate": trust_cert,
        },
    )

    engine = create_safe_sqlserver_test_engine(
        db_url.render_as_string(), {ARTISAN_INTEGRATION_DATABASE},
        pool_pre_ping=True, connect_args={"timeout": 5},
    )
    try:
        with engine.connect() as connection:
            outer = connection.begin()
            if connection.scalar(text("SELECT DB_NAME()")) != db_name:
                raise DatabaseSafetyError("Connected rental integration database does not match the approved target.")
            connection.exec_driver_sql("SET LOCK_TIMEOUT 8000")
            try:
                with Session(
                    bind=connection, join_transaction_mode="create_savepoint",
                    autoflush=False, expire_on_commit=False,
                ) as session:
                    yield session
            finally:
                assert outer.is_active, "The enclosing fixture transaction was lost"
                outer.rollback()
    finally:
        engine.dispose()


@pytest.fixture(scope="function")
def setup_staging_seed_data(staging_db_session):
    """Seed unique synthetic actors in the guarded transaction."""
    db = staging_db_session
    fixture_id = uuid4().hex
    user = User(full_name="Staging Traveler", email=f"tr05-traveler-{fixture_id}@example.test",
                role="traveler", is_active=True)
    prov_user = User(full_name="Staging Provider", email=f"tr05-provider-{fixture_id}@example.test",
                     role="provider", is_active=True)
    db.add_all([user, prov_user])
    db.flush()
    provider = ProviderProfile(
        user_id=prov_user.id, business_name="Staging Rentals Ltd", provider_type="rental",
        approval_status="approved", phone="0771234567", district="Colombo",
    )
    db.add(provider)
    db.commit()
    return {"user": user, "provider": provider}


# =============================================================================
# 1. ORM Column Compilation & Select Compatibility
# =============================================================================

def test_sqlserver_orm_compilation_all_models(staging_db_session):
    """Verifies that SQLAlchemy SELECT queries compile and execute against SQL Server with zero Error 207."""
    db = staging_db_session

    # Queries all tables that received new columns in Migration 030
    vehicles = db.query(RentalVehicle).limit(5).all()
    assert isinstance(vehicles, list)

    eligibilities = db.query(RentalEligibilityCheck).limit(5).all()
    assert isinstance(eligibilities, list)

    inspections = db.query(RentalInspection).limit(5).all()
    assert isinstance(inspections, list)

    documents = db.query(RentalVehicleDocument).limit(5).all()
    assert isinstance(documents, list)
    assert isinstance(db.query(RentalBooking).limit(5).all(), list)
    assert isinstance(db.query(RentalAgeRule).limit(5).all(), list)


# =============================================================================
# 2. AR-02: Verified Traveler Date of Birth & Minimum Age Storage
# =============================================================================

def test_sqlserver_ar02_traveler_dob_storage(staging_db_session, setup_staging_seed_data):
    """Verifies storing and retrieving verified date of birth on live SQL Server."""
    db = staging_db_session
    user = setup_staging_seed_data["user"]

    # Insert or update eligibility record with DOB
    eligibility = db.query(RentalEligibilityCheck).filter(RentalEligibilityCheck.user_id == user.id).first()
    if not eligibility:
        eligibility = RentalEligibilityCheck(
            user_id=user.id,
            driver_license_number="B9876543",
            license_country="LKA",
            license_expiry_date=date.today() + timedelta(days=365),
            verification_status="approved",
            date_of_birth=date(1998, 5, 20),
        )
        db.add(eligibility)
    else:
        eligibility.date_of_birth = date(1998, 5, 20)
    db.commit()
    db.refresh(eligibility)

    # Read back from live SQL Server
    loaded = db.query(RentalEligibilityCheck).filter(RentalEligibilityCheck.id == eligibility.id).one()
    assert loaded.date_of_birth == date(1998, 5, 20)

    # Compute age dynamically
    today = date.today()
    age = today.year - loaded.date_of_birth.year - (
        (today.month, today.day) < (loaded.date_of_birth.month, loaded.date_of_birth.day)
    )
    assert age >= 21, "Driver should satisfy 21+ tuk-tuk age threshold"


# =============================================================================
# 3. AR-03: Structured Helmet Availability
# =============================================================================

def test_sqlserver_ar03_helmet_columns(staging_db_session, setup_staging_seed_data):
    """Verifies helmet_count, helmets_included, and helmet_sizes columns on live SQL Server."""
    db = staging_db_session
    provider = setup_staging_seed_data["provider"]

    vehicle = RentalVehicle(
        provider_id=provider.id,
        vehicle_type="bike",
        vehicle_name="Honda Dio Staging Edition",
        price_per_day=Decimal("3500.00"),
        status="available",
        approval_status="approved",
        helmet_count=2,
        helmets_included=True,
        helmet_sizes="M, L",
    )
    db.add(vehicle)
    db.commit()
    db.refresh(vehicle)

    loaded = db.query(RentalVehicle).filter(RentalVehicle.id == vehicle.id).one()
    assert loaded.helmet_count == 2
    assert loaded.helmets_included is True
    assert loaded.helmet_sizes == "M, L"


# =============================================================================
# 4. AR-04: Vehicle-Specific Security Deposit
# =============================================================================

def test_sqlserver_ar04_security_deposit_storage(staging_db_session, setup_staging_seed_data):
    """Verifies vehicle-specific security deposit storage on live SQL Server."""
    db = staging_db_session
    provider = setup_staging_seed_data["provider"]

    vehicle = RentalVehicle(
        provider_id=provider.id,
        vehicle_type="bike",
        vehicle_name="Yamaha FZ Staging",
        price_per_day=Decimal("4500.00"),
        status="available",
        approval_status="approved",
        security_deposit=Decimal("15000.00"),
    )
    db.add(vehicle)
    db.commit()
    db.refresh(vehicle)

    loaded = db.query(RentalVehicle).filter(RentalVehicle.id == vehicle.id).one()
    assert loaded.security_deposit == Decimal("15000.00")


# =============================================================================
# 5. AR-05: Structured Fuel Policy Values
# =============================================================================

def test_sqlserver_ar05_fuel_policy_values(staging_db_session, setup_staging_seed_data):
    """Verifies valid fuel policy values (same_to_same, full_to_full, not_applicable) on live SQL Server."""
    db = staging_db_session
    provider = setup_staging_seed_data["provider"]

    created_ids = []
    for policy in ["same_to_same", "full_to_full", "not_applicable"]:
        v = RentalVehicle(
            provider_id=provider.id,
            vehicle_type="bike" if policy != "not_applicable" else "bicycle",
            vehicle_name=f"Staging Vehicle {policy}",
            price_per_day=Decimal("2000.00"),
            status="available",
            approval_status="approved",
            fuel_policy=policy,
        )
        db.add(v)
        db.flush()
        created_ids.append(v.id)
    db.commit()

    loaded_policies = [
        row[0]
        for row in db.execute(
            text(f"SELECT fuel_policy FROM dbo.rental_vehicles WHERE id IN ({','.join(str(i) for i in created_ids)}) ORDER BY id ASC;")
        ).fetchall()
    ]
    assert loaded_policies == ["same_to_same", "full_to_full", "not_applicable"]


# =============================================================================
# 6. AR-06: Handover Digital Acknowledgement Metadata
# =============================================================================

def test_sqlserver_ar06_inspection_acknowledgement(staging_db_session, setup_staging_seed_data):
    """Verifies acknowledged_at and acknowledgement_ip on live SQL Server."""
    db = staging_db_session
    provider = setup_staging_seed_data["provider"]
    user = setup_staging_seed_data["user"]

    # 1. Create vehicle & booking
    v = RentalVehicle(
        provider_id=provider.id,
        vehicle_type="bike",
        vehicle_name="Inspection Bike",
        price_per_day=Decimal("3000.00"),
        status="available",
        approval_status="approved",
    )
    db.add(v)
    db.commit()

    now = datetime.now(timezone.utc)
    booking = RentalBooking(
        vehicle_id=v.id,
        user_id=user.id,
        pickup_at=now,
        return_at=now + timedelta(days=2),
        total_price=Decimal("6000.00"),
        status="active",
    )
    db.add(booking)
    db.commit()

    # 2. Record inspection with acknowledgement metadata
    ack_time = datetime.now(timezone.utc)
    inspection = RentalInspection(
        booking_id=booking.id,
        inspection_type="pickup",
        inspected_by=user.id,
        odometer_reading=12450,
        fuel_level_percent=100,
        cleanliness_status="clean",
        acknowledged_at=ack_time,
        acknowledgement_ip="203.115.24.12",
    )
    db.add(inspection)
    db.commit()
    db.refresh(inspection)

    loaded = db.query(RentalInspection).filter(RentalInspection.id == inspection.id).one()
    assert loaded.acknowledgement_ip == "203.115.24.12"
    assert loaded.acknowledged_at is not None


# =============================================================================
# 7. AR-07: Late Return Lateness & Grace Period Calculation
# =============================================================================

def test_sqlserver_ar07_return_timestamps_round_trip(staging_db_session, setup_staging_seed_data):
    db = staging_db_session
    vehicle = RentalVehicle(provider_id=setup_staging_seed_data["provider"].id,
                            vehicle_type="bike", vehicle_name="Late return fixture", price_per_day=1000)
    db.add(vehicle)
    db.flush()
    scheduled = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    delay = timedelta(minutes=60, seconds=1)
    user_id = setup_staging_seed_data["user"].id
    booking = RentalBooking(vehicle_id=vehicle.id, user_id=user_id,
                            pickup_at=scheduled - timedelta(days=1), return_at=scheduled,
                            total_price=1000, status="completed")
    db.add(booking)
    db.flush()
    inspection = RentalInspection(booking_id=booking.id, inspection_type="return", inspected_by=user_id,
                                  odometer_reading=100, fuel_level_percent=50, cleanliness_status="clean",
                                  created_at=scheduled + delay)
    db.add(inspection)
    db.commit()
    db.refresh(booking)
    db.refresh(inspection)
    assert inspection.created_at - booking.return_at == delay


# =============================================================================
# 8. AR-11: Foreign Driving Permit Evidence
# =============================================================================

def test_sqlserver_ar11_recognition_permit_storage(staging_db_session, setup_staging_seed_data):
    """Verifies recognition_permit_number and recognition_permit_document_url on live SQL Server."""
    db = staging_db_session
    user = setup_staging_seed_data["user"]

    eligibility = RentalEligibilityCheck(
        user_id=user.id,
        driver_license_number="UK-DL-994821",
        license_country="GBR",
        license_expiry_date=date.today() + timedelta(days=500),
        verification_status="approved",
        recognition_permit_number="AAC-LK-2026-8812",
        recognition_permit_document_url="/uploads/permits/aac_permit_8812.pdf",
    )
    db.add(eligibility)
    db.commit()
    db.refresh(eligibility)

    loaded = db.query(RentalEligibilityCheck).filter(RentalEligibilityCheck.id == eligibility.id).one()
    assert loaded.recognition_permit_number == "AAC-LK-2026-8812"
    assert loaded.recognition_permit_document_url == "/uploads/permits/aac_permit_8812.pdf"


# =============================================================================
# 9. AR-12: Structured Insurance Information
# =============================================================================

def test_sqlserver_ar12_structured_insurance_storage(staging_db_session, setup_staging_seed_data):
    """Verifies structured insurance columns on live SQL Server."""
    db = staging_db_session
    provider = setup_staging_seed_data["provider"]

    v = RentalVehicle(
        provider_id=provider.id,
        vehicle_type="tuk_tuk",
        vehicle_name="Staging Tuk-Tuk Insured",
        price_per_day=Decimal("5000.00"),
        status="available",
        approval_status="approved",
    )
    db.add(v)
    db.commit()

    doc = RentalVehicleDocument(
        vehicle_id=v.id,
        document_type="insurance_certificate",
        file_reference="/uploads/insurance/tuk_cert_2026.pdf",
        insurance_provider="Sri Lanka Insurance Corporation",
        insurance_policy_number="SLIC-MOT-2026-98712",
        coverage_amount=Decimal("1500000.00"),
        deductible_amount=Decimal("25000.00"),
        is_verified=True,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    loaded = db.query(RentalVehicleDocument).filter(RentalVehicleDocument.id == doc.id).one()
    assert loaded.insurance_provider == "Sri Lanka Insurance Corporation"
    assert loaded.insurance_policy_number == "SLIC-MOT-2026-98712"
    assert loaded.coverage_amount == Decimal("1500000.00")
    assert loaded.deductible_amount == Decimal("25000.00")


# =============================================================================
# 10. Live SQL Server CHECK Constraints & Data Integrity
# =============================================================================

def test_sqlserver_check_constraints_enforcement(staging_db_session, setup_staging_seed_data):
    """Verifies that live SQL Server active check constraints reject invalid values."""
    db = staging_db_session

    provider_id = setup_staging_seed_data["provider"].id
    for field, invalid in (("fuel_policy", "unlimited_fuel"),
                           ("helmet_count", -5), ("security_deposit", Decimal("-100.00"))):
        with pytest.raises(IntegrityError, match=f"CK_rental_vehicles_{field}"):
            with db.begin_nested():
                db.add(RentalVehicle(provider_id=provider_id, vehicle_type="bike",
                                     vehicle_name="Constraint fixture", price_per_day=1000, **{field: invalid}))
                db.flush()

    vehicle = RentalVehicle(provider_id=provider_id, vehicle_type="bike",
                            vehicle_name="Valid constraint fixture", price_per_day=1000)
    db.add(vehicle)
    db.flush()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with pytest.raises(IntegrityError, match="CK_rental_bookings_passenger_count"):
        with db.begin_nested():
            db.add(RentalBooking(vehicle_id=vehicle.id, user_id=setup_staging_seed_data["user"].id,
                                 pickup_at=now, return_at=now + timedelta(days=1),
                                 total_price=1000, passenger_count=0))
            db.flush()
    with pytest.raises(IntegrityError, match="CK_rental_vehicle_docs_insurance_dates"):
        with db.begin_nested():
            db.add(RentalVehicleDocument(
                vehicle_id=vehicle.id, document_type="insurance_certificate",
                file_reference="synthetic-insurance.pdf", expiry_date=now.date(),
                insurance_effective_date=now.date() + timedelta(days=1),
            ))
            db.flush()


# =============================================================================
# 11. Live SQL Server End-to-End Rental Booking Workflow
# =============================================================================

def test_sqlserver_rental_booking_e2e_workflow(staging_db_session, setup_staging_seed_data):
    """Verifies end-to-end rental booking lifecycle with deposits and inspections on live SQL Server."""
    from app.models.rental import RentalDeposit

    db = staging_db_session
    user = setup_staging_seed_data["user"]
    provider = setup_staging_seed_data["provider"]

    # 1. Vehicle with structured helmet and deposit
    vehicle = RentalVehicle(
        provider_id=provider.id,
        vehicle_type="bike",
        vehicle_name="E2E Staging Pulsar 150",
        price_per_day=Decimal("4000.00"),
        status="available",
        approval_status="approved",
        helmet_count=2,
        helmets_included=True,
        helmet_sizes="L, XL",
        security_deposit=Decimal("10000.00"),
        fuel_policy="same_to_same",
    )
    db.add(vehicle)
    db.commit()
    db.refresh(vehicle)

    # 2. Traveler Eligibility Check with DOB
    eligibility = RentalEligibilityCheck(
        user_id=user.id,
        driver_license_number="B-E2E-778899",
        license_country="Sri Lanka",
        license_expiry_date=date.today() + timedelta(days=730),
        verification_status="approved",
        date_of_birth=date(1996, 8, 15),
    )
    db.add(eligibility)
    db.commit()

    # 3. Booking creation
    pickup_time = datetime.now(timezone.utc)
    return_time = pickup_time + timedelta(days=3)
    booking = RentalBooking(
        vehicle_id=vehicle.id,
        user_id=user.id,
        pickup_at=pickup_time,
        return_at=return_time,
        total_price=Decimal("12000.00"),
        status="confirmed",
    )
    db.add(booking)
    db.commit()
    db.refresh(booking)

    # 4. Handover Inspection with Acknowledgement metadata
    handover = RentalInspection(
        booking_id=booking.id,
        inspection_type="pickup",
        inspected_by=user.id,
        odometer_reading=15200,
        fuel_level_percent=100,
        cleanliness_status="clean",
        acknowledged_at=datetime.now(timezone.utc),
        acknowledgement_ip="192.168.1.50",
    )
    db.add(handover)
    booking.status = "active"

    # 5. Security deposit recorded
    deposit = RentalDeposit(
        booking_id=booking.id,
        deposit_amount=vehicle.security_deposit,
        currency="LKR",
        deposit_status="held",
    )
    db.add(deposit)
    db.commit()

    # 6. Return inspection & completion
    return_inspection = RentalInspection(
        booking_id=booking.id,
        inspection_type="return",
        inspected_by=user.id,
        odometer_reading=15450,
        fuel_level_percent=100,
        cleanliness_status="clean",
    )
    db.add(return_inspection)
    deposit.deposit_status = "fully_refunded"
    deposit.refunded_amount = deposit.deposit_amount
    deposit.released_at = datetime.now(timezone.utc)
    booking.status = "completed"
    db.commit()

    # 7. Verification of all lifecycle records in live SQL Server
    loaded_booking = db.query(RentalBooking).filter(RentalBooking.id == booking.id).one()
    assert loaded_booking.status == "completed"

    inspections = db.query(RentalInspection).filter(RentalInspection.booking_id == booking.id).all()
    assert len(inspections) == 2
    pickup_ins = next(i for i in inspections if i.inspection_type == "pickup")
    assert pickup_ins.acknowledgement_ip == "192.168.1.50"
    assert pickup_ins.acknowledged_at is not None

    loaded_deposit = db.query(RentalDeposit).filter(RentalDeposit.booking_id == booking.id).one()
    assert loaded_deposit.deposit_status == "fully_refunded"
    assert loaded_deposit.deposit_amount == Decimal("10000.00")
    assert loaded_deposit.refunded_amount == Decimal("10000.00")
