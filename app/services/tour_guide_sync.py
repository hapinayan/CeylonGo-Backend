"""
Local Tour Guide Module 05 — Core Domain Helpers, Validation & Shared Sync.

Provides:
- Canonical guide category definitions and specialties normalization/validation
- Tour Guide -> dbo.listing_references synchronization
- Admin audit logging helper for Tour Guide moderation & verification
- Availability time-window overlap and booking-conflict detection
- Secure storage and validation helpers for public gallery images and
  confidential qualification/credential documents
"""

from __future__ import annotations

import io
from app.core.audit import sanitize_audit_details
import os
import re
import uuid
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile, status
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.models.provider_profile import ProviderProfile
from app.models.shared import AdminAuditLog, ListingReference
from app.models.tour_guide import (
    TourGuide,
    TourGuideAvailability,
    TourGuideBooking,
)

# =============================================================================
# 1. CANONICAL GUIDE CATEGORIES (Module 05 Spec Section 2)
# =============================================================================

GUIDE_CATEGORIES: tuple[dict[str, str], ...] = (
    {
        "slug": "cultural_heritage",
        "name": "Cultural & Heritage",
        "examples": "Historical sites, temples, museums and heritage tours",
    },
    {
        "slug": "adventure_nature",
        "name": "Adventure & Nature",
        "examples": "Hiking, trekking, waterfalls and nature trails",
    },
    {
        "slug": "city_village",
        "name": "City & Village",
        "examples": "City walks, markets, village visits and sightseeing",
    },
    {
        "slug": "wildlife_eco",
        "name": "Wildlife & Eco",
        "examples": "Birdwatching, wildlife observation and eco guidance",
    },
)

GUIDE_CATEGORY_BY_SLUG: dict[str, dict[str, str]] = {
    item["slug"]: item for item in GUIDE_CATEGORIES
}

# Accepted aliases (case-insensitive) mapped to canonical slug
_CATEGORY_ALIAS_TO_SLUG: dict[str, str] = {}
for _cat in GUIDE_CATEGORIES:
    _slug = _cat["slug"]
    _name = _cat["name"]
    _CATEGORY_ALIAS_TO_SLUG[_slug.lower()] = _slug
    _CATEGORY_ALIAS_TO_SLUG[_slug.replace("_", " ").lower()] = _slug
    _CATEGORY_ALIAS_TO_SLUG[_slug.replace("_", "-").lower()] = _slug
    _CATEGORY_ALIAS_TO_SLUG[_name.lower()] = _slug
    _CATEGORY_ALIAS_TO_SLUG[_name.replace("&", "and").lower()] = _slug

# Short aliases from spec tables (e.g., "Cultural", "Adventure", "City/Village", "Wildlife/Eco")
_CATEGORY_ALIAS_TO_SLUG.update(
    {
        "cultural": "cultural_heritage",
        "heritage": "cultural_heritage",
        "adventure": "adventure_nature",
        "nature": "adventure_nature",
        "city/village": "city_village",
        "city": "city_village",
        "village": "city_village",
        "wildlife/eco": "wildlife_eco",
        "wildlife": "wildlife_eco",
        "eco": "wildlife_eco",
    }
)

ALLOWED_CURRENCIES: frozenset[str] = frozenset({"LKR", "USD", "EUR", "GBP", "AUD"})
LANGUAGE_CODE_RE = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2})?$")


def resolve_category_slug(raw: str) -> str | None:
    cleaned = " ".join(raw.strip().lower().split())
    return _CATEGORY_ALIAS_TO_SLUG.get(cleaned)


def normalize_specialties_string(raw: str | None) -> str | None:
    """
    Validate and normalize a comma-separated specialties string against the
    4 approved Module 05 guide categories. Returns canonical display names
    joined by ', ' in deterministic order without duplicates.
    """
    if raw is None:
        return None
    parts = [segment.strip() for segment in raw.split(",") if segment.strip()]
    if not parts:
        return None

    slugs: list[str] = []
    invalid: list[str] = []
    for part in parts:
        slug = resolve_category_slug(part)
        if slug is None:
            invalid.append(part)
        elif slug not in slugs:
            slugs.append(slug)

    if invalid:
        allowed_labels = ", ".join(cat["name"] for cat in GUIDE_CATEGORIES)
        raise ValueError(
            f"Unsupported guide categories: {', '.join(invalid)}. "
            f"Allowed categories: {allowed_labels}."
        )

    return ", ".join(GUIDE_CATEGORY_BY_SLUG[s]["name"] for s in slugs)


