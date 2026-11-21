from dataclasses import dataclass
from math import asin, cos, degrees, pi, radians, sin

from sqlalchemy import (
    Float,
    case,
    func,
    literal,
    null,
    or_,
    select,
    true,
    union_all,
)
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement

from app.core.publication import public_homestay
from app.models.activity import Activity
from app.models.activity_image import ActivityImage
from app.models.artisan_listing import ArtisanListing
from app.models.eco_experience import EcoExperience, EcoExperienceImage
from app.models.homestay import Homestay, HomestayImage
from app.models.provider_profile import ProviderProfile
from app.models.rental import RentalLocation
from app.models.rental_vehicle import RentalVehicle
from app.models.shared import ListingReference, Review
from app.models.tour_guide import TourGuide, TourGuideImage
from app.models.user import User
from app.schemas.interactive_map import MapCategory, MapLocation, MapPage, MapPoint


@dataclass(frozen=True)
class LocationSource:
    model: type
    latitude: str = "latitude"
    longitude: str = "longitude"


SOURCES = {
    "homestay": LocationSource(Homestay),
    "activity": LocationSource(Activity),
    "eco_experience": LocationSource(EcoExperience),
    "tour_guide": LocationSource(TourGuide, "base_latitude", "base_longitude"),
    "artisan": LocationSource(ArtisanListing),
    "rental": LocationSource(RentalLocation),
}

CATEGORIES = [
    MapCategory(id="homestay", label="Homestays"),
    MapCategory(id="adventure", label="Adventure activities"),
    MapCategory(id="cultural", label="Cultural experiences"),
    MapCategory(id="local_activity", label="Local activities"),
    MapCategory(id="community_activity", label="Community activities"),
    MapCategory(id="eco_tourism", label="Eco-tourism"),
    MapCategory(id="tour_guide", label="Local tour guides"),
    MapCategory(id="artisan", label="Artisan shops"),
    MapCategory(id="bike_rental", label="Bike rental"),
    MapCategory(id="bicycle_rental", label="Bicycle rental"),
    MapCategory(
        id="local_business",
        label="Local businesses",
        available=False,
        reason="There is no separate public local-business listing catalog.",
    ),
    MapCategory(
        id="tourist_attraction",
        label="Tourist attractions",
        available=False,
        reason="There is no standalone tourist-attraction catalog.",
    ),
]

DETAIL_PATHS = {
    "homestay": "/api/v1/homestays",
    "eco_experience": "/api/v1/eco-experiences",
    "artisan": "/api/v1/artisan-listings",
    "rental": "/api/v1/rental-vehicles",
}


def first_image(model, parent, listing_id, *order):
    return (
        select(model.image_url)
        .where(parent == listing_id)
        .order_by(*order, model.id)
        .limit(1)
        .scalar_subquery()
    )


def published_rating(module, listing_id):
    return (
        select(func.avg(Review.rating))
        .join(ListingReference, ListingReference.id == Review.listing_reference_id)
        .where(
            ListingReference.module_type == module,
            ListingReference.target_id == listing_id,
            Review.is_published == true(),
        )
        .scalar_subquery()
    )


def projection(module, model, name, category, position, **fields):
    def expression(value):
        return literal(value) if isinstance(value, str) else value

    return select(
        literal(module).label("module_type"),
        model.id.label("listing_id"),
        name.label("name"),
        expression(category).label("category"),
        position[0].label("latitude"),
        position[1].label("longitude"),
        *(
            expression(fields.get(key, null())).label(key)
            for key in (
                "address",
                "city",
                "district",
                "image_url",
                "rating",
                "price",
                "currency",
            )
        ),
    ).select_from(model)


def with_provider(query, provider_id):
    return (
        query.join(ProviderProfile, ProviderProfile.id == provider_id)
        .join(User, User.id == ProviderProfile.user_id)
        .where(
            ProviderProfile.approval_status == "approved", User.is_active == true(),
            User.role == "provider",
        )
    )


