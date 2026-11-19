from app.schemas.weather import ForecastInterval


def travel_advice(intervals: list[ForecastInterval]) -> tuple[list[str], list[str]]:
    recommendations: list[str] = []
    cautions: list[str] = []
    codes = {interval.condition.code // 100 for interval in intervals}
    thunder = 2 in codes
    rain = bool(codes & {3, 5}) or any(
        (interval.rain_probability_percent or 0) >= 60 for interval in intervals
    )
    wind = max(interval.wind_speed_kmh for interval in intervals) >= 40
    heat = max(interval.max_temperature_c for interval in intervals) >= 35

    if thunder:
        cautions.append(
            "Thunderstorms are possible. Consider postponing exposed hikes."
        )
    if rain:
        cautions.append("Rain is possible. Trails and steps may be slippery.")
        recommendations.append(
            "Pack rain gear and check trail conditions with your guide."
        )
    if wind:
        cautions.append(
            "Strong winds are possible. Check with boat and activity operators."
        )
    if heat:
        cautions.append("High temperatures are possible. Plan shade and water breaks.")
        recommendations.append(
            "Consider early morning outings and carry drinking water."
        )
    if cautions:
        recommendations.append(
            "Keep a museum, gallery or indoor workshop as an alternative."
        )
    else:
        recommendations.append(
            "Consider an outdoor outing during a favorable interval and "
            "recheck conditions."
        )
    recommendations.append(
        "For eco-tourism, check park and trail access with the operator before "
        "departing."
    )
    return recommendations, cautions
