# Phase 02A baseline

**Application:** develop at 3bf9822f6be99780d0e25c8752ed263ac407c3c6. Local origin/develop at the same SHA. Isolated branch: feature/academic-mvp-backend-simplification.
**Requirements:** [approved Academic MVP V1.1](../CEYLONGO_ACADEMIC_MVP_MASTER_REQUIREMENTS_V1.1.md), commit 84ea50f341bbe20fdfd057087a9dcae27f6ddc3d, blob 31859af08c1bd0f88084205178b7cb0fdbe88582. Only the Markdown blob was materialized.

- 648 unique mounted FastAPI method/path operations across 75 included routers and one direct health route. 628 OpenAPI operations and 20 hidden operations. Four framework documentation routes and their eight GET/HEAD combinations are excluded.
- 99 configured mapped classes and 99 metadata tables. 43 forward migrations.
- 186 KEEP, 39 SIMPLIFY, 246 REMOVE CANDIDATE, 15 SHARED DEPENDENCY, 162 REVIEW. These reproduce the Phase 01 screen; none imply removal authorization.
- No lifespan started. Socket and ODBC connection entry points blocked. No server, integration suite, or migration ran.
- The CSV is the mounted and OpenAPI inventory, including operation IDs and hidden rows. No exact method/path duplicates were found. Parameterized route overlap was not ruled out.
- Client references are literal source matches, not request traces. Role descriptions record dependency evidence and indicate where inline guards remain to be checked. Import edges are module-level and may include branches unused by the operation. A blank result does not prove absence.

## Router inventory

