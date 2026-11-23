from fastapi import HTTPException

from app.models.rental import RentalAgeRule, RentalEligibilityCheck

DEFAULT_RENTAL_MINIMUM_AGES = {"bike": 18, "bicycle": 16, "tuk_tuk": 21}
FOREIGN_TUK_TUK_PERMIT_MESSAGE = (
    "Foreign travelers renting a tuk-tuk require an approved International Driving Permit (IDP) "
    "or Sri Lankan recognition permit on file."
)


def rental_minimum_age(db, vehicle_type: str) -> tuple[int, bool]:
    rule = db.query(RentalAgeRule).filter_by(vehicle_type=vehicle_type).one_or_none()
    if rule is not None:
        return rule.minimum_age, rule.minimum_age > 0
    return DEFAULT_RENTAL_MINIMUM_AGES[vehicle_type], vehicle_type != "bicycle"


def has_foreign_tuk_tuk_permit(eligibility: RentalEligibilityCheck) -> bool:
    return any(
        value and value.strip()
        for value in (eligibility.idp_number, eligibility.recognition_permit_number)
    )


def verify_rental_handover_driver(db, booking, vehicle) -> str:
    if vehicle.vehicle_type == "bicycle":
        return "Exempt (Bicycle)"
    eligibility = db.query(RentalEligibilityCheck).filter_by(user_id=booking.user_id).first()
    if not eligibility or eligibility.verification_status != "approved":
        raise HTTPException(400, "Handover cannot proceed: Renter driving license verification is missing or not approved.")
    if eligibility.license_expiry_date < booking.return_at.date():
        raise HTTPException(400, f"Handover cannot proceed: Renter driving license expired on {eligibility.license_expiry_date}.")
    if (
        vehicle.vehicle_type == "tuk_tuk"
        and eligibility.license_country.strip().lower() != "sri lanka"
        and not has_foreign_tuk_tuk_permit(eligibility)
    ):
        raise HTTPException(400, f"Handover cannot proceed: {FOREIGN_TUK_TUK_PERMIT_MESSAGE}")
    return f"Verified (License: {eligibility.license_country} - Approved)"
