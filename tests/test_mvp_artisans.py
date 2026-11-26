from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects import mssql

from app.database.mvp_session import get_mvp_db
from app.models.mvp import MVPListing, MVPListingVerification
from app.routers.mvp import artisans as artisan_router
from tests.test_mvp_listing_management import managed


API = "/api/v1"
PRODUCT = {
    "title": "Glazed clay cup", "description": "Hand-glazed cup in brown clay.",
    "category": "Ceramics", "materials": "Clay", "price": "1800.00",
}


def business(managed):
    client, (owner, _, _, _), photo = managed
    response = client.post(f"{API}/provider/listings", headers=owner, json={
        "category": "artisan", "title": "Delft Clay Studio", "description": "Small pottery studio.",
        "district": "Jaffna", "locality": "Delft", "latitude": "9.52", "longitude": "79.72",
    })
    assert response.status_code == 201, response.text
    listing = response.json()["listing"]["id"]
    path = f"{API}/provider/listings/{listing}"
    assert client.post(path + "/photos", headers=owner, files=photo).status_code == 201
    assert client.put(path + "/evidence", headers=owner, json={
        "notes": "Academic workshop documentation.",
    }).status_code == 200
    return listing, path


def approve(managed, listing, path):
    client, (owner, _, admin, _), _ = managed
    submitted = client.post(path + "/submit", headers=owner)
    assert submitted.status_code == 200, submitted.text
    approved = client.post(f"{API}/admin/listings/{listing}/decision", headers=admin,
                           json={"approval_status": "approved"})
    assert approved.status_code == 200, approved.text


def test_artisan_metadata_search_and_seed_compatibility(managed):
    client, (owner, other, _, _), _ = managed
    listing, path = business(managed)
    public = f"{API}/artisan-listings"
    assert client.get(public).json()["total"] == 0
    metadata = {
        "craft_category": "Pottery & Ceramics", "business_type": "Workshop",
        "business_background": "A clay workshop inspired by local craft.",
        "artisan_information": "Studio maker", "phone": "+94771234567",
        "whatsapp_number": "+94771234567", "email": "studio@example.com",
        "website_url": "https://example.com", "social_links": {"instagram": "https://example.com"},
        "opening_hours": "Monday mornings",
        "business_hours": [{"day_of_week": 0, "open_time": "09:00", "close_time": "12:00"}],
    }
    assert client.patch(path, headers=other, json={"artisan": metadata}).status_code == 403
    result = client.patch(path, headers=owner, json={"artisan": metadata, "price": 1600})
    assert result.status_code == 200, result.text
    approve(managed, listing, path)
    result = client.get(public, params={
        "search": "clay", "category": "pottery & ceramics", "business_type": "workshop",
        "district": "jaffna", "min_price": 1500, "max_price": 1700, "sort_by": "price_asc",
    })
    assert result.status_code == 200, result.text
    result = result.json()["items"][0]
    assert result["email"] == "studio@example.com" and result["total_reviews"] == 0
    assert result["whatsapp_link"].endswith("94771234567") and "notes" not in result
    assert client.get(f"{public}/{listing}/business-hours").json()[0]["day_of_week"] == 0
    assert client.get(public + "/categories").json() == ["Pottery & Ceramics"]
    assert client.get(public + "/districts").json() == ["Jaffna"]
    modified = client.patch(path, headers=owner, json={"artisan": {"phone": None}})
    assert modified.json()["details"]["artisan"]["business_type"] == "Workshop"
    assert client.get(f"{public}/{listing}").status_code == 404
    approve(managed, listing, path)
    sessions = client.app.dependency_overrides[get_mvp_db]()
    db = next(sessions)
    try:
        db.get(MVPListingVerification, listing).details = '{"academic_seed":{"key":"craft-01"}}'
        db.commit()
    finally:
        sessions.close()
    legacy = client.get(public, params={"category": "Pottery & Ceramics"}).json()["items"][0]
    assert legacy["id"] == listing and legacy["craft_category"] == "Pottery & Ceramics"
    assert legacy["phone"] is None and legacy["business_type"] is None


