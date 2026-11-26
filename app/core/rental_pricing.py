from datetime import datetime


def rental_days(pickup: datetime, returned: datetime) -> int:
    """Charge one rental day per started 24-hour interval."""
    elapsed = returned - pickup
    return max(1, elapsed.days + bool(elapsed.seconds or elapsed.microseconds))
