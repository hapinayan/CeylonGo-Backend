from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

CAPTURED = ("paid", "completed")
EXECUTED_REFUNDS = ("processed", "completed")
RESERVED_REFUNDS = ("pending", "approved", *EXECUTED_REFUNDS)
RENTAL_BOOKING_REFERENCE_TYPES = ("rental_vehicle", "rental")


def lock_booking_resource(db: Session, resource: str) -> None:
    """Serialize writers on SQL Server until their enclosing transaction ends."""
    if db.get_bind().dialect.name != "mssql":
        return
    result = db.execute(
        text("""
            DECLARE @result INT;
            EXEC @result = sp_getapplock
                @Resource = :resource, @LockMode = 'Exclusive',
                @LockOwner = 'Transaction', @LockTimeout = 5000;
            SELECT @result;
        """),
        {"resource": resource},
    ).scalar()
    if result is None or result < 0:
        db.rollback()
        raise HTTPException(503, "Booking service is busy. Please retry.")


def capture_status(db: Session) -> str:
    return "completed" if db.get_bind().dialect.name == "mssql" else "paid"


def executed_refund_status(db: Session) -> str:
    return "processed" if db.get_bind().dialect.name == "mssql" else "completed"