def test_artisan_product_visibility_images_and_wishlist(managed):
    client, (owner, other, _, traveler), image = managed
    listing, path = business(managed)
    products = path + "/products"
    created = client.post(products, headers=owner, json=PRODUCT)
    assert created.status_code == 201, created.text
    product = created.json()["id"]
    api_product = f"{API}/showcase-items/{product}"
    assert client.get(api_product).status_code == 404
    photo = client.post(f"{products}/{product}/images", headers=owner, files=image)
    assert photo.status_code == 201, photo.text
    photo = photo.json()
    assert client.get(photo["image_url"]).status_code == 401
    assert client.get(photo["image_url"], headers=other).status_code == 403
    assert client.get(photo["image_url"], headers=owner).status_code == 200
    approve(managed, listing, path)
    published = client.get(api_product).json()
    assert published["price"] == 1800 and published["category"] == "Ceramics"
    image_url = published["images"][0]["image_url"]
    assert client.get(image_url).status_code == 200
    assert client.get(f"{API}/listings/{listing}/photos/{photo['id']}").status_code == 404
    assert client.get(f"{API}/showcase-items", params={"category": "Ceramics"}).json()["total"] == 1
    assert client.get(f"{API}/artisan-listings/{listing}/showcase-items").json()["total"] == 1
    assert client.put(f"{products}/{product}", headers=other, json=PRODUCT).status_code == 403
    assert client.put(f"{products}/{product}", headers=owner,
                      json={**PRODUCT, "price": None}).json()["price"] is None
    wishlist = f"{API}/artisan-listings/{listing}/wishlist"
    assert client.post(wishlist).status_code == 401
    assert client.post(wishlist, headers=traveler, json={}).status_code == 201
    assert client.post(wishlist, headers=traveler, json={}).status_code == 201
    saved = client.get(f"{API}/artisan-wishlists/me", headers=traveler).json()
    assert saved["total"] == 1 and saved["items"][0]["artisan_listing_id"] == listing
    assert client.get(wishlist + "/check", headers=traveler).json()["is_saved"]
    assert client.delete(f"{products}/{product}", headers=owner).status_code == 204
    assert client.get(api_product).status_code == 404
    assert client.get(image_url).status_code == 404
    assert client.get(products, headers=owner).json()["items"][0]["is_active"] is False
    assert client.put(f"{products}/{product}", headers=owner, json=PRODUCT).status_code == 200
    assert client.delete(photo["image_url"], headers=owner).status_code == 204
    assert client.get(image_url).status_code == 404
    assert client.post(path + "/unpublish", headers=owner).status_code == 200
    assert client.get(api_product).status_code == 404
    saved = client.get(f"{API}/artisan-wishlists/me", headers=traveler).json()["items"][0]
    assert saved["listing"] is None and saved["is_available"] is False
    assert client.delete(wishlist, headers=traveler).status_code == 204
    assert not client.get(wishlist + "/check", headers=traveler).json()["is_saved"]


def test_artisan_review_envelope_no_bookings_and_admin_lifecycle(managed):
    client, (owner, other, admin, traveler), image = managed
    listing, path = business(managed)
    product_res = client.post(path + "/products", headers=owner, json=PRODUCT)
    assert product_res.status_code == 201, product_res.text
    product_id = product_res.json()["id"]

    # Single product detail endpoint and ownership enforcement
    assert client.get(f"{path}/products/{product_id}", headers=other).status_code == 403
    assert client.get(f"{path}/products/{product_id}", headers=traveler).status_code == 403
    assert client.get(f"{path}/products/999999", headers=owner).status_code == 404
    single = client.get(f"{path}/products/{product_id}", headers=owner)
    assert single.status_code == 200 and single.json()["id"] == product_id

    # Non-artisan listing returns 404 on artisan product endpoints
    homestay = client.post(f"{API}/provider/listings", headers=owner, json={"category": "homestay"})
    assert homestay.status_code == 201
    homestay_id = homestay.json()["listing"]["id"]
    assert client.get(f"{API}/provider/listings/{homestay_id}/products", headers=owner).status_code == 404

    # Review envelope exposes products cleanly and does not leak them as homestay rooms
    review_env = client.get(path, headers=owner).json()
    assert review_env["rooms"] == []
    assert [p["id"] for p in review_env["details"]["products"]] == [product_id]

    # Business hours validation and weekday sorting
    assert client.patch(path, headers=owner, json={"artisan": {
        "business_hours": [
            {"day_of_week": 0, "open_time": "10:00", "close_time": "09:00"},
        ],
    }}).status_code == 422
    assert client.patch(path, headers=owner, json={"artisan": {
        "business_hours": [
            {"day_of_week": 1, "open_time": "09:00", "close_time": "17:00"},
            {"day_of_week": 1, "is_closed": True},
        ],
    }}).status_code == 422
    hours_res = client.patch(path, headers=owner, json={"artisan": {
        "business_hours": [
            {"day_of_week": 5, "is_closed": True, "open_time": "09:00", "close_time": "17:00"},
            {"day_of_week": 0, "open_time": "08:30", "close_time": "16:30"},
        ],
    }})
    assert hours_res.status_code == 200, hours_res.text
    saved_hours = hours_res.json()["details"]["artisan"]["business_hours"]
    assert [h["day_of_week"] for h in saved_hours] == [0, 5]
    assert saved_hours[1]["open_time"] is None and saved_hours[1]["close_time"] is None

    # E-commerce / booking operations are blocked for artisan listings
    assert client.post(f"{API}/listings/{listing}/units", headers=owner,
                       json={"label": "Slot", "capacity": 1}).status_code == 422
    assert client.post(f"{API}/listings/{listing}/slots", headers=owner, json={
        "unit_id": product_id, "starts_at": "2030-01-01T09:00:00Z",
        "ends_at": "2030-01-01T11:00:00Z", "capacity": 1,
    }).status_code == 422
    assert client.post(f"{API}/bookings", headers=traveler, json={
        "listing_id": listing, "starts_at": "2030-01-01T09:00:00Z",
        "ends_at": "2030-01-01T11:00:00Z", "quantity": 1, "request_key": str(uuid4()),
    }).status_code == 422

    # Admin verification, more_evidence_required, approval, suspension, re-approval
    assert client.post(path + "/submit", headers=owner).status_code == 200
    queue = client.get(f"{API}/admin/listings", headers=admin, params={"category": "artisan"})
    assert [item["listing"]["id"] for item in queue.json()] == [listing]
    admin_detail = client.get(f"{API}/admin/listings/{listing}", headers=admin).json()
    assert admin_detail["rooms"] == []
    assert [p["id"] for p in admin_detail["details"]["products"]] == [product_id]
    assert client.post(f"{API}/admin/listings/{listing}/decision", headers=admin, json={
        "approval_status": "more_evidence_required",
        "decision_reason": "Add workshop registration photo",
    }).status_code == 200
    assert client.get(f"{path}/products/{product_id}", headers=owner).json()["approval_status"] == "more_evidence_required"
    assert client.post(path + "/evidence/files", headers=owner, files=image,
                       data={"kind": "workshop"}).status_code == 201
    approve(managed, listing, path)
    assert client.post(f"{API}/artisan-listings/{listing}/wishlist", headers=traveler).status_code == 201
    assert client.get(f"{API}/artisan-listings/{listing}/business-hours").json()[0]["day_of_week"] == 0

    # Suspend hides listing and products and marks wishlist entry unavailable
    assert client.post(f"{API}/admin/listings/{listing}/decision", headers=admin, json={
        "approval_status": "suspended", "decision_reason": "Verify workshop location",
    }).status_code == 200
    assert client.get(f"{API}/artisan-listings/{listing}").status_code == 404
    assert client.get(f"{API}/showcase-items/{product_id}").status_code == 404
    saved = client.get(f"{API}/artisan-wishlists/me", headers=traveler).json()["items"][0]
    assert saved["is_available"] is False and saved["listing"] is None
    assert client.post(f"{API}/admin/listings/{listing}/decision", headers=admin, json={
        "approval_status": "approved",
    }).status_code == 200
    assert client.get(f"{API}/artisan-listings/{listing}").status_code == 200
    assert client.get(f"{API}/showcase-items/{product_id}").status_code == 200


