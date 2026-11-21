# CeylonGo Module 03 – Activities & Experiences
## Public APIs Contract & Integration Specification

**Module:** Module 03 – Activities & Experiences  
**API Prefix:** `/api/v1/activities`  
**Authentication:** Public (No Bearer token required)  
**Specification Version:** 1.0.0  
**Status:** Implemented & Verified (Phase 2)  

---

### 1. Architectural & Security Overview

The Public Activities API provides read-only discovery, searching, filtering, taxonomy retrieval, detailed inspection, and photo gallery access for travelers exploring Sri Lankan activities and experiences.

#### Publication & Moderation Invariants
To guarantee traveler safety and platform integrity, every public retrieval strictly applies the central publication invariant `public_activity()`:
1. `Activity.is_active == True`: Inactive or soft-deleted activities are excluded.
2. `Activity.approval_status == 'approved'`: Draft, pending moderation, rejected, or suspended activities are excluded.
3. `ProviderProfile.approval_status == 'approved'`: Activities created by suspended, rejected, or unverified providers are excluded.
4. Non-compliant, draft, or unapproved activity IDs queried directly via detail endpoints return `404 Not Found`.

#### Privacy & Provider Identity Protection (A15)
To comply with CeylonGo privacy guidelines, public activity responses expose only approved business contact channels (`business_name`, `phone`, `secondary_phone`, `description`). Under no circumstances are sensitive provider identification fields (`nic_number`, `date_of_birth`, `address`, `selfie_image_path`) exposed to the public API.

---

### 2. Endpoints Summary

| Method | Path | Summary | Requirements |
|---|---|---|---|
| `GET` | `/api/v1/activities` | Browse and search approved activities with pagination and filters | A01, A02 |
| `GET` | `/api/v1/activities/categories` | List approved activity categories and examples | A03 |
| `GET` | `/api/v1/activities/{activity_id}` | Full public activity details, meeting point, itinerary, safety rules | A04, A06, A15, D02, D03 |
| `GET` | `/api/v1/activities/{activity_id}/images` | Public photo gallery ordered by display preference | A05 |
| `GET` | `/api/v1/activities/{activity_id}/slots` | Public available booking time slots (existing integration) | A07 |

---

### 3. Detailed Endpoint Contracts

#### 3.1 Browse & Search Activities (A01, A02)

- **Route:** `GET /api/v1/activities`
- **Authentication:** None (Public)
- **Description:** Returns a paginated list of approved activities with support for full-text search, multi-field filtering, and deterministic sorting.

##### Query Parameters

| Parameter | Type | Required | Default | Validation / Constraints | Description |
|---|---|---|---|---|---|
| `search` | `string` | No | `null` | Max 100 chars | Case-insensitive substring search matching `title`, `description`, or `city`. |
| `category` | `string` | No | `null` | `cultural`, `adventure`, `local`, `community` | Filter by activity category. |
| `district` | `string` | No | `null` | Max 100 chars | Filter by Sri Lankan district (e.g., `Kandy`, `Galle`). |
| `city` | `string` | No | `null` | Max 100 chars | Filter by city. |
| `min_price` | `decimal` | No | `null` | `>= 0` | Minimum base price in LKR. |
| `max_price` | `decimal` | No | `null` | `>= 0` | Maximum base price in LKR. Must be `>= min_price`. |
| `min_duration`| `int` | No | `null` | `> 0` | Minimum duration in minutes. |
| `max_duration`| `int` | No | `null` | `> 0` | Maximum duration in minutes. Must be `>= min_duration`. |
| `sort_by` | `string` | No | `"newest"`| `newest`, `price_asc`, `price_desc`, `duration_asc`, `duration_desc` | Deterministic sort order. |
| `page` | `int` | No | `1` | `>= 1` | 1-based page index. |
| `page_size` | `int` | No | `12` | `1 <= page_size <= 100` | Number of items per page. |

##### Response (200 OK) – `ActivityPagination`

