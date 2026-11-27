# Interactive Map API

`GET /api/v1/map/markers` is a guest endpoint for published Sri Lankan tourism listings. It uses the same approved listing and active, approved provider rules as public discovery. It returns stored listing coordinates and excludes missing coordinates, values outside valid geographic ranges, and the `0,0` placeholder.

## Request

```text
GET /api/v1/map/markers?category=adventure&district=Badulla&q=Ella&bbox=80.9,6.7,81.2,7.2&limit=50
```

All filters are optional and combine with one another.

| Parameter | Behavior |
|---|---|
| `category` | One of `homestay`, `adventure`, `guide`, `bike`, `bicycle`, `artisan` |
| `district` | Case-insensitive exact district, up to 80 characters |
| `q` | Case-insensitive literal match on listing title, district, or locality, up to 100 characters |
| `bbox` | Inclusive `west,south,east,north` bounds in decimal degrees. Each pair must be increasing and within geographic range. |
| `limit` | 1–200 markers, default 100 |

Results are ordered by decreasing listing ID. Pan or zoom with a narrower bounding box when the result reaches the limit. Invalid filters receive HTTP 422. A valid request with no matching listings returns `[]`.

## Response

```json
[
  {
    "listing_id": 52,
    "category": "adventure",
    "title": "Ella Ridge Hike",
    "district": "Badulla",
    "latitude": 6.8756,
    "longitude": 81.0527,
    "cover_image_url": "/api/v1/listings/52/photos/9",
    "display_price": "3500.00",
    "pricing_unit": "per_participant",
    "currency": "LKR",
    "detail_url": "/api/v1/activities/52"
  }
]
```

The cover uses the selected visible image or the first usable visible image. A missing cover is `null`. Homestay prices are per night, rentals per day, and adventures per participant. Guides use the lowest advertised active package with its configured unit. Where a guide has only an advertised listing price, the unit is null. Artisans have null price and pricing unit. Display prices are indicative. Use the public detail and existing quote endpoints for availability and booking terms.

## Configuration and client integration

The endpoint uses the existing MVP database and listing metadata. No new database settings are required. The existing optional `MVP_LISTING_MEDIA_BASE_URL` resolves relative storage keys for public listing images.

In Angular or Flutter, query the current viewport after map movement settles. Resolve relative cover and detail URLs against the API host, and show a placeholder for a null cover. Use the marker coordinates to open a directions link in the user's map service. Handle empty responses and capped results. The marker payload contains public listing information, with district and coordinates for location display.