Registration: [app/main.py](../../../app/main.py#L327). Counts include hidden operations. K / S / R / H / V follows the classification order above.

| Registration | Operations | K / S / R / H / V |
|---|---:|---|
| auth_router | 8 | 3 / 0 / 0 / 4 / 1 |
| admin_auth_router | 2 | 0 / 0 / 0 / 2 / 0 |
| admin_audit_logs_router | 1 | 0 / 0 / 0 / 0 / 1 |
| admin_dashboard_router | 1 | 0 / 0 / 0 / 0 / 1 |
| admin_promotions_router | 1 | 0 / 0 / 1 / 0 / 0 |
| admin_reports_router | 1 | 0 / 0 / 0 / 0 / 1 |
| admin_support_router | 1 | 0 / 0 / 1 / 0 / 0 |
| admin_reviews_router | 1 | 0 / 0 / 0 / 0 / 1 |
| admin_travelers_router | 4 | 4 / 0 / 0 / 0 / 0 |
| artisan_listings_router | 12 | 0 / 0 / 12 / 0 / 0 |
| showcase_items_router | 7 | 0 / 0 / 7 / 0 / 0 |
| artisan_images_router | 16 | 0 / 0 / 16 / 0 / 0 |
| artisan_uploads_router | 2 | 0 / 0 / 2 / 0 / 0 |
| artisan_business_hours_router | 7 | 0 / 0 / 7 / 0 / 0 |
| artisan_dashboard_router | 3 | 0 / 0 / 3 / 0 / 0 |
| artisan_reviews_router | 9 | 0 / 0 / 9 / 0 / 0 |
| artisan_wishlists_router | 15 | 0 / 0 / 15 / 0 / 0 |
| artisan_reservations_router | 8 | 0 / 0 / 8 / 0 / 0 |
| admin_artisan_router | 16 | 0 / 0 / 16 / 0 / 0 |
| promotions_router | 8 | 0 / 0 / 8 / 0 / 0 |
| homestay_campaigns_router | 19 | 0 / 0 / 19 / 0 / 0 |
| homestay_router | 18 | 12 / 4 / 0 / 0 / 2 |
| admin_homestays_router | 5 | 1 / 0 / 0 / 0 / 4 |
| booking_router | 14 | 9 / 1 / 1 / 0 / 3 |
| homestay_reviews_router | 4 | 0 / 0 / 0 / 0 / 4 |
| promotion_router | 5 | 0 / 0 / 5 / 0 / 0 |
| notifications_router | 4 | 0 / 0 / 0 / 4 / 0 |
| payments_router | 15 | 0 / 4 / 9 / 0 / 2 |
| provider_otp_router | 2 | 0 / 0 / 0 / 2 / 0 |
| provider_registration_router | 5 | 0 / 5 / 0 / 0 / 0 |
| admin_providers_router | 5 | 4 / 0 / 0 / 0 / 1 |
| provider_auth_router | 7 | 7 / 0 / 0 / 0 / 0 |
| provider_dashboard_router | 9 | 0 / 0 / 2 / 0 / 7 |
| provider_reviews_router | 2 | 0 / 0 / 0 / 0 / 2 |
| provider_community_router | 3 | 0 / 0 / 0 / 0 / 3 |
| provider_workspace_router | 4 | 0 / 0 / 0 / 0 / 4 |
| provider_reservations_router | 11 | 3 / 2 / 1 / 0 / 5 |
| eco_reservations_router | 19 | 6 / 2 / 3 / 0 / 8 |
| provider_activities_router | 5 | 5 / 0 / 0 / 0 / 0 |
| admin_activities_router | 4 | 3 / 0 / 0 / 0 / 1 |
| activity_slots_router | 5 | 3 / 0 / 0 / 0 / 2 |
| activity_bookings_router | 8 | 7 / 0 / 0 / 0 / 1 |
| eco_experiences_router | 33 | 11 / 1 / 2 / 0 / 19 |
| admin_eco_router | 11 | 6 / 1 / 0 / 0 / 4 |
| uploads_router | 1 | 0 / 0 / 0 / 1 / 0 |
| wishlists_router | 7 | 0 / 0 / 7 / 0 / 0 |
| eco_promotions_router | 12 | 0 / 0 / 12 / 0 / 0 |
| admin_eco_promotions_router | 12 | 0 / 0 / 12 / 0 / 0 |
| rental_vehicles_router | 17 | 7 / 5 / 0 / 0 / 5 |
| rental_pickups_router | 3 | 0 / 0 / 0 / 0 / 3 |
| rental_bookings_router | 17 | 6 / 1 / 7 / 0 / 3 |
| rental_locations_router | 1 | 1 / 0 / 0 / 0 / 0 |
| rental_reviews_router | 2 | 0 / 0 / 0 / 0 / 2 |
| rental_wishlists_router | 8 | 0 / 0 / 8 / 0 / 0 |
| rental_providers_router | 44 | 18 / 9 / 12 / 0 / 5 |
| admin_rentals_router | 25 | 5 / 1 / 11 / 0 / 8 |
| rental_eligibility_router | 6 | 0 / 0 / 0 / 0 / 6 |
| tour_guide_uploads_router | 1 | 0 / 0 / 0 / 1 / 0 |
| tour_guide_m034_public_router | 1 | 0 / 0 / 0 / 0 / 1 |
| tour_guide_public_router | 8 | 4 / 1 / 0 / 0 / 3 |
| tour_guide_traveler_bookings_router | 4 | 4 / 0 / 0 / 0 / 0 |
| tour_guide_payments_router | 4 | 0 / 2 / 2 / 0 / 0 |
| tour_guide_phase5_traveler_router | 8 | 0 / 0 / 6 / 0 / 2 |
| tour_guide_router | 38 | 27 / 0 / 0 / 0 / 11 |
| tour_guide_m034_provider_router | 3 | 0 / 0 / 0 / 0 / 3 |
| tour_guide_provider_bookings_router | 9 | 8 / 0 / 0 / 0 / 1 |
| tour_guide_phase5_provider_router | 9 | 0 / 0 / 6 / 0 / 3 |
| tour_guide_phase5_admin_router | 8 | 0 / 0 / 2 / 0 / 6 |
| tour_guide_m034_admin_router | 1 | 0 / 0 / 0 / 0 / 1 |
| admin_tour_guides_router | 12 | 8 / 0 / 0 / 0 / 4 |
| weather_router | 3 | 3 / 0 / 0 / 0 / 0 |
| interactive_map_router | 5 | 2 / 0 / 0 / 0 / 3 |
| admin_map_router | 3 | 0 / 0 / 0 / 0 / 3 |
| community_uploads_router | 1 | 0 / 0 / 0 / 0 / 1 |
| community_router | 34 | 9 / 0 / 14 / 0 / 11 |
| app | 1 | 0 / 0 / 0 / 1 / 0 |

## Migration inventory

Forward files are numbered 001?043. SHA-256 is over isolated checkout bytes. The DDL columns show lexical CREATE/ALTER and FK references and can include conditional statements. They do not establish applied state or a complete dependency graph. No migration runner command ran. Migration 001 requires pre-existing users. 039 extends 028 and uses 027 listing references. 040 and 043 alter users. 041 explicitly requires the ledger through 040 plus Eco and shared refund prerequisites. 042 requires 041.

| Migration | SHA-256 | CREATE/ALTER tables | FK targets |
|---|---|---|---|
| 001_create_password_resets.sql | 27c437a71dfaba493c97eaee8c9518f827db439ec66553b69ff66b754b43618a | password_resets | users |
| 002_add_google_sign_in_to_users.sql | 761e45740b4fab0f09820e42b88e4c3d19add45175379381d6e5fba77475a028 | users | - |
| 003_create_provider_profiles.sql | bd9b077644aad598a288255b74bca2b808aab4c13e6214d042ff47b6806b420a | provider_profiles | users |
| 004_extend_provider_registration.sql | 10861e95f699860947d58d2ec0aa691c0fa715bf55baa6daedc257cf7b105756 | provider_email_otps, provider_profiles, provider_registration_authorizations | IF |
| 005_provider_registration_drafts.sql | c6462cfea74e870aacddc9493dae9c61d3621e6cd469cf3d6db7692aae3f4ea4 | provider_registration_drafts | - |
| 006_add_personal_details_fields.sql | 6901d07beeefd307373e3d851a8cad50d07f2f6d2a9273b9fb273803d1013c37 | provider_profiles, provider_registration_drafts | - |
| 007_create_homestay_tables.sql | 175de8df46cd6481dac9233dc2419984564760cfdf226d9f69a9bc7e10031bb7 | homestay_images, homestays | homestays |
| 008_create_homestay_amenities.sql | 7d2968580388c795dfcfea49cc25d35aa2833ed4effa1da90d832a80bdd1816e | homestay_amenities | homestays |
| 009_add_homestay_owner.sql | 9b649a6c3b15784f189b054d676ee75bed273babb8f3959c0df942e922270a55 | homestays | users |
| 010_create_homestay_bookings.sql | c03375584611df3eae49f44885cfebacfbece79bcbd0e3fd9e29a58fbf206862 | homestay_bookings | homestays, users |
| 011_create_homestay_promotions.sql | 37c136526345e63492d28ffbd9bf54036e15d826206933fa67dd7cfd50416f16 | homestay_promotions | homestays |
| 012_create_artisan_listings.sql | 9ccb3c7c92546ae45d21947cd56b8e3de067a08c7c48d4dc8a4b709e98e9de1c | artisan_listings | provider_profiles |
| 013_create_showcase_items.sql | ba961c2bfdce380c73e6430892ade2f8c42c389fb6a6146e5bdce5b32da89f54 | showcase_items | artisan_listings |
| 014_create_artisan_promotions.sql | 03b568555d7329a474168cc9d69d4e255dc5e40f4bea9854effa28dcc1ff0dc1 | promotions | artisan_listings, showcase_items |
| 015_add_homestay_provider_profile.sql | 8780ccb042ebefbe60201eaa09b700948a890bd4505b00d700f867aa23c001ef | homestays | provider_profiles |
| 016_add_rental_provider_type.sql | 4bf0089963619accdb4a65c11176363dac744313c9d38e9f5913d00fa56098b6 | provider_profiles | - |
| 017_create_eco_tourism_core.sql | c44eaddd938656660e433ebc89639268d5cef6fe2877d16e9930bbba72ab3a4e | eco_evidences, eco_experience_images, eco_experiences, eco_practices, eco_safety_rules, eco_season_restrictions, eco_verifications | eco_experiences, eco_practices, homestays, provider_profiles, users |
| 018_create_eco_wishlists.sql | ec080d6bea6fa3ea1c0e1047f63639aadbcf386e30af90149a74d809e227ecb3 | eco_wishlists | eco_experiences, users |
| 019_create_rental_tables.sql | 185f00fc9ff79e59c07d3e7b6a73dd855b6fef938faf2e4668a13fe196a90d7a | rental_bookings, rental_vehicles | provider_profiles, rental_vehicles, users |
| 020_create_eco_promotions.sql | 558014ef41e40c3d7e2fc6e89e77b387747efb20f32c82f688cf133e1e154514 | eco_promotion_analytics, eco_promotion_packages, eco_sponsored_promotions | eco_experiences, eco_promotion_packages, eco_sponsored_promotions, provider_profiles, users |
| 021_create_activities_module.sql | ce62ca9a49b57a391fb260d6f5d9365fec5e4ae5409e7b9fa1bd09eab82d81c2 | activities, activity_bookings, activity_images, activity_slots | activities, activity_slots, provider_profiles, users |
| 022_create_local_tour_guide_module.sql | dcdef61f47bea6f1a5906e7768a8acbf398f7267c9fef9cbd4304cf3902deb76 | tour_guide_availability, tour_guide_bookings, tour_guide_credentials, tour_guide_images, tour_guide_languages, tour_guide_packages, tour_guide_service_areas, tour_guide_verifications, tour_guides | CONSTRAINT, provider_profiles, tour_guide_credentials, tour_guide_packages, tour_guides, users |
| 023_extend_homestay_module.sql | 1d94aef4d01f17ec37437e02e77a34edefafaeb144c841c747d7fc58363c80d4 | homestay_availability_blocks, homestay_bookings, homestays | homestays, users |
| 024_extend_artisan_module.sql | e34ca63cabff89089063e1647fff63e13b7d55789e3f323f9cc709a4041bcdc1 | artisan_business_hours, artisan_business_images, artisan_listings, artisan_product_images, artisan_reservations, showcase_items | artisan_listings, showcase_items, users |
| 025_extend_activities_eco_module.sql | 2c4d5f27d2656ea56a18394fde2c3ff6c5bffeab47925071f9acbde786fffc92 | activity_booking_participants, activity_bookings, activity_slots | activity_bookings, eco_experiences |
| 026_extend_rental_module.sql | 843e2d0f1c19344ffdd46c79f9749c54972a4c83895e32f5e9d1e446879a8475 | rental_availability_blocks, rental_bookings, rental_condition_evidence, rental_deposits, rental_eligibility_checks, rental_incidents, rental_inspections, rental_locations, rental_rate_plans, rental_vehicle_documents, rental_vehicle_images, rental_vehicles | provider_profiles, rental_bookings, rental_inspections, rental_locations, rental_vehicles, users |
| 027_create_shared_infrastructure.sql | 6ab4f37d5f5f438487261475958015c76ffc98b72da38c075503af68afad9f53 | admin_audit_logs, booking_references, booking_status_history, listing_references, notifications, payment_refunds, payment_transactions, promotion_campaigns, promotion_events, promotion_packages, provider_settlements, reviews, wishlists | booking_references, listing_references, payment_transactions, promotion_campaigns, provider_profiles, users |
| 028_create_travel_community_module.sql | 4fa15dc924de27d783e59442a789f632d88a88a4c12bf9e51704c2c720a94867 | community_comments, community_follows, community_hashtags, community_post_hashtags, community_post_likes, community_post_media, community_posts, community_reports, community_saved_posts | community_comments, community_hashtags, community_posts, users |
| 029_create_ai_travel_planner_module.sql | 391cca5890958029063ef34cbc98f2a8ccd1b8d5403b55e2ffda9c897c0a84e6 | ai_action_audit, ai_chat_messages, ai_chat_sessions, ai_pending_actions, travel_plan_days, travel_plan_items, travel_plans, travel_preferences | ai_chat_sessions, ai_pending_actions, listing_references, travel_plan_days, travel_plans, users |
| 030_extend_homestay_final_requirements.sql | 6afa724589967b1195411513e7af18592416d856365515c0f4f9f59c8a04e318 | advertising_payments, homestay_bookings, homestay_promotions, payment_transactions, promotion_campaigns | listing_references, promotion_campaigns, provider_profiles |
| 031_extend_promotion_booking_attribution.sql | e9d6d7b3f06e3d954ff44ddb75612a06f84f0e1bcbc81f72d1804806babbcf52 | promotion_campaigns, promotion_events | booking_references |
| 032_enforce_tour_guide_coordinate_pairs.sql | 2338a1b04caee858a8ee1f2738b83c74f2811c785919ce57d4f7ce03d3914234 | tour_guide_packages, tour_guides | - |
| 033_extend_rental_module_final_gaps.sql | 517aaea10311ca9a1d4f8c277cc2364c81f8195d58d129ca4106e3c5d5bb405c | rental_eligibility_checks, rental_inspections, rental_vehicle_documents, rental_vehicles | - |
| 034_create_eco_reservations.sql | 2c2b405fed72f4830a95f5f19e4a5f061342c05e7e89f6526bed80ed6ef35b38 | booking_references, eco_availability_slots, eco_reservations | eco_availability_slots, eco_experiences, users |
| 035_extend_tour_guide_module_final_gaps.sql | f3e7748ccd803472f1b1eb035552dfe5ac375f90cce15479eb83a55ab514dcd9 | tour_guide_bookings, tour_guide_packages, tour_guides | - |
| 036_align_shared_payment_status_constraints.sql | d6bc3850b0fdd19dd9fedc4445f301c45a4a2c1ce3c82e3c2bbe4d7c7a600756 | payment_refunds, payment_transactions, provider_settlements | - |
| 037_extend_artisan_reservation_rejection.sql | f52c90caa6ce7ccf7df88e3c2847006be1187bd2a1cddf26328a6b8910905fad | artisan_reservations | users |
| 038_create_artisan_product_favorites.sql | 89dd3a123c1d5837ae4863271a35638ae56c2cad5da360bdb0ef5808c7162e38 | artisan_product_favorites | showcase_items, users |
| 039_extend_travel_community_module.sql | 05791ca69991dea29f9d00880f2c61253f519a402cbfdc22ac1de6d095e737be | community_comments, community_hashtags, community_posts, community_user_profiles | listing_references, users |
| 040_add_users_credential_version.sql | 9ab202c3d7718a45e0bddb9f58b751042d266671ec1ff8daf5903dbb22a10489 | users | - |
| 041_extend_eco_native_safety_and_refunds.sql | 85cad3510a93ede800dc1eae701f08f43aecf4b9f0b6c5508db8860293f63726 | eco_age_limits, eco_cancellation_policies, eco_cancellation_snapshots, eco_refund_reconciliations, eco_reservations, eco_slot_clearances | eco_availability_slots, eco_experiences, eco_reservations, payment_refunds, users |
| 042_create_eco_sponsorship_ledger.sql | cc9d36f897df5dd94f5a9ade1051e72a97765609dc0dc2bead8ed7681e81c424 | eco_promotion_attributions, eco_promotion_event_receipts, eco_sponsorship_captures | booking_references, eco_sponsored_promotions |
| 043_add_user_auth_version.sql | 7f28187b9f95560310c056dc46ca3a5f53fb6e555192370ef15e373040e66d33 | users | - |

## Original worktree status captured at preflight

Original branch and tracked modifications preserved. Modified file SHA-256 values: {'README.md': 'eccd90c4e04a568963d247c66a80355011996da3bcecd81e83bf5f3553771d5e', 'scripts/migrate.py': '4971f765548b74bf83162a7fc9d9b0c9255c924ce0b3f4f4deacf2adfe7b7ae9'}.

<details><summary>Full porcelain status, including all untracked files</summary>

```text
## develop...origin/develop
 M README.md
 M scripts/migrate.py
?? .agents/skills/archify/LICENSE
?? .agents/skills/archify/LICENSE-MIT
?? .agents/skills/archify/NOTICE
?? .agents/skills/archify/SKILL.md
?? .agents/skills/archify/THIRD_PARTY_NOTICES.md
?? .agents/skills/archify/assets/JetBrainsMono-OFL.txt
?? .agents/skills/archify/assets/template.html
?? .agents/skills/archify/bin/archify.mjs
?? .agents/skills/archify/bin/delivery-update.mjs
?? .agents/skills/archify/bin/finalize.mjs
?? .agents/skills/archify/bin/open-artifact.mjs
?? .agents/skills/archify/bin/preview.mjs
?? .agents/skills/archify/bin/recover-output.mjs
?? .agents/skills/archify/bin/visual-check.mjs
?? .agents/skills/archify/brand-marks/README.md
?? .agents/skills/archify/brand-marks/catalog.json
?? .agents/skills/archify/delta/architecture-delta.mjs
?? .agents/skills/archify/examples/agent-run.lifecycle.json
?? .agents/skills/archify/examples/agent-tool-call.workflow.json
?? .agents/skills/archify/examples/archify-dev-activity.timeline.json
?? .agents/skills/archify/examples/archify-repository.tree.json
?? .agents/skills/archify/examples/async-job-roundtrip.sequence.json
?? .agents/skills/archify/examples/brand-aware-delivery.architecture.json
?? .agents/skills/archify/examples/cache-miss-request.sequence.json
?? .agents/skills/archify/examples/checkout-platform.base.architecture.json
?? .agents/skills/archify/examples/checkout-platform.head.architecture.json
?? .agents/skills/archify/examples/checkout-request.waterfall.json
?? .agents/skills/archify/examples/class-payment-processors-rendered.html
?? .agents/skills/archify/examples/class-payments-rendered.html
?? .agents/skills/archify/examples/dataflow-product-analytics.html
?? .agents/skills/archify/examples/delivery.explainer.json
?? .agents/skills/archify/examples/deployment-release.lifecycle.json
?? .agents/skills/archify/examples/erd-orders-rendered.html
?? .agents/skills/archify/examples/event-stream.dataflow.json
?? .agents/skills/archify/examples/example-rebuild.waterfall.json
?? .agents/skills/archify/examples/holiday-planning.workflow.json
?? .agents/skills/archify/examples/incident-response.workflow.json
?? .agents/skills/archify/examples/lifecycle-agent-run.html
?? .agents/skills/archify/examples/locales/de.json
?? .agents/skills/archify/examples/locales/fr.json
?? .agents/skills/archify/examples/locales/fr.partial.json
?? .agents/skills/archify/examples/locales/it.json
?? .agents/skills/archify/examples/locales/ja.json
?? .agents/skills/archify/examples/locales/pt.json
?? .agents/skills/archify/examples/locales/ru.json
?? .agents/skills/archify/examples/orders.erd.json
?? .agents/skills/archify/examples/payment-incident.timeline.json
?? .agents/skills/archify/examples/payment-platform.tree.json
?? .agents/skills/archify/examples/payment-processors.class.json
?? .agents/skills/archify/examples/payments.class.json
?? .agents/skills/archify/examples/product-analytics.dataflow.json
?? .agents/skills/archify/examples/production-deployment.architecture.json
?? .agents/skills/archify/examples/release-delivery.workflow.json
?? .agents/skills/archify/examples/sequence-cache-miss-request.html
?? .agents/skills/archify/examples/subscription-billing-rendered.html
?? .agents/skills/archify/examples/subscription-billing.erd.json
?? .agents/skills/archify/examples/timeline-archify-dev-activity-rendered.html
?? .agents/skills/archify/examples/timeline-payment-incident-rendered.html
?? .agents/skills/archify/examples/tree-archify-repository-rendered.html
?? .agents/skills/archify/examples/tree-payment-platform-rendered.html
?? .agents/skills/archify/examples/waterfall-checkout-request-rendered.html
?? .agents/skills/archify/examples/waterfall-example-rebuild-rendered.html
?? .agents/skills/archify/examples/web-app-rendered.html
?? .agents/skills/archify/examples/web-app.architecture.json
?? .agents/skills/archify/examples/workflow-agent-tool-call-rendered.html
?? .agents/skills/archify/explainer/browser-check.mjs
?? .agents/skills/archify/explainer/charts.mjs
?? .agents/skills/archify/explainer/check.mjs
?? .agents/skills/archify/explainer/diagrams.mjs
?? .agents/skills/archify/explainer/diff.mjs
?? .agents/skills/archify/explainer/inline.mjs
?? .agents/skills/archify/explainer/layout.mjs
?? .agents/skills/archify/explainer/page-bundle.mjs
?? .agents/skills/archify/explainer/parse.mjs
?? .agents/skills/archify/explainer/patch.mjs
?? .agents/skills/archify/explainer/prepare.mjs
?? .agents/skills/archify/explainer/reader.css
?? .agents/skills/archify/explainer/reader.js
?? .agents/skills/archify/explainer/render.mjs
?? .agents/skills/archify/explainer/revision.mjs
?? .agents/skills/archify/explainer/sources.mjs
?? .agents/skills/archify/explainer/validate.mjs
?? .agents/skills/archify/explainer/vendor/pele/LICENSE
?? .agents/skills/archify/explainer/vendor/pele/README.md
?? .agents/skills/archify/explainer/vendor/pele/chunks/3DKXF73K.js
?? .agents/skills/archify/explainer/vendor/pele/chunks/42UTT654.js
?? .agents/skills/archify/explainer/vendor/pele/chunks/DKTLEL5E.js
?? .agents/skills/archify/explainer/vendor/pele/chunks/EEIX5ZYH.js
?? .agents/skills/archify/explainer/vendor/pele/chunks/EJ5KOXYD.js
?? .agents/skills/archify/explainer/vendor/pele/chunks/GUJTEFZK.js
?? .agents/skills/archify/explainer/vendor/pele/chunks/IZOKQ2GT.js
?? .agents/skills/archify/explainer/vendor/pele/chunks/L4LY553T.js
?? .agents/skills/archify/explainer/vendor/pele/chunks/MSKIJHB2.js
?? .agents/skills/archify/explainer/vendor/pele/chunks/RVARI57O.js
?? .agents/skills/archify/explainer/vendor/pele/chunks/UKSIWDDN.js
?? .agents/skills/archify/explainer/vendor/pele/chunks/UPTXNAXN.js
?? .agents/skills/archify/explainer/vendor/pele/chunks/XTDUIGUI.js
?? .agents/skills/archify/explainer/vendor/pele/chunks/YAYTP6L5.js
?? .agents/skills/archify/explainer/vendor/pele/core.js
?? .agents/skills/archify/explainer/vendor/pele/diagrams/pie.js
?? .agents/skills/archify/explainer/vendor/pele/diagrams/quadrantChart.js
?? .agents/skills/archify/explainer/vendor/pele/diagrams/radar.js
?? .agents/skills/archify/explainer/vendor/pele/diagrams/sankey.js
?? .agents/skills/archify/explainer/vendor/pele/diagrams/venn.js
?? .agents/skills/archify/explainer/vendor/pele/diagrams/xychart.js
?? .agents/skills/archify/explainer/writing.mjs
?? .agents/skills/archify/locales/README.md
?? .agents/skills/archify/locales/ar.json
?? .agents/skills/archify/locales/en.json
?? .agents/skills/archify/locales/es.json
?? .agents/skills/archify/locales/ko.json
?? .agents/skills/archify/locales/manifest.json
?? .agents/skills/archify/locales/zh-CN.json
?? .agents/skills/archify/locales/zh-TW.json
?? .agents/skills/archify/migrations/workflow-v2.mjs
?? .agents/skills/archify/package.json
?? .agents/skills/archify/recipes/scenarios.mjs
?? .agents/skills/archify/references/architecture-layout-repair.md
?? .agents/skills/archify/references/authoring-contract.md
?? .agents/skills/archify/references/authoring-defaults.md
?? .agents/skills/archify/references/brand-marks.md
?? .agents/skills/archify/references/delivery-contract.md
?? .agents/skills/archify/references/explainer-authoring.md
?? .agents/skills/archify/references/repository-authoring.md
?? .agents/skills/archify/references/update-awareness.md
?? .agents/skills/archify/references/viewer-runtime.md
?? .agents/skills/archify/renderers/architecture/grid.mjs
?? .agents/skills/archify/renderers/architecture/labels.mjs
?? .agents/skills/archify/renderers/architecture/render-architecture.mjs
?? .agents/skills/archify/renderers/architecture/routing.mjs
?? .agents/skills/archify/renderers/class/README.md
?? .agents/skills/archify/renderers/class/render-class.mjs
?? .agents/skills/archify/renderers/dataflow/README.md
?? .agents/skills/archify/renderers/dataflow/dataflow-compiler.mjs
?? .agents/skills/archify/renderers/dataflow/joint-label-routing.mjs
?? .agents/skills/archify/renderers/dataflow/label-capacity.mjs
?? .agents/skills/archify/renderers/dataflow/render-dataflow.mjs
?? .agents/skills/archify/renderers/erd/README.md
?? .agents/skills/archify/renderers/erd/grid.mjs
?? .agents/skills/archify/renderers/erd/render-erd.mjs
?? .agents/skills/archify/renderers/explainer/render-explainer.mjs
?? .agents/skills/archify/renderers/lifecycle/README.md
?? .agents/skills/archify/renderers/lifecycle/render-lifecycle.mjs
?? .agents/skills/archify/renderers/sequence/README.md
?? .agents/skills/archify/renderers/sequence/render-sequence.mjs
?? .agents/skills/archify/renderers/shared/atomic-output.mjs
?? .agents/skills/archify/renderers/shared/automatic-labels.mjs
?? .agents/skills/archify/renderers/shared/brand-marks.mjs
?? .agents/skills/archify/renderers/shared/cli.mjs
?? .agents/skills/archify/renderers/shared/desktop-readability.mjs
?? .agents/skills/archify/renderers/shared/diagnostics.mjs
?? .agents/skills/archify/renderers/shared/engineering-profiles.mjs
?? .agents/skills/archify/renderers/shared/generated-brand-marks.mjs
?? .agents/skills/archify/renderers/shared/generated-validators.mjs
?? .agents/skills/archify/renderers/shared/geometry.mjs
?? .agents/skills/archify/renderers/shared/i18n.mjs
?? .agents/skills/archify/renderers/shared/layout-report.mjs
?? .agents/skills/archify/renderers/shared/legend.mjs
?? .agents/skills/archify/renderers/shared/node-card-style.mjs
?? .agents/skills/archify/renderers/shared/output-path.mjs
?? .agents/skills/archify/renderers/shared/owned-temp-directory.mjs
?? .agents/skills/archify/renderers/shared/path-semantics.mjs
?? .agents/skills/archify/renderers/shared/portable-path.mjs
?? .agents/skills/archify/renderers/shared/repository-evidence.mjs
?? .agents/skills/archify/renderers/shared/repository-location.mjs
?? .agents/skills/archify/renderers/shared/route-quality.mjs
?? .agents/skills/archify/renderers/shared/sidecar-path.mjs
?? .agents/skills/archify/renderers/shared/software-notices.mjs
?? .agents/skills/archify/renderers/shared/spatial-grid.mjs
?? .agents/skills/archify/renderers/shared/svg-path-analysis.mjs
?? .agents/skills/archify/renderers/shared/text-fit.mjs
?? .agents/skills/archify/renderers/shared/utils.mjs
?? .agents/skills/archify/renderers/shared/validator.mjs
?? .agents/skills/archify/renderers/timeline/README.md
?? .agents/skills/archify/renderers/timeline/render-timeline.mjs
?? .agents/skills/archify/renderers/tree/README.md
?? .agents/skills/archify/renderers/tree/render-tree.mjs
?? .agents/skills/archify/renderers/waterfall/README.md
?? .agents/skills/archify/renderers/waterfall/render-waterfall.mjs
?? .agents/skills/archify/renderers/workflow/README.md
?? .agents/skills/archify/renderers/workflow/render-workflow.mjs
?? .agents/skills/archify/renderers/workflow/workflow-compiler.mjs
?? .agents/skills/archify/renderers/workflow/workflow-migration-geometry.mjs
?? .agents/skills/archify/renderers/workflow/workflow-placement-repair.mjs
?? .agents/skills/archify/schemas/README.md
?? .agents/skills/archify/schemas/architecture.schema.json
?? .agents/skills/archify/schemas/class.schema.json
?? .agents/skills/archify/schemas/common.schema.json
?? .agents/skills/archify/schemas/dataflow.schema.json
?? .agents/skills/archify/schemas/erd.schema.json
?? .agents/skills/archify/schemas/explainer.schema.json
?? .agents/skills/archify/schemas/lifecycle.schema.json
?? .agents/skills/archify/schemas/sequence.schema.json
?? .agents/skills/archify/schemas/timeline.schema.json
?? .agents/skills/archify/schemas/tree.schema.json
?? .agents/skills/archify/schemas/waterfall.schema.json
?? .agents/skills/archify/schemas/workflow.schema.json
?? .agents/skills/archify/scripts/check-render-output.mjs
?? .agents/skills/archify/scripts/check-update.mjs
?? .agents/skills/archify/scripts/delivery-update-child.mjs
?? .agents/skills/archify/scripts/render-examples.mjs
?? .agents/skills/archify/scripts/update-contract.mjs
?? .agents/skills/archify/skill-release.json
?? AGENTS.md
?? docs/TR06_COMMUNITY_SQLSERVER_MIGRATION.md
?? docs/academic-mvp/CEYLONGO_ACADEMIC_MVP_MASTER_REQUIREMENTS_V1.1.md
?? docs/academic-mvp/CeylonGo_Academic_MVP_Master_Requirements_V1.1.docx
?? docs/architecture/backend-v2/01-system-architecture/01-complete-backend-architecture.browser-check.json
?? docs/architecture/backend-v2/01-system-architecture/01-complete-backend-architecture.delivery.json
?? docs/architecture/backend-v2/01-system-architecture/01-complete-backend-architecture.finalize-summary.json
?? docs/architecture/backend-v2/01-system-architecture/01-complete-backend-architecture.finalize.json
?? docs/architecture/backend-v2/01-system-architecture/01-complete-backend-architecture.html
?? docs/architecture/backend-v2/01-system-architecture/01-complete-backend-architecture.json
?? docs/architecture/backend-v2/01-system-architecture/02-detailed-backend-components.browser-check.json
?? docs/architecture/backend-v2/01-system-architecture/02-detailed-backend-components.delivery.json
?? docs/architecture/backend-v2/01-system-architecture/02-detailed-backend-components.finalize-summary.json
?? docs/architecture/backend-v2/01-system-architecture/02-detailed-backend-components.finalize.json
?? docs/architecture/backend-v2/01-system-architecture/02-detailed-backend-components.html
?? docs/architecture/backend-v2/01-system-architecture/02-detailed-backend-components.json
?? docs/architecture/backend-v2/01-system-architecture/08-backend-data-flow.delivery.json
?? docs/architecture/backend-v2/01-system-architecture/08-backend-data-flow.html
?? docs/architecture/backend-v2/01-system-architecture/08-backend-data-flow.json
?? docs/architecture/backend-v2/01-system-architecture/17-backend-directory-structure.delivery.json
?? docs/architecture/backend-v2/01-system-architecture/17-backend-directory-structure.html
?? docs/architecture/backend-v2/01-system-architecture/17-backend-directory-structure.json
?? docs/architecture/backend-v2/01-system-architecture/validation/08-backend-data-flow/run-1/08-backend-data-flow.browser-check.json
?? docs/architecture/backend-v2/01-system-architecture/validation/08-backend-data-flow/run-1/08-backend-data-flow.finalize-summary.json
?? docs/architecture/backend-v2/01-system-architecture/validation/08-backend-data-flow/run-1/08-backend-data-flow.finalize.json
?? docs/architecture/backend-v2/01-system-architecture/validation/17-backend-directory-structure/run-1/17-backend-directory-structure.browser-check.json
?? docs/architecture/backend-v2/01-system-architecture/validation/17-backend-directory-structure/run-1/17-backend-directory-structure.finalize-summary.json
?? docs/architecture/backend-v2/01-system-architecture/validation/17-backend-directory-structure/run-1/17-backend-directory-structure.finalize.json
?? docs/architecture/backend-v2/01-system-architecture/visual-review/01-complete-backend-architecture.visual-check.1440x900.dark.png
?? docs/architecture/backend-v2/01-system-architecture/visual-review/01-complete-backend-architecture.visual-check.1440x900.light.png
?? docs/architecture/backend-v2/01-system-architecture/visual-review/01-complete-backend-architecture.visual-check.2048x1320.dark.png
?? docs/architecture/backend-v2/01-system-architecture/visual-review/01-complete-backend-architecture.visual-check.2048x1320.light.png
?? docs/architecture/backend-v2/01-system-architecture/visual-review/01-complete-backend-architecture.visual-check.html
?? docs/architecture/backend-v2/01-system-architecture/visual-review/01-complete-backend-architecture.visual-check.json
?? docs/architecture/backend-v2/02-authentication/03-authentication-authorization.browser-check.json
?? docs/architecture/backend-v2/02-authentication/03-authentication-authorization.delivery.json
?? docs/architecture/backend-v2/02-authentication/03-authentication-authorization.finalize-summary.json
?? docs/architecture/backend-v2/02-authentication/03-authentication-authorization.finalize.json
?? docs/architecture/backend-v2/02-authentication/03-authentication-authorization.html
?? docs/architecture/backend-v2/02-authentication/03-authentication-authorization.json
?? docs/architecture/backend-v2/02-authentication/09-traveler-authentication.browser-check.json
?? docs/architecture/backend-v2/02-authentication/09-traveler-authentication.delivery.json
?? docs/architecture/backend-v2/02-authentication/09-traveler-authentication.finalize-summary.json
?? docs/architecture/backend-v2/02-authentication/09-traveler-authentication.finalize.json
?? docs/architecture/backend-v2/02-authentication/09-traveler-authentication.html
?? docs/architecture/backend-v2/02-authentication/09-traveler-authentication.json
?? docs/architecture/backend-v2/02-authentication/10-provider-authentication.delivery.json
?? docs/architecture/backend-v2/02-authentication/10-provider-authentication.html
?? docs/architecture/backend-v2/02-authentication/10-provider-authentication.json
?? docs/architecture/backend-v2/02-authentication/validation/10-provider-authentication/run-1/10-provider-authentication.browser-check.json
?? docs/architecture/backend-v2/02-authentication/validation/10-provider-authentication/run-1/10-provider-authentication.finalize-summary.json
?? docs/architecture/backend-v2/02-authentication/validation/10-provider-authentication/run-1/10-provider-authentication.finalize.json
?? docs/architecture/backend-v2/03-business-modules/04-service-provider-management.delivery.json
?? docs/architecture/backend-v2/03-business-modules/04-service-provider-management.html
?? docs/architecture/backend-v2/03-business-modules/04-service-provider-management.json
?? docs/architecture/backend-v2/03-business-modules/11-homestay-module.browser-check.json
?? docs/architecture/backend-v2/03-business-modules/11-homestay-module.delivery.json
?? docs/architecture/backend-v2/03-business-modules/11-homestay-module.finalize-summary.json
?? docs/architecture/backend-v2/03-business-modules/11-homestay-module.finalize.json
?? docs/architecture/backend-v2/03-business-modules/11-homestay-module.html
?? docs/architecture/backend-v2/03-business-modules/11-homestay-module.json
?? docs/architecture/backend-v2/03-business-modules/12-artisan-small-business.delivery.json
?? docs/architecture/backend-v2/03-business-modules/12-artisan-small-business.html
?? docs/architecture/backend-v2/03-business-modules/12-artisan-small-business.json
?? docs/architecture/backend-v2/03-business-modules/13-bike-bicycle-rental.delivery.json
?? docs/architecture/backend-v2/03-business-modules/13-bike-bicycle-rental.html
?? docs/architecture/backend-v2/03-business-modules/13-bike-bicycle-rental.json
?? docs/architecture/backend-v2/03-business-modules/validation/04-service-provider-management/run-1/04-service-provider-management.browser-check.json
?? docs/architecture/backend-v2/03-business-modules/validation/04-service-provider-management/run-1/04-service-provider-management.finalize-summary.json
?? docs/architecture/backend-v2/03-business-modules/validation/04-service-provider-management/run-1/04-service-provider-management.finalize.json
?? docs/architecture/backend-v2/03-business-modules/validation/12-artisan-small-business/run-1/12-artisan-small-business.browser-check.json
?? docs/architecture/backend-v2/03-business-modules/validation/12-artisan-small-business/run-1/12-artisan-small-business.finalize-summary.json
?? docs/architecture/backend-v2/03-business-modules/validation/12-artisan-small-business/run-1/12-artisan-small-business.finalize.json
?? docs/architecture/backend-v2/03-business-modules/validation/13-bike-bicycle-rental/run-3/13-bike-bicycle-rental.browser-check.json
?? docs/architecture/backend-v2/03-business-modules/validation/13-bike-bicycle-rental/run-3/13-bike-bicycle-rental.finalize-summary.json
?? docs/architecture/backend-v2/03-business-modules/validation/13-bike-bicycle-rental/run-3/13-bike-bicycle-rental.finalize.json
?? docs/architecture/backend-v2/03-business-modules/validation/13-bike-bicycle-rental/visual-final/13-bike-bicycle-rental.visual-check.1440x900.dark.png
?? docs/architecture/backend-v2/03-business-modules/validation/13-bike-bicycle-rental/visual-final/13-bike-bicycle-rental.visual-check.1440x900.light.png
?? docs/architecture/backend-v2/03-business-modules/validation/13-bike-bicycle-rental/visual-final/13-bike-bicycle-rental.visual-check.2048x1320.dark.png
?? docs/architecture/backend-v2/03-business-modules/validation/13-bike-bicycle-rental/visual-final/13-bike-bicycle-rental.visual-check.2048x1320.light.png
?? docs/architecture/backend-v2/03-business-modules/validation/13-bike-bicycle-rental/visual-final/13-bike-bicycle-rental.visual-check.html
?? docs/architecture/backend-v2/03-business-modules/validation/13-bike-bicycle-rental/visual-final/13-bike-bicycle-rental.visual-check.json
?? docs/architecture/backend-v2/04-booking-and-payments/05-booking-payment-architecture.delivery.json
?? docs/architecture/backend-v2/04-booking-and-payments/05-booking-payment-architecture.html
?? docs/architecture/backend-v2/04-booking-and-payments/05-booking-payment-architecture.json
?? docs/architecture/backend-v2/04-booking-and-payments/14-payment-lifecycle.browser-check.json
?? docs/architecture/backend-v2/04-booking-and-payments/14-payment-lifecycle.delivery.json
?? docs/architecture/backend-v2/04-booking-and-payments/14-payment-lifecycle.finalize-summary.json
?? docs/architecture/backend-v2/04-booking-and-payments/14-payment-lifecycle.finalize.json
?? docs/architecture/backend-v2/04-booking-and-payments/14-payment-lifecycle.html
?? docs/architecture/backend-v2/04-booking-and-payments/14-payment-lifecycle.json
?? docs/architecture/backend-v2/04-booking-and-payments/18-booking-lifecycle.delivery.json
?? docs/architecture/backend-v2/04-booking-and-payments/18-booking-lifecycle.html
?? docs/architecture/backend-v2/04-booking-and-payments/18-booking-lifecycle.json
?? docs/architecture/backend-v2/04-booking-and-payments/validation/05-booking-payment-architecture/run-2/05-booking-payment-architecture.browser-check.json
?? docs/architecture/backend-v2/04-booking-and-payments/validation/05-booking-payment-architecture/run-2/05-booking-payment-architecture.finalize-summary.json
?? docs/architecture/backend-v2/04-booking-and-payments/validation/05-booking-payment-architecture/run-2/05-booking-payment-architecture.finalize.json
?? docs/architecture/backend-v2/04-booking-and-payments/validation/18-booking-lifecycle/run-1/18-booking-lifecycle.browser-check.json
?? docs/architecture/backend-v2/04-booking-and-payments/validation/18-booking-lifecycle/run-1/18-booking-lifecycle.finalize-summary.json
?? docs/architecture/backend-v2/04-booking-and-payments/validation/18-booking-lifecycle/run-1/18-booking-lifecycle.finalize.json
?? docs/architecture/backend-v2/05-database/06-complete-database-erd.browser-check.json
?? docs/architecture/backend-v2/05-database/06-complete-database-erd.delivery.json
?? docs/architecture/backend-v2/05-database/06-complete-database-erd.finalize-summary.json
?? docs/architecture/backend-v2/05-database/06-complete-database-erd.finalize.json
?? docs/architecture/backend-v2/05-database/06-complete-database-erd.html
?? docs/architecture/backend-v2/05-database/06-complete-database-erd.json
?? docs/architecture/backend-v2/05-database/domain-activities.browser-check.json
?? docs/architecture/backend-v2/05-database/domain-activities.delivery.json
?? docs/architecture/backend-v2/05-database/domain-activities.finalize-summary.json
?? docs/architecture/backend-v2/05-database/domain-activities.finalize.json
?? docs/architecture/backend-v2/05-database/domain-activities.html
?? docs/architecture/backend-v2/05-database/domain-activities.json
?? docs/architecture/backend-v2/05-database/domain-ai-schema.browser-check.json
?? docs/architecture/backend-v2/05-database/domain-ai-schema.delivery.json
?? docs/architecture/backend-v2/05-database/domain-ai-schema.finalize-summary.json
?? docs/architecture/backend-v2/05-database/domain-ai-schema.finalize.json
?? docs/architecture/backend-v2/05-database/domain-ai-schema.html
?? docs/architecture/backend-v2/05-database/domain-ai-schema.json
?? docs/architecture/backend-v2/05-database/domain-artisan.browser-check.json
?? docs/architecture/backend-v2/05-database/domain-artisan.delivery.json
?? docs/architecture/backend-v2/05-database/domain-artisan.finalize-summary.json
?? docs/architecture/backend-v2/05-database/domain-artisan.finalize.json
?? docs/architecture/backend-v2/05-database/domain-artisan.html
?? docs/architecture/backend-v2/05-database/domain-artisan.json
?? docs/architecture/backend-v2/05-database/domain-commerce.browser-check.json
?? docs/architecture/backend-v2/05-database/domain-commerce.delivery.json
?? docs/architecture/backend-v2/05-database/domain-commerce.finalize-summary.json
?? docs/architecture/backend-v2/05-database/domain-commerce.finalize.json
?? docs/architecture/backend-v2/05-database/domain-commerce.html
?? docs/architecture/backend-v2/05-database/domain-commerce.json
?? docs/architecture/backend-v2/05-database/domain-community.browser-check.json
?? docs/architecture/backend-v2/05-database/domain-community.delivery.json
?? docs/architecture/backend-v2/05-database/domain-community.finalize-summary.json
?? docs/architecture/backend-v2/05-database/domain-community.finalize.json
?? docs/architecture/backend-v2/05-database/domain-community.html
?? docs/architecture/backend-v2/05-database/domain-community.json
?? docs/architecture/backend-v2/05-database/domain-eco-tourism.browser-check.json
?? docs/architecture/backend-v2/05-database/domain-eco-tourism.delivery.json
?? docs/architecture/backend-v2/05-database/domain-eco-tourism.finalize-summary.json
?? docs/architecture/backend-v2/05-database/domain-eco-tourism.finalize.json
?? docs/architecture/backend-v2/05-database/domain-eco-tourism.html
?? docs/architecture/backend-v2/05-database/domain-eco-tourism.json
?? docs/architecture/backend-v2/05-database/domain-homestay.browser-check.json
?? docs/architecture/backend-v2/05-database/domain-homestay.delivery.json
?? docs/architecture/backend-v2/05-database/domain-homestay.finalize-summary.json
?? docs/architecture/backend-v2/05-database/domain-homestay.finalize.json
?? docs/architecture/backend-v2/05-database/domain-homestay.html
?? docs/architecture/backend-v2/05-database/domain-homestay.json
?? docs/architecture/backend-v2/05-database/domain-identity.browser-check.json
?? docs/architecture/backend-v2/05-database/domain-identity.delivery.json
?? docs/architecture/backend-v2/05-database/domain-identity.finalize-summary.json
?? docs/architecture/backend-v2/05-database/domain-identity.finalize.json
?? docs/architecture/backend-v2/05-database/domain-identity.html
?? docs/architecture/backend-v2/05-database/domain-identity.json
?? docs/architecture/backend-v2/05-database/domain-rental.browser-check.json
?? docs/architecture/backend-v2/05-database/domain-rental.delivery.json
?? docs/architecture/backend-v2/05-database/domain-rental.finalize-summary.json
?? docs/architecture/backend-v2/05-database/domain-rental.finalize.json
?? docs/architecture/backend-v2/05-database/domain-rental.html
?? docs/architecture/backend-v2/05-database/domain-rental.json
?? docs/architecture/backend-v2/05-database/domain-shared.browser-check.json
?? docs/architecture/backend-v2/05-database/domain-shared.delivery.json
?? docs/architecture/backend-v2/05-database/domain-shared.finalize-summary.json
?? docs/architecture/backend-v2/05-database/domain-shared.finalize.json
?? docs/architecture/backend-v2/05-database/domain-shared.html
?? docs/architecture/backend-v2/05-database/domain-shared.json
?? docs/architecture/backend-v2/05-database/domain-tour-guides.browser-check.json
?? docs/architecture/backend-v2/05-database/domain-tour-guides.delivery.json
?? docs/architecture/backend-v2/05-database/domain-tour-guides.finalize-summary.json
?? docs/architecture/backend-v2/05-database/domain-tour-guides.finalize.json
?? docs/architecture/backend-v2/05-database/domain-tour-guides.html
?? docs/architecture/backend-v2/05-database/domain-tour-guides.json
?? docs/architecture/backend-v2/05-database/visual-review/06-complete-database-erd.visual-check.1440x900.dark.png
?? docs/architecture/backend-v2/05-database/visual-review/06-complete-database-erd.visual-check.1440x900.light.png
?? docs/architecture/backend-v2/05-database/visual-review/06-complete-database-erd.visual-check.2048x1320.dark.png
?? docs/architecture/backend-v2/05-database/visual-review/06-complete-database-erd.visual-check.2048x1320.light.png
?? docs/architecture/backend-v2/05-database/visual-review/06-complete-database-erd.visual-check.html
?? docs/architecture/backend-v2/05-database/visual-review/06-complete-database-erd.visual-check.json
?? docs/architecture/backend-v2/06-external-integrations/07-external-api-integration.delivery.json
?? docs/architecture/backend-v2/06-external-integrations/07-external-api-integration.html
?? docs/architecture/backend-v2/06-external-integrations/07-external-api-integration.json
?? docs/architecture/backend-v2/06-external-integrations/15-live-weather.browser-check.json
?? docs/architecture/backend-v2/06-external-integrations/15-live-weather.delivery.json
?? docs/architecture/backend-v2/06-external-integrations/15-live-weather.finalize-summary.json
?? docs/architecture/backend-v2/06-external-integrations/15-live-weather.finalize.json
?? docs/architecture/backend-v2/06-external-integrations/15-live-weather.html
?? docs/architecture/backend-v2/06-external-integrations/15-live-weather.json
?? docs/architecture/backend-v2/06-external-integrations/16-interactive-map.browser-check.json
?? docs/architecture/backend-v2/06-external-integrations/16-interactive-map.delivery.json
?? docs/architecture/backend-v2/06-external-integrations/16-interactive-map.finalize-summary.json
?? docs/architecture/backend-v2/06-external-integrations/16-interactive-map.finalize.json
?? docs/architecture/backend-v2/06-external-integrations/16-interactive-map.html
?? docs/architecture/backend-v2/06-external-integrations/16-interactive-map.json
?? docs/architecture/backend-v2/06-external-integrations/validation/07-external-api-integration/run-1/07-external-api-integration.browser-check.json
?? docs/architecture/backend-v2/06-external-integrations/validation/07-external-api-integration/run-1/07-external-api-integration.finalize-summary.json
?? docs/architecture/backend-v2/06-external-integrations/validation/07-external-api-integration/run-1/07-external-api-integration.finalize.json
?? docs/architecture/backend-v2/19-supervisor-architecture-overview-final/19-supervisor-architecture-overview.browser-check.json
?? docs/architecture/backend-v2/19-supervisor-architecture-overview-final/19-supervisor-architecture-overview.finalize-summary.json
?? docs/architecture/backend-v2/19-supervisor-architecture-overview-final/19-supervisor-architecture-overview.finalize.json
?? docs/architecture/backend-v2/19-supervisor-architecture-overview-final/19-supervisor-architecture-overview.visual-check.1440x900.dark.png
?? docs/architecture/backend-v2/19-supervisor-architecture-overview-final/19-supervisor-architecture-overview.visual-check.1440x900.light.png
?? docs/architecture/backend-v2/19-supervisor-architecture-overview-final/19-supervisor-architecture-overview.visual-check.2048x1320.dark.png
?? docs/architecture/backend-v2/19-supervisor-architecture-overview-final/19-supervisor-architecture-overview.visual-check.2048x1320.light.png
?? docs/architecture/backend-v2/19-supervisor-architecture-overview-final/19-supervisor-architecture-overview.visual-check.html
?? docs/architecture/backend-v2/19-supervisor-architecture-overview-final/19-supervisor-architecture-overview.visual-check.json
?? docs/architecture/backend-v2/19-supervisor-architecture-overview.delivery.json
?? docs/architecture/backend-v2/19-supervisor-architecture-overview.html
?? docs/architecture/backend-v2/19-supervisor-architecture-overview.json
?? docs/architecture/backend-v2/20-local-tour-guide-module.delivery.json
?? docs/architecture/backend-v2/20-local-tour-guide-module.html
?? docs/architecture/backend-v2/20-local-tour-guide-module.json
?? docs/architecture/backend-v2/API_ENDPOINT_INVENTORY.md
?? docs/architecture/backend-v2/ARCHITECTURE_REPORT.md
?? docs/architecture/backend-v2/DATABASE_INVENTORY.md
?? docs/architecture/backend-v2/INDEX.html
?? docs/architecture/backend-v2/MODULE_STATUS.md
?? docs/architecture/backend-v2/README.md
?? docs/architecture/backend-v2/validation-v21/01-complete-backend-architecture/01-complete-backend-architecture.browser-check.json
?? docs/architecture/backend-v2/validation-v21/01-complete-backend-architecture/01-complete-backend-architecture.finalize-summary.json
?? docs/architecture/backend-v2/validation-v21/01-complete-backend-architecture/01-complete-backend-architecture.finalize.json
?? docs/architecture/backend-v2/validation-v21/02-detailed-backend-components/02-detailed-backend-components.browser-check.json
?? docs/architecture/backend-v2/validation-v21/02-detailed-backend-components/02-detailed-backend-components.finalize-summary.json
?? docs/architecture/backend-v2/validation-v21/02-detailed-backend-components/02-detailed-backend-components.finalize.json
?? docs/architecture/backend-v2/validation-v21/04-service-provider-management/04-service-provider-management.browser-check.json
?? docs/architecture/backend-v2/validation-v21/04-service-provider-management/04-service-provider-management.finalize-summary.json
?? docs/architecture/backend-v2/validation-v21/04-service-provider-management/04-service-provider-management.finalize.json
?? docs/architecture/backend-v2/validation-v21/05-booking-payment-architecture/05-booking-payment-architecture.browser-check.json
?? docs/architecture/backend-v2/validation-v21/05-booking-payment-architecture/05-booking-payment-architecture.finalize-summary.json
?? docs/architecture/backend-v2/validation-v21/05-booking-payment-architecture/05-booking-payment-architecture.finalize.json
?? docs/architecture/backend-v2/validation-v21/06-complete-database-erd/06-complete-database-erd.browser-check.json
?? docs/architecture/backend-v2/validation-v21/06-complete-database-erd/06-complete-database-erd.finalize-summary.json
?? docs/architecture/backend-v2/validation-v21/06-complete-database-erd/06-complete-database-erd.finalize.json
?? docs/architecture/backend-v2/validation-v21/07-external-api-integration/07-external-api-integration.browser-check.json
?? docs/architecture/backend-v2/validation-v21/07-external-api-integration/07-external-api-integration.finalize-summary.json
?? docs/architecture/backend-v2/validation-v21/07-external-api-integration/07-external-api-integration.finalize.json
?? docs/architecture/backend-v2/validation-v21/08-backend-data-flow/08-backend-data-flow.browser-check.json
?? docs/architecture/backend-v2/validation-v21/08-backend-data-flow/08-backend-data-flow.finalize-summary.json
?? docs/architecture/backend-v2/validation-v21/08-backend-data-flow/08-backend-data-flow.finalize.json
?? docs/architecture/backend-v2/validation-v21/13-bike-bicycle-rental/13-bike-bicycle-rental.browser-check.json
?? docs/architecture/backend-v2/validation-v21/13-bike-bicycle-rental/13-bike-bicycle-rental.finalize-summary.json
?? docs/architecture/backend-v2/validation-v21/13-bike-bicycle-rental/13-bike-bicycle-rental.finalize.json
?? docs/architecture/backend-v2/validation-v21/14-payment-lifecycle/14-payment-lifecycle.browser-check.json
?? docs/architecture/backend-v2/validation-v21/14-payment-lifecycle/14-payment-lifecycle.finalize-summary.json
?? docs/architecture/backend-v2/validation-v21/14-payment-lifecycle/14-payment-lifecycle.finalize.json
?? docs/architecture/backend-v2/validation-v21/17-backend-directory-structure/17-backend-directory-structure.browser-check.json
?? docs/architecture/backend-v2/validation-v21/17-backend-directory-structure/17-backend-directory-structure.finalize-summary.json
?? docs/architecture/backend-v2/validation-v21/17-backend-directory-structure/17-backend-directory-structure.finalize.json
?? docs/architecture/backend-v2/validation-v21/18-booking-lifecycle/18-booking-lifecycle.browser-check.json
?? docs/architecture/backend-v2/validation-v21/18-booking-lifecycle/18-booking-lifecycle.finalize-summary.json
?? docs/architecture/backend-v2/validation-v21/18-booking-lifecycle/18-booking-lifecycle.finalize.json
?? docs/architecture/backend-v2/validation-v21/19-supervisor-architecture-overview/19-supervisor-architecture-overview.browser-check.json
?? docs/architecture/backend-v2/validation-v21/19-supervisor-architecture-overview/19-supervisor-architecture-overview.finalize-summary.json
?? docs/architecture/backend-v2/validation-v21/19-supervisor-architecture-overview/19-supervisor-architecture-overview.finalize.json
?? docs/architecture/backend-v2/validation-v21/19-supervisor-architecture-overview/19-supervisor-architecture-overview.visual-check.1440x900.dark.png
?? docs/architecture/backend-v2/validation-v21/19-supervisor-architecture-overview/19-supervisor-architecture-overview.visual-check.1440x900.light.png
?? docs/architecture/backend-v2/validation-v21/19-supervisor-architecture-overview/19-supervisor-architecture-overview.visual-check.2048x1320.dark.png
?? docs/architecture/backend-v2/validation-v21/19-supervisor-architecture-overview/19-supervisor-architecture-overview.visual-check.2048x1320.light.png
?? docs/architecture/backend-v2/validation-v21/19-supervisor-architecture-overview/19-supervisor-architecture-overview.visual-check.html
?? docs/architecture/backend-v2/validation-v21/19-supervisor-architecture-overview/19-supervisor-architecture-overview.visual-check.json
?? docs/architecture/backend-v2/validation-v21/20-local-tour-guide-module-2/20-local-tour-guide-module.browser-check.json
?? docs/architecture/backend-v2/validation-v21/20-local-tour-guide-module-2/20-local-tour-guide-module.finalize-summary.json
?? docs/architecture/backend-v2/validation-v21/20-local-tour-guide-module-2/20-local-tour-guide-module.finalize.json
?? docs/architecture/backend-v2/validation-v21/20-local-tour-guide-module-2/20-local-tour-guide-module.visual-check.1440x900.dark.png
?? docs/architecture/backend-v2/validation-v21/20-local-tour-guide-module-2/20-local-tour-guide-module.visual-check.1440x900.light.png
?? docs/architecture/backend-v2/validation-v21/20-local-tour-guide-module-2/20-local-tour-guide-module.visual-check.2048x1320.dark.png
?? docs/architecture/backend-v2/validation-v21/20-local-tour-guide-module-2/20-local-tour-guide-module.visual-check.2048x1320.light.png
?? docs/architecture/backend-v2/validation-v21/20-local-tour-guide-module-2/20-local-tour-guide-module.visual-check.html
?? docs/architecture/backend-v2/validation-v21/20-local-tour-guide-module-2/20-local-tour-guide-module.visual-check.json
?? docs/architecture/backend-v2/validation-v21/20-local-tour-guide-module/20-local-tour-guide-module.browser-check.json
?? docs/architecture/backend-v2/validation-v21/20-local-tour-guide-module/20-local-tour-guide-module.finalize-summary.json
?? docs/architecture/backend-v2/validation-v21/20-local-tour-guide-module/20-local-tour-guide-module.finalize.json
?? docs/architecture/backend-v2/validation-v21/20-local-tour-guide-module/20-local-tour-guide-module.visual-check.1440x900.dark.png
?? docs/architecture/backend-v2/validation-v21/20-local-tour-guide-module/20-local-tour-guide-module.visual-check.1440x900.light.png
?? docs/architecture/backend-v2/validation-v21/20-local-tour-guide-module/20-local-tour-guide-module.visual-check.2048x1320.dark.png
?? docs/architecture/backend-v2/validation-v21/20-local-tour-guide-module/20-local-tour-guide-module.visual-check.2048x1320.light.png
?? docs/architecture/backend-v2/validation-v21/20-local-tour-guide-module/20-local-tour-guide-module.visual-check.html
?? docs/architecture/backend-v2/validation-v21/20-local-tour-guide-module/20-local-tour-guide-module.visual-check.json
?? docs/architecture/backend-v2/validation-v21/domain-commerce/domain-commerce.browser-check.json
?? docs/architecture/backend-v2/validation-v21/domain-commerce/domain-commerce.finalize-summary.json
?? docs/architecture/backend-v2/validation-v21/domain-commerce/domain-commerce.finalize.json
?? docs/architecture/backend-v2/validation-v21/domain-eco-tourism/domain-eco-tourism.browser-check.json
?? docs/architecture/backend-v2/validation-v21/domain-eco-tourism/domain-eco-tourism.finalize-summary.json
?? docs/architecture/backend-v2/validation-v21/domain-eco-tourism/domain-eco-tourism.finalize.json
?? docs/architecture/backend-v2/validation-v21/domain-rental/domain-rental.browser-check.json
?? docs/architecture/backend-v2/validation-v21/domain-rental/domain-rental.finalize-summary.json
?? docs/architecture/backend-v2/validation-v21/domain-rental/domain-rental.finalize.json
?? docs/architecture/backend-v2/validation-v21/domain-tour-guides/domain-tour-guides.browser-check.json
?? docs/architecture/backend-v2/validation-v21/domain-tour-guides/domain-tour-guides.finalize-summary.json
?? docs/architecture/backend-v2/validation-v21/domain-tour-guides/domain-tour-guides.finalize.json
?? docs/database/CeylonGo_Master_Database_Inventory.txt
?? docs/interactive-map/CeylonGo_Interactive_Map_Module.pdf
?? docs/live-weather/CeylonGo_Live_Weather_Module.pdf
?? docs/live-weather/CeylonGo_Master_Database_Documentation_V1.0.pdf
?? docs/live-weather/CeylonGo_Team_Development_Guidelines.pdf
?? docs/provider-management/CeylonGo_Service_Provider_Management_Specification.pdf
?? docs/reports/ARTISAN_INTEGRATION_POST_RECONCILIATION_2026-11-20.json
?? docs/reports/ARTISAN_INTEGRATION_PRE_RECONCILIATION_2026-11-20.json
?? docs/reports/BACKEND_DEVELOPMENT_PROGRESS_2026-11-20.md
?? docs/reports/CEYLONGO_BACKEND_REQUIREMENTS_TRACEABILITY_2026-11-22.csv
?? docs/reports/CEYLONGO_COMPLETE_BACKEND_AUDIT_2026-11-22.md
?? docs/reports/ECO_PHASE04B_SELECT_ONLY_PREFLIGHT_FINAL.md
?? docs/reports/ECO_PHASE04_DATABASE_READINESS_AUDIT.md
?? docs/reports/ECO_PHASE04_PAYMENT_ACCEPTANCE_CHECKLIST.md
?? docs/reports/ECO_PHASE04_READ_ONLY_PRINCIPAL_SETUP.md
?? docs/reports/ECO_PHASE04_SQLSERVER_ACCEPTANCE_PLAN.md
?? docs/reports/ECO_TOURISM_COMPLETE_BACKEND_AUDIT_2026-11-22.md
?? docs/reports/ECO_TOURISM_FINAL_COMPLETION_PROGRESS_2026-11-22.md
?? docs/reports/ECO_TOURISM_FINAL_OFFLINE_MILESTONE_REPORT_2026-11-22.md
?? docs/reports/ECO_TOURISM_FINAL_SQLSERVER_CLEANUP_AUDIT_2026-11-22.json
?? docs/reports/ECO_TOURISM_PHASE03_MIGRATION_READINESS_2026-11-22.md
?? docs/reports/ECO_TOURISM_PHASE05_OFFLINE_COMPLETION_2026-11-22.md
?? docs/reports/ECO_TOURISM_PHASE06_REQUIREMENT_CLOSURE_2026-11-22.md
?? docs/reports/ECO_TOURISM_T09_MOCK_PAYMENT_TEST_REPORT_2026-11-22.md
?? scripts/community_migrate.py
?? scripts/eco_phase04b_select_only_preflight.ps1
?? tests/test_community_migrate.py
?? tests/test_preflight_permission_guard.py
```

</details>