```json
{
  "items": [
    {
      "id": 101,
      "title": "Kitulgala White Water Rafting",
      "category": "adventure",
      "district": "Kegalle",
      "city": "Kitulgala",
      "duration_minutes": 180,
      "pricing_type": "per_person",
      "price": "6500.00",
      "currency": "LKR",
      "minimum_age": 12,
      "cover_image_url": "https://cdn.ceylongo.com/activities/101/cover.jpg",
      "short_description": "Experience thrilling grade 3 and 4 rapids on the scenic Kelani River with certified safety instructors.",
      "provider_business_name": "Kelani River Adventures"
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 12,
  "total_pages": 1
}
```

##### Error Responses
- `422 Unprocessable Entity`: Triggered when `min_price > max_price` or `min_duration > max_duration`, or invalid query parameter formats.
  ```json
  {
    "detail": "min_price cannot exceed max_price"
  }
  ```

---

#### 3.2 Activity Category Taxonomy (A03)

- **Route:** `GET /api/v1/activities/categories`
- **Authentication:** None (Public)
- **Description:** Returns the 4 official approved activity categories with localized metadata and representative examples. Registered before dynamic `/{activity_id}` routes to prevent path collisions.

##### Response (200 OK) – `list[ActivityCategoryDetail]`

```json
[
  {
    "slug": "cultural",
    "name": "Cultural Activities",
    "description": "Traditional dance, heritage experiences, festivals and cultural shows",
    "examples": [
      "Traditional dance",
      "Heritage experiences",
      "Festivals",
      "Cultural shows"
    ]
  },
  {
    "slug": "adventure",
    "name": "Adventure Activities",
    "description": "Rafting, surfing, hiking, rock climbing and ziplining",
    "examples": [
      "Rafting",
      "Surfing",
      "Hiking",
      "Rock climbing",
      "Ziplining"
    ]
  },
  {
    "slug": "local",
    "name": "Local Experiences",
    "description": "Cooking classes, village tours, farming and craft workshops",
    "examples": [
      "Cooking classes",
      "Village tours",
      "Farming",
      "Craft workshops"
    ]
  },
  {
    "slug": "community",
    "name": "Community Experiences",
    "description": "Tea experiences, fishing communities and community-led visits",
    "examples": [
      "Tea experiences",
      "Fishing communities",
      "Community-led visits"
    ]
  }
]
```

---

#### 3.3 Public Activity Details (A04, A06, A15, D02, D03)

- **Route:** `GET /api/v1/activities/{activity_id}`
- **Authentication:** None (Public)
- **Description:** Returns comprehensive public details for an approved activity including itinerary, safety instructions (D02), required equipment, meeting point coordinates (A06, D03), photo gallery, and safe organizer contacts (A15).

##### Path Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `activity_id` | `int` | Yes | Primary key ID of the activity. |

##### Response (200 OK) – `ActivityPublicDetail`

```json
{
  "id": 101,
  "title": "Kitulgala White Water Rafting",
  "description": "Full description of the white water rafting adventure...",
  "category": "adventure",
  "district": "Kegalle",
  "city": "Kitulgala",
  "duration_minutes": 180,
  "itinerary": "1. Arrival and safety briefing\n2. Equipment fitting\n3. River run\n4. Debrief",
  "requirements": "Basic swimming ability required. Minimum age 12.",
  "safety_rules": "Always wear provided life jacket and helmet. Follow guide instructions at all times.",
  "required_equipment": "Life jackets and helmets provided. Bring spare dry clothes and water shoes.",
  "minimum_age": 12,
  "pricing_type": "per_person",
  "price": "6500.00",
  "currency": "LKR",
  "meeting_point": "Kelani River Base Camp, Main Street, Kitulgala",
  "latitude": "6.991200",
  "longitude": "80.413200",
  "organizer": {
    "business_name": "Kelani River Adventures",
    "phone": "+94771234567",
    "secondary_phone": "+94112345678",
    "description": "Pioneers in eco-friendly adventure tourism in Sri Lanka."
  },
  "images": [
    {
      "id": 501,
      "activity_id": 101,
      "image_url": "https://cdn.ceylongo.com/activities/101/cover.jpg",
      "alt_text": "Rafting team conquering rapid",
      "display_order": 0,
      "is_cover": true
    },
    {
      "id": 502,
      "activity_id": 101,
      "image_url": "https://cdn.ceylongo.com/activities/101/river.jpg",
      "alt_text": "Scenic view of Kelani River",
      "display_order": 1,
      "is_cover": false
    }
  ],
  "created_at": "2026-11-20T10:00:00Z"
}
```