def source_queries():
    home, activity, eco, guide, artisan, vehicle, rental = (
        Homestay,
        Activity,
        EcoExperience,
        TourGuide,
        ArtisanListing,
        RentalVehicle,
        RentalLocation,
    )
    activity_category = case(
        (activity.category == "local", "local_activity"),
        (activity.category == "community", "community_activity"),
        else_=activity.category,
    )
    yield (
        projection(
            "homestay",
            home,
            home.name,
            "homestay",
            (home.latitude, home.longitude),
            address=home.address,
            city=home.city,
            district=home.district,
            image_url=first_image(
                HomestayImage,
                HomestayImage.homestay_id,
                home.id,
                HomestayImage.is_primary.desc(),
            ),
            rating=published_rating("homestay", home.id),
            price=home.price_per_night,
            currency="LKR",
        ).where(public_homestay())
    )
    yield with_provider(
        projection(
            "activity",
            activity,
            activity.title,
            activity_category,
            (activity.latitude, activity.longitude),
            city=activity.city,
            district=activity.district,
            address=activity.meeting_point,
            image_url=first_image(
                ActivityImage,
                ActivityImage.activity_id,
                activity.id,
                ActivityImage.is_cover.desc(),
                ActivityImage.display_order,
            ),
            rating=published_rating("activity", activity.id),
            price=activity.price,
            currency=activity.currency,
        ),
        activity.provider_profile_id,
    ).where(
        activity.approval_status == "approved",
        activity.is_active == true(),
    )
    yield with_provider(
        projection(
            "eco_experience",
            eco,
            eco.title,
            "eco_tourism",
            (eco.latitude, eco.longitude),
            city=eco.city,
            district=eco.district,
            address=eco.address,
            image_url=first_image(
                EcoExperienceImage,
                EcoExperienceImage.eco_experience_id,
                eco.id,
                EcoExperienceImage.is_primary.desc(),
                EcoExperienceImage.sort_order,
            ),
            rating=published_rating("eco_experience", eco.id),
            price=eco.base_price,
            currency=eco.currency,
        ),
        eco.provider_profile_id,
    ).where(
        eco.listing_status == "approved",
        eco.is_published == true(),
    )
    yield with_provider(
        projection(
            "tour_guide",
            guide,
            ProviderProfile.business_name,
            "tour_guide",
            (guide.base_latitude, guide.base_longitude),
            city=guide.base_city,
            district=guide.base_district,
            rating=case(
                (guide.review_count > 0, guide.rating_average),
                else_=null(),
            ),
            price=guide.daily_rate,
            currency=guide.currency,
            image_url=first_image(
                TourGuideImage,
                TourGuideImage.tour_guide_id,
                guide.id,
                TourGuideImage.is_profile.desc(),
                TourGuideImage.display_order,
            ),
        ),
        guide.provider_profile_id,
    ).where(
        guide.approval_status == "approved",
        guide.is_active == true(),
    )
    yield with_provider(
        projection(
            "artisan",
            artisan,
            artisan.title,
            "artisan",
            (artisan.latitude, artisan.longitude),
            city=artisan.city,
            district=artisan.district,
            address=artisan.address,
            image_url=artisan.cover_image_url,
            rating=published_rating("artisan", artisan.id),
        ),
        artisan.provider_profile_id,
    ).where(
        artisan.approval_status == "approved",
        artisan.is_active == true(),
        artisan.is_verified == true(),
    )
    yield with_provider(
        projection(
            "rental",
            vehicle,
            vehicle.vehicle_name,
            case(
                (vehicle.vehicle_type == "bike", "bike_rental"), else_="bicycle_rental"
            ),
            (rental.latitude, rental.longitude),
            city=rental.city,
            district=rental.district,
            address=rental.address,
            price=vehicle.price_per_day,
            currency="LKR",
            rating=published_rating("rental_vehicle", vehicle.id),
        ).join(
            rental,
            (rental.id == vehicle.location_id)
            & (rental.provider_id == vehicle.provider_id),
        ),
        vehicle.provider_id,
    ).where(
        vehicle.approval_status == "approved",
        vehicle.status == "available",
        rental.is_active == true(),
        vehicle.vehicle_type.in_(("bike", "bicycle")),
    )


class GeographyDistance(FunctionElement):
    type = Float()
    inherit_cache = True


