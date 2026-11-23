from sqlalchemy import or_

from app.models.rental_booking import RentalBooking


def overlapping_rental_bookings(db, vehicle_id, start, end, now):
    return db.query(RentalBooking).filter(
        RentalBooking.vehicle_id == vehicle_id,
        RentalBooking.status.in_(("pending", "confirmed", "active", "in_progress")),
        or_(
            RentalBooking.status != "pending",
            RentalBooking.expires_at.is_(None),
            RentalBooking.expires_at > now,
        ),
        RentalBooking.pickup_at < end,
        RentalBooking.return_at > start,
    )