##### Error Responses
- `404 Not Found`: Activity does not exist, is inactive, is in draft/pending/rejected/suspended status, or belongs to an unapproved provider.
  ```json
  {
    "detail": "Activity not found"
  }
  ```
- `422 Unprocessable Entity`: Invalid `activity_id` (e.g. non-integer).

---

#### 3.4 Public Photo Gallery (A05)

- **Route:** `GET /api/v1/activities/{activity_id}/images`
- **Authentication:** None (Public)
- **Description:** Returns all images associated with an approved activity, sorted by `display_order ASC, id ASC`.

##### Path Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `activity_id` | `int` | Yes | Primary key ID of the activity. |

##### Response (200 OK) – `list[ActivityImageResponse]`

```json
[
  {
    "id": 501,
    "activity_id": 101,
    "image_url": "https://cdn.ceylongo.com/activities/101/cover.jpg",
    "alt_text": "Rafting team conquering rapid",
    "display_order": 0,
    "is_cover": true
  },
  {
    "id": 502,
    "activity_id": 101,
    "image_url": "https://cdn.ceylongo.com/activities/101/river.jpg",
    "alt_text": "Scenic view of Kelani River",
    "display_order": 1,
    "is_cover": false
  }
]
```

##### Error Responses
- `404 Not Found`: Activity does not exist or is not approved/published.
  ```json
  {
    "detail": "Activity not found"
  }
  ```

---

#### 3.5 Public Activity Time Slots (A07 - Existing Integration)

- **Route:** `GET /api/v1/activities/{activity_id}/slots`
- **Authentication:** None (Public)
- **Description:** Returns available booking time slots for an approved activity within an optional date range.
- **Router:** `app/routers/activity_slots.py`

##### Path Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `activity_id` | `int` | Yes | Primary key ID of the activity. |

##### Query Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `start_date` | `date` | No | Filter slots on or after this date (`YYYY-MM-DD`). |
| `end_date` | `date` | No | Filter slots on or before this date (`YYYY-MM-DD`). |

##### Response (200 OK) – `list[ActivitySlotResponse]`

```json
[
  {
    "id": 201,
    "activity_id": 101,
    "start_time": "2026-12-01T09:00:00",
    "end_time": "2026-12-01T12:00:00",
    "max_capacity": 12,
    "booked_count": 4,
    "available_capacity": 8,
    "is_available": true
  }
]
```

##### Error Responses
- `404 Not Found`: Activity does not exist or is not approved/published.
- `422 Unprocessable Entity`: Invalid date format or date range mismatch.

---

### 4. Pydantic Schema Definitions

Source file: `app/schemas/activity.py`

```python
class ActivityCategory(str, Enum):
    cultural = "cultural"
    adventure = "adventure"
    local = "local"
    community = "community"

class ActivityImageResponse(BaseModel):
    id: int
    activity_id: int
    image_url: str
    alt_text: str | None = None
    display_order: int = 0
    is_cover: bool = False

class ActivityOrganizerContact(BaseModel):
    business_name: str
    phone: str
    secondary_phone: str | None = None
    description: str | None = None

class ActivityCategoryDetail(BaseModel):
    slug: ActivityCategory
    name: str
    description: str
    examples: list[str]

class ActivityPublicSummary(BaseModel):
    id: int
    title: str
    category: ActivityCategory
    district: str
    city: str
    duration_minutes: int
    pricing_type: PricingType
    price: Decimal
    currency: str
    minimum_age: int
    cover_image_url: str | None = None
    short_description: str
    provider_business_name: str | None = None

class ActivityPagination(BaseModel):
    items: list[ActivityPublicSummary]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total_pages: int = Field(ge=0)

class ActivityPublicDetail(BaseModel):
    id: int
    title: str
    description: str
    category: ActivityCategory
    district: str
    city: str
    duration_minutes: int
    itinerary: str | None = None
    requirements: str | None = None
    safety_rules: str | None = None
    required_equipment: str | None = None
    minimum_age: int
    pricing_type: PricingType
    price: Decimal
    currency: str
    meeting_point: str
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    organizer: ActivityOrganizerContact | None = None
    images: list[ActivityImageResponse] = Field(default_factory=list)
    created_at: datetime
```