def extract_category_slugs(raw: str | None) -> list[str]:
    """
    Extract canonical category slugs from a stored specialties string.
    Tolerates legacy free-text values without raising on read/serialization.
    """
    if not raw:
        return []
    slugs: list[str] = []
    for part in raw.split(","):
        slug = resolve_category_slug(part)
        if slug and slug not in slugs:
            slugs.append(slug)
    return slugs


def categories_to_specialties_string(slugs: list[str]) -> str:
    """Convert validated category slugs into canonical comma-separated specialties."""
    unique_slugs: list[str] = []
    for slug in slugs:
        resolved = resolve_category_slug(slug)
        if resolved is None:
            allowed = ", ".join(GUIDE_CATEGORY_BY_SLUG.keys())
            raise ValueError(
                f"Unsupported guide category '{slug}'. Allowed slugs: {allowed}."
            )
        if resolved not in unique_slugs:
            unique_slugs.append(resolved)
    if not unique_slugs:
        raise ValueError("At least one guide category must be selected.")
    return ", ".join(GUIDE_CATEGORY_BY_SLUG[s]["name"] for s in unique_slugs)


def validate_currency_code(currency: str) -> str:
    cleaned = currency.strip().upper()
    if cleaned not in ALLOWED_CURRENCIES:
        allowed = ", ".join(sorted(ALLOWED_CURRENCIES))
        raise ValueError(f"Unsupported currency '{currency}'. Allowed: {allowed}.")
    return cleaned


def validate_language_code(code: str) -> str:
    cleaned = code.strip()
    if "-" in cleaned:
        lang, region = cleaned.split("-", 1)
        cleaned = f"{lang.lower()}-{region.upper()}"
    else:
        cleaned = cleaned.lower()
    if not LANGUAGE_CODE_RE.match(cleaned):
        raise ValueError(
            "Invalid language_code format. Use ISO 639-1/639-2 code (e.g. 'en', 'si', 'ta', 'zh-CN')."
        )
    return cleaned


def classify_package_duration(duration_hours: float) -> str:
    """Classify package duration into half_day, full_day, or multi_day."""
    if duration_hours <= 4.5:
        return "half_day"
    if duration_hours <= 12.0:
        return "full_day"
    return "multi_day"


# =============================================================================
# 2. LISTING REFERENCE SYNCHRONIZATION (dbo.listing_references)
# =============================================================================

def sync_tour_guide_listing_reference(
    db: Session,
    guide: TourGuide,
) -> ListingReference | None:
    """
    Synchronize a TourGuide with the centralized dbo.listing_references table
    (module_type='tour_guide', target_id=guide.id).

    - Creates a ListingReference when a guide becomes approved & active.
    - Updates metadata and is_active flag on subsequent edits, suspension,
      rejection, deactivation, or reinstatement.
    """
    if guide.id is None:
        return None

    provider_profile = guide.provider_profile
    if provider_profile is None and guide.provider_profile_id is not None:
        provider_profile = (
            db.query(ProviderProfile)
            .filter(ProviderProfile.id == guide.provider_profile_id)
            .first()
        )

    title = (
        provider_profile.business_name
        if provider_profile and provider_profile.business_name
        else f"Tour Guide #{guide.id}"
    )
    is_ref_active = bool(guide.is_active and guide.approval_status == "approved")

    ref = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == "tour_guide",
            ListingReference.target_id == guide.id,
        )
        .first()
    )

    if ref is None:
        if is_ref_active:
            ref = ListingReference(
                module_type="tour_guide",
                target_id=guide.id,
                provider_profile_id=guide.provider_profile_id,
                title=title[:255],
                district=guide.base_district[:100],
                city=guide.base_city[:100] if guide.base_city else None,
                latitude=guide.base_latitude,
                longitude=guide.base_longitude,
                is_active=True,
            )
            db.add(ref)
    else:
        ref.title = title[:255]
        ref.district = guide.base_district[:100]
        ref.city = guide.base_city[:100] if guide.base_city else None
        ref.latitude = guide.base_latitude
        ref.longitude = guide.base_longitude
        ref.is_active = is_ref_active
        ref.provider_profile_id = guide.provider_profile_id

    return ref


