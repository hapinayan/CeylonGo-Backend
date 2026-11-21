
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.activity_slot import ActivitySlot


def reserve_activity_capacity(
    db: Session,
    slot_id: int,
    participants: int,
) -> bool:
    """
    Atomically reserve participants in an active slot.

    The caller must commit or roll back the transaction.
    """

    if slot_id <= 0:
        raise ValueError("slot_id must be positive")

    if participants <= 0:
        raise ValueError("participants must be positive")

    statement = (
        update(ActivitySlot)
        .where(
            ActivitySlot.id == slot_id,
            ActivitySlot.is_active == True,
            ActivitySlot.reserved_count
            <= ActivitySlot.capacity - participants,
        )
        .values(
            reserved_count=(
                ActivitySlot.reserved_count + participants
            )
        )
        .execution_options(synchronize_session=False)
    )

    result = db.execute(statement)

    return result.rowcount == 1


def release_activity_capacity(
    db: Session,
    slot_id: int,
    participants: int,
) -> bool:
    """
    Atomically decrease reserved_count.

    IMPORTANT:
    This helper does not guarantee exactly-once release
    by itself. The cancellation service must first
    transition the booking from an active state to
    cancelled within the same database transaction.

    The caller must commit or roll back the transaction.
    """

    if slot_id <= 0:
        raise ValueError("slot_id must be positive")

    if participants <= 0:
        raise ValueError("participants must be positive")

    statement = (
        update(ActivitySlot)
        .where(
            ActivitySlot.id == slot_id,
            ActivitySlot.reserved_count >= participants,
        )
        .values(
            reserved_count=(
                ActivitySlot.reserved_count - participants
            )
        )
        .execution_options(synchronize_session=False)
    )

    result = db.execute(statement)

    return result.rowcount == 1


def update_slot_capacity(
    db: Session,
    slot_id: int,
    new_capacity: int,
) -> bool:
    """
    Atomically update capacity of a slot, ensuring new_capacity >= reserved_count.

    The caller must commit or roll back the transaction.
    """
    if slot_id <= 0:
        raise ValueError("slot_id must be positive")

    if new_capacity <= 0:
        raise ValueError("new_capacity must be positive")

    statement = (
        update(ActivitySlot)
        .where(
            ActivitySlot.id == slot_id,
            ActivitySlot.reserved_count <= new_capacity,
        )
        .values(capacity=new_capacity)
        .execution_options(synchronize_session=False)
    )

    result = db.execute(statement)

    return result.rowcount == 1