def test_artisan_discovery_pagination_sorting_and_mssql_compilation(managed):
    client, (owner, _, _, _), _ = managed
    created_ids = []
    for title, district, locality, craft, price in (
        ("Kandy Brass Works", "Kandy", "Pilimathalawa", "Brass & Metalware", 2500),
        ("Ambalangoda Mask House", "Galle", "Ambalangoda", "Woodcarving & Masks", 1200),
        ("Batticaloa Handloom Co-op", "Batticaloa", "Kattankudy", "Handloom & Weaving", 3400),
    ):
        listing, path = business(managed)
        assert client.patch(path, headers=owner, json={
            "title": title, "district": district, "locality": locality, "price": price,
            "artisan": {"craft_category": craft, "business_type": "Studio"},
        }).status_code == 200
        approve(managed, listing, path)
        created_ids.append(listing)

    assert client.post(
        f"{API}/provider/listings/{created_ids[0]}/products", headers=owner,
        json={**PRODUCT, "title": "Engraved Brass Lamp", "materials": "Solid Brass"},
    ).status_code == 201
    assert client.get(f"{API}/showcase-items", params={"item_type": "service"}).json()["total"] == 0
    assert client.get(f"{API}/showcase-items", params={"search": "brass"}).json()["total"] == 1

    # Pagination, sorting, district search, and invalid price bounds
    assert client.get(f"{API}/artisan-listings", params={"min_price": 2000, "max_price": 1000}).status_code == 422
    assert client.get(f"{API}/artisan-listings", params={"is_featured": "true"}).json()["total"] == 0
    assert client.get(f"{API}/artisan-listings", params={"search": "batticaloa"}).json()["total"] == 1
    page1 = client.get(f"{API}/artisan-listings", params={
        "sort_by": "price_asc", "page": 1, "page_size": 2,
    }).json()
    assert page1["total"] == 3 and page1["total_pages"] == 2
    assert [item["title"] for item in page1["items"]] == [
        "Ambalangoda Mask House", "Kandy Brass Works",
    ]
    by_title = client.get(f"{API}/artisan-listings", params={"sort_by": "title"}).json()
    assert [item["title"] for item in by_title["items"]] == [
        "Ambalangoda Mask House", "Batticaloa Handloom Co-op", "Kandy Brass Works",
    ]

    # Compile artisan discovery and product queries for SQL Server dialect
    sessions = client.app.dependency_overrides[get_mvp_db]()
    db = next(sessions)
    try:
        dialect = mssql.dialect()
        craft = artisan_router._craft(db)
        queries = (
            artisan_router.ARTISAN.outerjoin(MVPListingVerification),
            artisan_router.ARTISAN.outerjoin(MVPListingVerification).with_only_columns(craft).distinct().order_by(craft),
            artisan_router.ARTISAN.with_only_columns(MVPListing.district).distinct().order_by(MVPListing.district),
            artisan_router._product_query(db),
        )
        for query in queries:
            sql = str(query.compile(dialect=dialect))
            assert sql
    finally:
        sessions.close()