# =============================================================================
# 3. ADMIN AUDIT LOGGING
# =============================================================================

def record_tour_guide_admin_audit(
    db: Session,
    *,
    admin_user_id: int,
    action: str,
    entity_name: str,
    entity_id: int,
    details: dict[str, Any] | str | None = None,
    ip_address: str | None = None,
) -> AdminAuditLog:
    """Create a structured entry in dbo.admin_audit_logs."""
    entry = AdminAuditLog(
        admin_user_id=admin_user_id,
        action=action[:50],
        entity_name=entity_name[:50],
        entity_id=entity_id,
        details=sanitize_audit_details(details),
        ip_address=ip_address[:45] if ip_address else None,
    )
    db.add(entry)
    return entry


# =============================================================================
# 4. AVAILABILITY OVERLAP & BOOKING CONFLICT HELPERS
# =============================================================================

def times_overlap(
    start_a: time | None,
    end_a: time | None,
    start_b: time | None,
    end_b: time | None,
) -> bool:
    """
    Determine whether two same-day availability slots overlap.
    A full-day slot (start=None, end=None) overlaps every other slot on that date.
    Two timed slots [start_a, end_a) and [start_b, end_b) overlap iff
    start_a < end_b and start_b < end_a (adjacent slots like 08:00-12:00 and
    12:00-16:00 do NOT overlap).
    """
    if start_a is None or end_a is None or start_b is None or end_b is None:
        return True
    return start_a < end_b and start_b < end_a


def find_overlapping_availability_slot(
    db: Session,
    *,
    tour_guide_id: int,
    available_date: date,
    start_time: time | None,
    end_time: time | None,
    exclude_slot_id: int | None = None,
) -> TourGuideAvailability | None:
    query = db.query(TourGuideAvailability).filter(
        TourGuideAvailability.tour_guide_id == tour_guide_id,
        TourGuideAvailability.available_date == available_date,
    )
    if exclude_slot_id is not None:
        query = query.filter(TourGuideAvailability.id != exclude_slot_id)

    for existing in query.all():
        if times_overlap(start_time, end_time, existing.start_time, existing.end_time):
            return existing
    return None


def find_conflicting_active_booking(
    db: Session,
    *,
    tour_guide_id: int,
    target_date: date,
    start_time: time | None,
    end_time: time | None,
) -> TourGuideBooking | None:
    """
    Check if blocking a date/time window conflicts with any confirmed or
    in-progress tour guide booking.
    """
    window_start = datetime.combine(target_date, start_time or time.min)
    window_end = (
        datetime.combine(target_date, end_time)
        if end_time is not None
        else datetime.combine(target_date, time(23, 59, 59, 999999))
    )

    return (
        db.query(TourGuideBooking)
        .filter(
            TourGuideBooking.tour_guide_id == tour_guide_id,
            TourGuideBooking.booking_status.in_(("confirmed", "in_progress")),
            TourGuideBooking.start_datetime < window_end,
            TourGuideBooking.end_datetime > window_start,
        )
        .first()
    )


# =============================================================================
# 5. SECURE FILE STORAGE (IMAGES & PRIVATE CREDENTIALS)
# =============================================================================

MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024        # 5 MB
MAX_CREDENTIAL_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

ALLOWED_IMAGE_EXTENSIONS: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

ALLOWED_CREDENTIAL_EXTENSIONS: dict[str, str] = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

PILLOW_FORMAT_TO_EXT: dict[str, str] = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
}


def get_tour_guide_images_dir() -> Path:
    base = Path(
        os.getenv(
            "TOUR_GUIDE_IMAGES_DIR",
            str(Path(os.getcwd()) / "uploads" / "tour_guide_images"),
        )
    )
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_tour_guide_credentials_dir() -> Path:
    base = Path(
        os.getenv(
            "TOUR_GUIDE_CREDENTIALS_DIR",
            str(Path(os.getcwd()) / "uploads" / "tour_guide_credentials"),
        )
    )
    base.mkdir(parents=True, exist_ok=True)
    return base


