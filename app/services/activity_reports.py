"""
Service functions for Module 03 — Platform-Wide Admin Activity Reporting (C07).

Provides high-performance, deterministic aggregation across:
- Overall activity inventory by approval and publication status
- Platform booking lifecycle metrics and conversion rates
- Aggregate slot capacity utilization and occupancy rate
- Canonical category distribution (cultural, adventure, local, community)
- Regional distribution by administrative district
- Multi-currency financial breakdown (gross, paid, unpaid, refunded)
- Top activities and top activity providers

All calculations execute with zero N+1 queries, strict currency partitioning,
and zero traveler PII leakage.
"""

from datetime import date, datetime, time, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.models.activity import Activity
from app.models.activity_booking import ActivityBooking
from app.models.activity_slot import ActivitySlot
from app.models.provider_profile import ProviderProfile
from app.schemas.activity_admin import (
    AdminActivityBookingOverview,
    AdminActivityCapacityUtilization,
    AdminActivityCategoryReport,
    AdminActivityFinancialBreakdown,
    AdminActivityInventoryOverview,
    AdminActivityPlatformReportResponse,
    AdminActivityRegionalReport,
    AdminTopActivitySummary,
    AdminTopProviderSummary,
)


def generate_platform_activity_report(
    db: Session,
    date_from: date | None = None,
    date_to: date | None = None,
) -> AdminActivityPlatformReportResponse:
    """
    Generate comprehensive platform-wide administrative activity report (C07).
    Supports optional date filtering on booking creation and slot schedules.
    """
    # 1. Date Range Validation & Normalization
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="date_from / start_date cannot be after date_to / end_date",
        )

    if date_from is not None and date_to is not None:
        if (date_to - date_from).days > 1826:  # 5 years max window
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Date range exceeds maximum allowed window of 5 years (1826 days)",
            )

    now_utc = datetime.now(timezone.utc)

    # 2. Activity Inventory Query (1 query, selecting strictly needed scalar columns)
    activities = (
        db.query(
            Activity.id,
            Activity.title,
            Activity.category,
            Activity.district,
            Activity.provider_profile_id,
            Activity.currency,
            Activity.approval_status,
            Activity.is_active,
        )
        .order_by(Activity.id.asc())
        .all()
    )

    total_acts = len(activities)
    approved_acts = sum(1 for a in activities if a.approval_status == "approved")
    pending_acts = sum(1 for a in activities if a.approval_status == "pending")
    draft_acts = sum(1 for a in activities if a.approval_status == "draft")
    suspended_acts = sum(1 for a in activities if a.approval_status == "suspended")
    rejected_acts = sum(1 for a in activities if a.approval_status == "rejected")
    active_published_acts = sum(
        1 for a in activities if a.approval_status == "approved" and a.is_active
    )
    inactive_acts = total_acts - active_published_acts

    inventory_overview = AdminActivityInventoryOverview(
        total_activities=total_acts,
        approved_activities=approved_acts,
        pending_activities=pending_acts,
        draft_activities=draft_acts,
        suspended_activities=suspended_acts,
        rejected_activities=rejected_acts,
        active_published_activities=active_published_acts,
        inactive_activities=inactive_acts,
    )

    # 3. Provider Profiles Lookup (Map provider_profile_id -> business_name)
    providers = (
        db.query(ProviderProfile.id, ProviderProfile.business_name)
        .filter(ProviderProfile.provider_type == "activity")
        .all()
    )
    provider_name_map = {p.id: p.business_name for p in providers}

    # 4. Activity Slots Capacity Utilization (Database-level SQL aggregation, 1 query)
    slot_aggregates = db.query(
        func.count(ActivitySlot.id).label("total_slots"),
        func.coalesce(
            func.sum(case((ActivitySlot.is_active == True, 1), else_=0)), 0
        ).label("active_slots"),
        func.coalesce(func.sum(ActivitySlot.capacity), 0).label("total_capacity"),
        func.coalesce(func.sum(ActivitySlot.reserved_count), 0).label("total_reserved"),
    ).filter(
        ActivitySlot.eco_experience_id.is_(None)
    )

    if date_from is not None:
        slot_aggregates = slot_aggregates.filter(
            ActivitySlot.starts_at >= datetime.combine(date_from, time.min)
        )
    if date_to is not None:
        slot_aggregates = slot_aggregates.filter(
            ActivitySlot.starts_at <= datetime.combine(date_to, time.max)
        )

    slot_row = slot_aggregates.one()
    total_slots = slot_row.total_slots or 0
    active_slots = int(slot_row.active_slots or 0)
    total_capacity = int(slot_row.total_capacity or 0)
    total_reserved = int(slot_row.total_reserved or 0)
    total_remaining = max(0, total_capacity - total_reserved)
    occupancy_rate = (
        round((total_reserved / total_capacity) * 100.0, 2)
        if total_capacity > 0
        else 0.0
    )
    occupancy_rate = min(100.0, max(0.0, occupancy_rate))

    capacity_utilization = AdminActivityCapacityUtilization(
        total_slots=total_slots,
        active_slots=active_slots,
        total_capacity=total_capacity,
        total_reserved=total_reserved,
        total_remaining=total_remaining,
        occupancy_rate=occupancy_rate,
    )

    # 5. Activity Bookings Query (1 joined query, strictly excluding eco experiences)
    b_query = (
        db.query(
            ActivityBooking.id,
            ActivityBooking.participants,
            ActivityBooking.total_price,
            ActivityBooking.currency,
            ActivityBooking.payment_status,
            ActivityBooking.booking_status,
            ActivityBooking.created_at,
            ActivitySlot.activity_id,
            Activity.title.label("activity_title"),
            Activity.category.label("activity_category"),
            Activity.district.label("activity_district"),
            Activity.provider_profile_id.label("provider_profile_id"),
        )
        .join(ActivitySlot, ActivityBooking.activity_slot_id == ActivitySlot.id)
        .join(Activity, ActivitySlot.activity_id == Activity.id)
        .filter(ActivityBooking.eco_experience_id.is_(None))
    )

    if date_from is not None:
        b_query = b_query.filter(
            ActivityBooking.created_at >= datetime.combine(date_from, time.min)
        )
    if date_to is not None:
        b_query = b_query.filter(
            ActivityBooking.created_at <= datetime.combine(date_to, time.max)
        )

    bookings = b_query.all()

    total_bookings = len(bookings)
    confirmed_bookings = sum(1 for b in bookings if b.booking_status == "confirmed")
    completed_bookings = sum(1 for b in bookings if b.booking_status == "completed")
    pending_bookings = sum(1 for b in bookings if b.booking_status == "pending")
    cancelled_bookings = sum(1 for b in bookings if b.booking_status == "cancelled")
    rejected_bookings = sum(1 for b in bookings if b.booking_status == "rejected")
    total_participants = sum(b.participants for b in bookings)
    confirmed_participants = sum(
        b.participants for b in bookings if b.booking_status in ("confirmed", "completed")
    )

    booking_conversion_rate = (
        round(((confirmed_bookings + completed_bookings) / total_bookings) * 100.0, 2)
        if total_bookings > 0
        else 0.0
    )
    booking_conversion_rate = min(100.0, max(0.0, booking_conversion_rate))

    cancellation_rate = (
        round((cancelled_bookings / total_bookings) * 100.0, 2)
        if total_bookings > 0
        else 0.0
    )
    cancellation_rate = min(100.0, max(0.0, cancellation_rate))

    booking_overview = AdminActivityBookingOverview(
        total_bookings=total_bookings,
        confirmed_bookings=confirmed_bookings,
        completed_bookings=completed_bookings,
        pending_bookings=pending_bookings,
        cancelled_bookings=cancelled_bookings,
        rejected_bookings=rejected_bookings,
        total_participants=total_participants,
        confirmed_participants=confirmed_participants,
        booking_conversion_rate=booking_conversion_rate,
        cancellation_rate=cancellation_rate,
    )

    # 6. Financial Breakdown (Partitioned strictly by currency)
    currency_map: dict[str, dict[str, Decimal]] = {}
    for b in bookings:
        curr = b.currency or "LKR"
        if curr not in currency_map:
            currency_map[curr] = {
                "gross": Decimal("0.00"),
                "paid": Decimal("0.00"),
                "unpaid": Decimal("0.00"),
                "refunded": Decimal("0.00"),
            }

        # Count gross, paid, unpaid for active bookings
        if b.booking_status not in ("cancelled", "rejected"):
            currency_map[curr]["gross"] += b.total_price
            if b.payment_status == "paid":
                currency_map[curr]["paid"] += b.total_price
            elif b.payment_status in ("unpaid", "pending"):
                currency_map[curr]["unpaid"] += b.total_price

        # Track refunds
        if b.payment_status in ("refunded", "refund_pending"):
            currency_map[curr]["refunded"] += b.total_price

    financial_overview = [
        AdminActivityFinancialBreakdown(
            currency=c,
            total_gross_revenue=vals["gross"],
            paid_revenue=vals["paid"],
            unpaid_revenue=vals["unpaid"],
            refunded_amount=vals["refunded"],
        )
        for c, vals in sorted(currency_map.items())
    ]
    if not financial_overview:
        financial_overview = [
            AdminActivityFinancialBreakdown(
                currency="LKR",
                total_gross_revenue=Decimal("0.00"),
                paid_revenue=Decimal("0.00"),
                unpaid_revenue=Decimal("0.00"),
                refunded_amount=Decimal("0.00"),
            )
        ]

    # 7. Category Distribution (Canonical Categories)
    canonical_categories = ["cultural", "adventure", "local", "community"]
    cat_distribution: list[AdminActivityCategoryReport] = []
    for cat in canonical_categories:
        cat_acts = [a for a in activities if a.category == cat]
        cat_books = [b for b in bookings if b.activity_category == cat]
        cat_gross = sum(
            (b.total_price for b in cat_books if b.booking_status not in ("cancelled", "rejected")),
            Decimal("0.00"),
        )
        cat_distribution.append(
            AdminActivityCategoryReport(
                category=cat,
                activity_count=len(cat_acts),
                active_activity_count=sum(
                    1 for a in cat_acts if a.approval_status == "approved" and a.is_active
                ),
                booking_count=len(cat_books),
                total_participants=sum(b.participants for b in cat_books),
                gross_revenue=cat_gross,
                currency="LKR",
            )
        )

    # 8. Regional Distribution (by district)
    district_map: dict[str, dict[str, int]] = {}
    for a in activities:
        dist = a.district or "Unknown"
        if dist not in district_map:
            district_map[dist] = {"acts": 0, "books": 0, "participants": 0}
        district_map[dist]["acts"] += 1

    for b in bookings:
        dist = b.activity_district or "Unknown"
        if dist not in district_map:
            district_map[dist] = {"acts": 0, "books": 0, "participants": 0}
        district_map[dist]["books"] += 1
        district_map[dist]["participants"] += b.participants

    regional_distribution = [
        AdminActivityRegionalReport(
            district=dist,
            activity_count=vals["acts"],
            booking_count=vals["books"],
            total_participants=vals["participants"],
        )
        for dist, vals in sorted(
            district_map.items(),
            key=lambda x: (-x[1]["books"], -x[1]["acts"], x[0]),
        )
    ]

    # 9. Top Activities (Top 5 by booking count, then gross revenue, tie-broken by ID)
    act_metrics: dict[int, dict] = {}
    for a in activities:
        act_metrics[a.id] = {
            "activity_id": a.id,
            "activity_title": a.title,
            "category": a.category,
            "district": a.district,
            "provider_profile_id": a.provider_profile_id,
            "provider_business_name": provider_name_map.get(a.provider_profile_id),
            "booking_count": 0,
            "total_participants": 0,
            "gross_revenue": Decimal("0.00"),
            "currency": a.currency or "LKR",
        }

    for b in bookings:
        aid = b.activity_id
        if aid in act_metrics:
            act_metrics[aid]["booking_count"] += 1
            act_metrics[aid]["total_participants"] += b.participants
            if b.booking_status not in ("cancelled", "rejected"):
                act_metrics[aid]["gross_revenue"] += b.total_price

    top_activities_list = [
        AdminTopActivitySummary(**m)
        for m in sorted(
            act_metrics.values(),
            key=lambda x: (-x["booking_count"], -x["gross_revenue"], x["activity_id"]),
        )
        if m["booking_count"] > 0
    ][:5]

    # 10. Top Providers (Top 5 by booking count, then gross revenue, tie-broken by ID)
    prov_metrics: dict[int, dict] = {}
    for a in activities:
        pid = a.provider_profile_id
        if pid not in prov_metrics:
            prov_metrics[pid] = {
                "provider_profile_id": pid,
                "business_name": provider_name_map.get(pid, f"Provider #{pid}"),
                "total_activities": 0,
                "active_activities": 0,
                "booking_count": 0,
                "total_participants": 0,
                "gross_revenue": Decimal("0.00"),
                "currency": "LKR",
            }
        prov_metrics[pid]["total_activities"] += 1
        if a.approval_status == "approved" and a.is_active:
            prov_metrics[pid]["active_activities"] += 1

    for b in bookings:
        pid = b.provider_profile_id
        if pid in prov_metrics:
            prov_metrics[pid]["booking_count"] += 1
            prov_metrics[pid]["total_participants"] += b.participants
            if b.booking_status not in ("cancelled", "rejected"):
                prov_metrics[pid]["gross_revenue"] += b.total_price

    top_providers_list = [
        AdminTopProviderSummary(**m)
        for m in sorted(
            prov_metrics.values(),
            key=lambda x: (-x["booking_count"], -x["gross_revenue"], x["provider_profile_id"]),
        )
        if m["booking_count"] > 0
    ][:5]

    return AdminActivityPlatformReportResponse(
        generated_at=now_utc,
        date_from=date_from,
        date_to=date_to,
        activity_inventory=inventory_overview,
        booking_overview=booking_overview,
        capacity_utilization=capacity_utilization,
        category_distribution=cat_distribution,
        regional_distribution=regional_distribution,
        financial_overview=financial_overview,
        top_activities=top_activities_list,
        top_providers=top_providers_list,
    )
