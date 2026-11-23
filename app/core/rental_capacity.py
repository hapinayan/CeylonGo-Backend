TUK_TUK_MIN_PASSENGERS = 1


def valid_tuk_tuk_seating_capacity(total_seats: int | None) -> bool:
    return total_seats in (3, 4)


def tuk_tuk_passenger_limit(total_seats: int) -> int | None:
    if valid_tuk_tuk_seating_capacity(total_seats):
        return total_seats - 1
    return None