def _validate_image_bytes(raw_bytes: bytes) -> str:
    """Validate image magic bytes with Pillow and return canonical extension."""
    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            img.verify()
            fmt = (img.format or "").upper()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is not a valid image (JPEG, PNG, or WEBP required).",
        ) from exc

    ext = PILLOW_FORMAT_TO_EXT.get(fmt)
    if not ext:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported image format. Allowed formats: JPEG, PNG, WEBP.",
        )
    return ext


async def save_tour_guide_image_file(guide_id: int, file: UploadFile) -> str:
    """
    Validate and store a public tour guide gallery image.
    Returns the public API URL path '/api/v1/uploads/tour-guides/<filename>'.
    """
    filename_orig = file.filename or ""
    ext_orig = Path(filename_orig).suffix.lower()
    if ext_orig and ext_orig not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported image file extension. Allowed: .jpg, .jpeg, .png, .webp.",
        )

    raw_bytes = await file.read(MAX_IMAGE_SIZE_BYTES + 1)
    if not raw_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded image file is empty.",
        )
    if len(raw_bytes) > MAX_IMAGE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Image file exceeds maximum allowed size of 5 MB.",
        )

    canonical_ext = _validate_image_bytes(raw_bytes)
    safe_name = f"guide_{guide_id}_{uuid.uuid4().hex}{canonical_ext}"
    dest = get_tour_guide_images_dir() / safe_name
    dest.write_bytes(raw_bytes)
    return f"/api/v1/uploads/tour-guides/{safe_name}"


async def save_tour_guide_credential_file(guide_id: int, file: UploadFile) -> str:
    """
    Validate and store a confidential tour guide credential document privately.
    Returns an internal filename reference (NEVER a public URL).
    """
    filename_orig = file.filename or ""
    ext_orig = Path(filename_orig).suffix.lower()
    if ext_orig not in ALLOWED_CREDENTIAL_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported credential file type. Allowed: PDF, JPEG, PNG, WEBP.",
        )

    raw_bytes = await file.read(MAX_CREDENTIAL_SIZE_BYTES + 1)
    if not raw_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded credential file is empty.",
        )
    if len(raw_bytes) > MAX_CREDENTIAL_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Credential file exceeds maximum allowed size of 10 MB.",
        )

    if ext_orig == ".pdf":
        if not raw_bytes.startswith(b"%PDF-"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded PDF file is invalid or corrupted.",
            )
        canonical_ext = ".pdf"
    else:
        canonical_ext = _validate_image_bytes(raw_bytes)

    safe_name = f"cred_{guide_id}_{uuid.uuid4().hex}{canonical_ext}"
    dest = get_tour_guide_credentials_dir() / safe_name
    dest.write_bytes(raw_bytes)
    return safe_name


def resolve_safe_storage_path(base_dir: Path, filename: str) -> Path:
    """Resolve a filename safely within base_dir, preventing path traversal."""
    clean_name = os.path.basename(filename)
    if not clean_name or clean_name != filename or ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file reference.",
        )
    resolved_base = base_dir.resolve()
    target = (resolved_base / clean_name).resolve()
    if not str(target).startswith(str(resolved_base) + os.sep):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file path.",
        )
    return target


def delete_local_tour_guide_image(image_url: str) -> None:
    """Best-effort cleanup for locally hosted tour guide gallery images."""
    prefix = "/api/v1/uploads/tour-guides/"
    if not image_url or not image_url.startswith(prefix):
        return
    filename = image_url[len(prefix):]
    try:
        path = resolve_safe_storage_path(get_tour_guide_images_dir(), filename)
        if path.is_file():
            path.unlink()
    except (HTTPException, OSError):
        pass


def delete_local_credential_file(file_reference: str) -> None:
    """Best-effort cleanup for privately stored credential files."""
    if not file_reference:
        return
    try:
        path = resolve_safe_storage_path(
            get_tour_guide_credentials_dir(),
            os.path.basename(file_reference),
        )
        if path.is_file():
            path.unlink()
    except (HTTPException, OSError):
        pass