@compiles(GeographyDistance, "mssql")
def compile_geography_distance(element, compiler, **kwargs):
    lat, lon, origin_lat, origin_lon = (
        compiler.process(part, **kwargs) for part in element.clauses
    )
    safe_lat = f"CASE WHEN {lat} BETWEEN -90 AND 90 THEN {lat} ELSE 0 END"
    safe_lon = f"CASE WHEN {lon} BETWEEN -180 AND 180 THEN {lon} ELSE 0 END"
    # The CASE guards prevent SQL Server constructing geography from legacy bad rows.
    return (
        f"geography::Point({safe_lat}, {safe_lon}, 4326)"
        f".STDistance(geography::Point({origin_lat}, {origin_lon}, 4326)) / 1000.0"
    )


def distance_expression(db, locations, origin):
    lat, lon = locations.c.latitude, locations.c.longitude
    if db.get_bind().dialect.name == "mssql":
        return GeographyDistance(
            lat, lon, literal(origin.latitude), literal(origin.longitude)
        )
    # Haversine for the isolated SQLite engine.
    target_lat = radians(origin.latitude)
    latitude_sine = func.sin((func.radians(lat) - target_lat) / 2.0)
    longitude_sine = func.sin(func.radians(lon - origin.longitude) / 2.0)
    half_chord = (
        latitude_sine * latitude_sine
        + cos(target_lat)
        * func.cos(func.radians(lat))
        * longitude_sine
        * longitude_sine
    )
    clamped = case((half_chord > 1, 1), (half_chord < 0, 0), else_=half_chord)
    return 12742.0176 * func.asin(func.sqrt(clamped))


def within_bounds(columns, bounds):
    west, south, east, north = bounds
    longitude = (
        columns.longitude.between(west, east)
        if west <= east
        else or_(columns.longitude >= west, columns.longitude <= east)
    )
    return columns.latitude.between(south, north), longitude


def nearby_bounds(origin, radius):
    # Pad the spherical prefilter for ellipsoid variation in SQL Server.
    angular = radius / 6320
    lat = radians(origin.latitude)
    south, north = max(-90, degrees(lat - angular)), min(90, degrees(lat + angular))
    if abs(lat) + angular >= pi / 2:
        return -180, south, 180, north
    delta = degrees(asin(sin(angular) / cos(lat)))
    west = origin.longitude - delta
    east = origin.longitude + delta
    return (
        west + 360 if west < -180 else west,
        south,
        east - 360 if east > 180 else east,
        north,
    )


def list_locations(
    db,
    *,
    page,
    page_size,
    category=None,
    bounds=None,
    search=None,
    origin: MapPoint | None = None,
    radius=None,
) -> MapPage:
    sources = union_all(*source_queries()).subquery("map_sources")
    columns = sources.c
    query = select(sources).where(
        columns.latitude.between(-90, 90),
        columns.longitude.between(-180, 180),
    )
    if category:
        query = query.where(columns.category == category)
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%")
        escaped = escaped.replace("_", "\\_").replace("[", "\\[")
        query = query.where(
            or_(
                *(
                    column.ilike(f"%{escaped}%", escape="\\")
                    for column in (columns.name, columns.city, columns.district)
                )
            )
        )
    if origin is not None:
        bounds = nearby_bounds(origin, radius)
    if bounds is not None:
        query = query.where(*within_bounds(columns, bounds))
    if origin is not None:
        distance = distance_expression(db, sources, origin)
        query = query.add_columns(distance.label("distance_km"))
        query = query.where(distance <= radius).order_by(distance)
    query = query.order_by(columns.module_type, columns.listing_id)
    rows = db.execute(query.offset((page - 1) * page_size).limit(page_size + 1))
    results = rows.mappings().all()
    items = []
    for row in results[:page_size]:
        path = DETAIL_PATHS.get(row["module_type"])
        items.append(
            MapLocation(
                **row,
                detail_path=f"{path}/{row['listing_id']}" if path else None,
            )
        )
    return MapPage(
        items=items,
        page=page,
        page_size=page_size,
        has_more=len(results) > page_size,
        distance_type="straight_line" if origin is not None else None,
    )
