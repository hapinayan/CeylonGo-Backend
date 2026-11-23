# DB-01 proposed MVP schema

**49 existing mapped tables** are the smallest practical reuse-first target identified for the design below. This is a conditional logical profile. It preserves existing keys and selected supporting workflows. It has FK closure within the mapped metadata. It does not establish a bootable reduced SQL Server catalog with today's routers, migration runner or all current queries. The migration ledger and other operational objects are excluded from this count.

The design assumes native Activity supplies Adventure. Keep the EcoExperience anchor because both native Activity bookings and slots reference it. Native Eco reservations, safety, clearance and weather logic remain blocked until scope and runtime dependencies are decided. Keep motorized bike license verification and provider approval. AI planning returns a bounded, transient itinerary. Map and weather use existing published listings, coordinates and external adapters. Admin and traveler/provider roles share users.

| Workflow | Exact retained tables |
|---|---|
| Identity and provider onboarding | password_resets, provider_email_otps, provider_profiles, provider_registration_authorizations, provider_registration_drafts, users |
| Homestay | homestay_amenities, homestay_availability_blocks, homestay_bookings, homestay_images, homestays |
| Bike and bicycle | rental_availability_blocks, rental_bookings, rental_eligibility_checks, rental_locations, rental_rate_plans, rental_vehicle_images, rental_vehicles |
| Adventure using native Activity | activities, activity_booking_participants, activity_bookings, activity_images, activity_slots |
| Local tour guide | tour_guide_availability, tour_guide_bookings, tour_guide_credentials, tour_guide_images, tour_guide_languages, tour_guide_packages, tour_guide_service_areas, tour_guide_verifications, tour_guides |
| Shared booking, payment and oversight | admin_audit_logs, booking_references, booking_status_history, listing_references, notifications, payment_transactions, reviews |
| Community feed and moderation | community_comments, community_hashtags, community_post_hashtags, community_post_likes, community_post_media, community_posts, community_reports, community_saved_posts, community_user_profiles |
| Required FK anchor | eco_experiences |

## Reuse rules

- Retain native IDs and inventory semantics. Homestay uses dates and capacity, rental uses vehicle interval and blackout, Activity uses slots and participant capacity, and guide uses guide availability and package context. Confirm actor, cancellation and allocation policies before changing any writes. Native Activity currently confirms on creation.
- Keep booking status separate from payment. Pay at Location records unpaid at booking and uses existing cash handling. Proposed simulation records a transaction with an explicit demo method/source and idempotency identifier. Use existing allowed status values only after verifying every service and SQL constraint, or propose a narrowly scoped additive change. Preserve gateway records and check uniqueness plus transaction boundaries. No gateway calls or live card data enter the demo path.
- Preserve onboarding drafts, OTP and approval history, password resets, identity version columns, notifications, shared booking/listing links and audit. Keep file references and enforce private document access. Guide packages, credentials and verification records are retained to avoid changing currently reused listing and approval behavior.
- Keep Community engagement uniqueness, nesting, moderation and evidence. Saved-post state remains in the retained schema because existing post serialization and deletion use it, even if standalone save routes are later excluded. Reviews remain for Map rating joins. These are compatibility dependencies rather than new scope.
- Retiring tables does not remove SQL code already deployed or rewrite historic checksums. Preserve personal, payment and audit records according to a separately approved retention policy.

## Blocked dependencies and historical risks

- Activity slots and bookings point to EcoExperience. Native Eco reservation, age, policy, cancellation, clearance and seasonal tables require a decision on Adventure coverage and approval behavior. Do not discard safety data.
- Map catalog reads Artisan, Eco, rental locations, listing references and published reviews. Shared provider reservations handle Artisan, guide and activity. Removing Artisan from the schema now could break retained queries even without a direct FK on a retained table.
- Shared PayHere dispatch invokes Eco and advertising paths. Promotion events and attribution can refer to booking references, and refund/settlement records can retain financial history. Keep stored records and callbacks untouched pending separate approval and reconciliation.
- Community post deletion reads saved-post and report rows and may preserve evidence. SQL delete cascades and ORM cascades are separate. Test both hard-delete and evidence-preserving paths using synthetic records before changing any dependent table.
- Catalog history, messaging, audit, booking counters and availability can depend on module/native-ID links without an FK. FK closure alone cannot prove a safe removal order.

Sources: [activity booking](../../../app/routers/activity_bookings.py#L70), [rental eligibility](../../../app/routers/rental_bookings.py#L719), [map catalog](../../../app/services/map_catalog.py#L19), [provider native events](../../../app/services/provider_reservations.py#L96), [payment callback](../../../app/routers/payments.py#L650), [Community post handling](../../../app/services/community_posts.py#L950), [shared payment constraint](../../../migrations/036_align_shared_payment_status_constraints.sql#L30).

## Outside the proposed profile

Exact rows are cross-referenced in the dependency matrix. Candidate status does not authorize retirement.

### RETIREMENT CANDIDATE / HOLD

advertising_payments, ai_action_audit, ai_chat_messages, ai_chat_sessions, ai_pending_actions, artisan_business_hours, artisan_business_images, artisan_listings, artisan_product_favorites, artisan_product_images, artisan_reservations, community_follows, eco_promotion_analytics, eco_promotion_attributions, eco_promotion_event_receipts, eco_promotion_packages, eco_refund_reconciliations, eco_sponsored_promotions, eco_sponsorship_captures, eco_wishlists, homestay_promotions, payment_refunds, promotion_campaigns, promotion_events, promotion_packages, promotions, provider_settlements, rental_condition_evidence, rental_deposits, rental_incidents, rental_inspections, showcase_items, travel_plan_days, travel_plan_items, travel_plans, travel_preferences, wishlists

### BLOCKED / REVIEW

eco_age_limits, eco_availability_slots, eco_cancellation_policies, eco_cancellation_snapshots, eco_evidences, eco_experience_images, eco_practices, eco_reservations, eco_safety_rules, eco_season_restrictions, eco_slot_clearances, eco_verifications, rental_vehicle_documents

