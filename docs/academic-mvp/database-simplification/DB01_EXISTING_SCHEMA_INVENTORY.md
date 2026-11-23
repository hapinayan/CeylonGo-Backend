# DB-01 existing schema inventory

Baseline 3bf9822f6be99780d0e25c8752ed263ac407c3c6 on feature/academic-mvp-backend-simplification. Approved requirements blob 31859af08c1bd0f88084205178b7cb0fdbe88582. Scope: [V1.1](../CEYLONGO_ACADEMIC_MVP_MASTER_REQUIREMENTS_V1.1.md).

Configured metadata contains 99 mapped classes and 99 tables. There are 43 numbered forward migrations. Physical catalog counts, applied migrations, enforced constraints, and data volumes are unverified. The ORM inventory records declared behavior. SQL marker excerpts include conditional SQL and can miss constructed identifiers. ORM cascades and SQL FK actions are separate.

| Table / Model | PK | FK and declared actions | Unique constraints | Indexes | Checks | ORM relationships and cascade |
|---|---|---|---|---|---|---|
| activities / Activity ([app/models/activity.py:22](../../../app/models/activity.py#L22)) | id | provider_profile_id -> provider_profiles.id (delete unspecified; update unspecified), reviewed_by -> users.id (delete unspecified; update unspecified) | None declared | ix_activities_category (activities.category), ix_activities_provider_profile_id (activities.provider_profile_id) | CK_activities_age: minimum_age BETWEEN 0 AND 120, CK_activities_category: category IN ('cultural', 'adventure', 'local', 'community'), CK_activities_duration: duration_minutes > 0, CK_activities_latitude: latitude IS NULL OR latitude BETWEEN -90 AND 90, CK_activities_longitude: longitude IS NULL OR longitude BETWEEN -180 AND 180, CK_activities_price: price >= 0, CK_activities_pricing: pricing_type IN ('per_person', 'per_group'), CK_activities_status: approval_status IN ('draft', 'pending', 'approved', 'rejected', 'suspended') | images -> activity_images (merge,save-update; passive_deletes=False), provider_profile -> provider_profiles (merge,save-update; passive_deletes=False), reviewer -> users (merge,save-update; passive_deletes=False), slots -> activity_slots (merge,save-update; passive_deletes=False) |
| activity_booking_participants / ActivityBookingParticipant ([app/models/activity_booking.py:144](../../../app/models/activity_booking.py#L144)) | id | activity_booking_id -> activity_bookings.id (delete CASCADE; update unspecified) | None declared | ix_activity_booking_participants_activity_booking_id (activity_booking_participants.activity_booking_id) | CK_activity_participants_age: age IS NULL OR age > 0 | booking -> activity_bookings (merge,save-update; passive_deletes=False) |
| activity_bookings / ActivityBooking ([app/models/activity_booking.py:20](../../../app/models/activity_booking.py#L20)) | id | activity_slot_id -> activity_slots.id (delete unspecified; update unspecified), eco_experience_id -> eco_experiences.id (delete unspecified; update unspecified), user_id -> users.id (delete unspecified; update unspecified) | None declared | ix_activity_bookings_activity_slot_id (activity_bookings.activity_slot_id), ix_activity_bookings_eco_experience_id (activity_bookings.eco_experience_id), ix_activity_bookings_user_id (activity_bookings.user_id) | CK_activity_bookings_method: payment_method IN ('online', 'pay_at_location'), CK_activity_bookings_participants: participants > 0, CK_activity_bookings_payment: payment_status IN ('unpaid', 'pending', 'paid', 'refund_pending', 'refunded', 'failed'), CK_activity_bookings_price: total_price >= 0, CK_activity_bookings_status: booking_status IN ('pending', 'confirmed', 'rejected', 'cancelled', 'completed') | activity_slot -> activity_slots (merge,save-update; passive_deletes=False), participants_list -> activity_booking_participants (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=False), traveler -> users (merge,save-update; passive_deletes=False) |
| activity_images / ActivityImage ([app/models/activity_image.py:18](../../../app/models/activity_image.py#L18)) | id | activity_id -> activities.id (delete unspecified; update unspecified) | None declared | ix_activity_images_activity_id (activity_images.activity_id) | CK_activity_images_order: display_order >= 0 | activity -> activities (merge,save-update; passive_deletes=False) |
| activity_slots / ActivitySlot ([app/models/activity_slot.py:18](../../../app/models/activity_slot.py#L18)) | id | activity_id -> activities.id (delete unspecified; update unspecified), eco_experience_id -> eco_experiences.id (delete unspecified; update unspecified) | None declared | ix_activity_slots_activity_id (activity_slots.activity_id), ix_activity_slots_eco_experience_id (activity_slots.eco_experience_id) | CK_activity_slots_capacity: capacity > 0, CK_activity_slots_reserved: reserved_count >= 0 AND reserved_count <= capacity, CK_activity_slots_time: ends_at > starts_at | activity -> activities (merge,save-update; passive_deletes=False), bookings -> activity_bookings (merge,save-update; passive_deletes=False) |
| admin_audit_logs / AdminAuditLog ([app/models/shared.py:377](../../../app/models/shared.py#L377)) | id | admin_user_id -> users.id (delete unspecified; update unspecified) | None declared | ix_admin_audit_logs_admin_user_id (admin_audit_logs.admin_user_id) | None declared | None declared |
| advertising_payments / AdvertisingPayment ([app/models/shared.py:295](../../../app/models/shared.py#L295)) | id | campaign_id -> promotion_campaigns.id (delete unspecified; update unspecified), provider_profile_id -> provider_profiles.id (delete unspecified; update unspecified) | unnamed (order_reference) | ix_advertising_payments_campaign_id (advertising_payments.campaign_id), ix_advertising_payments_payment_status (advertising_payments.payment_status), ix_advertising_payments_provider_profile_id (advertising_payments.provider_profile_id) | None declared | campaign -> promotion_campaigns (merge,save-update; passive_deletes=False), provider_profile -> provider_profiles (merge,save-update; passive_deletes=False) |
| ai_action_audit / AIActionAudit ([app/models/ai_planner.py:222](../../../app/models/ai_planner.py#L222)) | id | pending_action_id -> ai_pending_actions.id (delete CASCADE; update unspecified), user_id -> users.id (delete unspecified; update unspecified) | None declared | ix_ai_action_audit_pending_action_id (ai_action_audit.pending_action_id) | CK_ai_action_audit_outcome: outcome IN ('success', 'failed', 'cancelled') | pending_action -> ai_pending_actions (merge,save-update; passive_deletes=False), user -> users (merge,save-update; passive_deletes=False) |
| ai_chat_messages / AIChatMessage ([app/models/ai_planner.py:40](../../../app/models/ai_planner.py#L40)) | id | session_id -> ai_chat_sessions.id (delete CASCADE; update unspecified) | None declared | ix_ai_chat_messages_session_id (ai_chat_messages.session_id) | CK_ai_chat_messages_role: sender_role IN ('user', 'assistant', 'system') | session -> ai_chat_sessions (merge,save-update; passive_deletes=False) |
| ai_chat_sessions / AIChatSession ([app/models/ai_planner.py:23](../../../app/models/ai_planner.py#L23)) | id | user_id -> users.id (delete CASCADE; update unspecified) | None declared | ix_ai_chat_sessions_user_id (ai_chat_sessions.user_id) | None declared | messages -> ai_chat_messages (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=False), user -> users (merge,save-update; passive_deletes=False) |
| ai_pending_actions / AIPendingAction ([app/models/ai_planner.py:192](../../../app/models/ai_planner.py#L192)) | id | session_id -> ai_chat_sessions.id (delete CASCADE; update unspecified), user_id -> users.id (delete unspecified; update unspecified) | None declared | ix_ai_pending_actions_session_id (ai_pending_actions.session_id), ix_ai_pending_actions_user_id (ai_pending_actions.user_id) | CK_ai_pending_actions_status: status IN ('pending', 'approved', 'rejected', 'executed', 'expired') | audit_logs -> ai_action_audit (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=False), session -> ai_chat_sessions (merge,save-update; passive_deletes=False), user -> users (merge,save-update; passive_deletes=False) |
| artisan_business_hours / ArtisanBusinessHours ([app/models/artisan.py:209](../../../app/models/artisan.py#L209)) | id | artisan_listing_id -> artisan_listings.id (delete CASCADE; update unspecified) | UQ_artisan_business_hours_day (artisan_listing_id, day_of_week) | ix_artisan_business_hours_artisan_listing_id (artisan_business_hours.artisan_listing_id) | None declared | artisan_listing -> artisan_listings (merge,save-update; passive_deletes=False) |
| artisan_business_images / ArtisanBusinessImage ([app/models/artisan.py:23](../../../app/models/artisan.py#L23)) | id | artisan_listing_id -> artisan_listings.id (delete CASCADE; update unspecified) | None declared | ix_artisan_business_images_artisan_listing_id (artisan_business_images.artisan_listing_id) | None declared | artisan_listing -> artisan_listings (merge,save-update; passive_deletes=False) |
| artisan_listings / ArtisanListing ([app/models/artisan_listing.py:11](../../../app/models/artisan_listing.py#L11)) | id | provider_profile_id -> provider_profiles.id (delete unspecified; update unspecified), reviewed_by -> users.id (delete unspecified; update unspecified) | None declared | ix_artisan_listings_approval_status (artisan_listings.approval_status), ix_artisan_listings_craft_category (artisan_listings.craft_category), ix_artisan_listings_district (artisan_listings.district), ix_artisan_listings_provider_profile_id (artisan_listings.provider_profile_id), ix_artisan_listings_verification_status (artisan_listings.verification_status) | None declared | business_hours -> artisan_business_hours (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=False), business_images -> artisan_business_images (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=False), promotions -> promotions (merge,save-update; passive_deletes=False), provider_profile -> provider_profiles (merge,save-update; passive_deletes=False), reservations -> artisan_reservations (merge,save-update; passive_deletes=False), showcase_items -> showcase_items (merge,save-update; passive_deletes=False) |
| artisan_product_favorites / ArtisanProductFavorite ([app/models/artisan.py:74](../../../app/models/artisan.py#L74)) | id | product_id -> showcase_items.id (delete CASCADE; update unspecified), user_id -> users.id (delete CASCADE; update unspecified) | UQ_artisan_favorites_user_product (user_id, product_id) | ix_artisan_product_favorites_product_id (artisan_product_favorites.product_id) | None declared | None declared |
| artisan_product_images / ArtisanProductImage ([app/models/artisan.py:53](../../../app/models/artisan.py#L53)) | id | showcase_item_id -> showcase_items.id (delete CASCADE; update unspecified) | None declared | ix_artisan_product_images_showcase_item_id (artisan_product_images.showcase_item_id) | None declared | showcase_item -> showcase_items (merge,save-update; passive_deletes=False) |
| artisan_reservations / ArtisanReservation ([app/models/artisan.py:92](../../../app/models/artisan.py#L92)) | id | artisan_listing_id -> artisan_listings.id (delete unspecified; update unspecified), cancelled_by -> users.id (delete unspecified; update unspecified), rejected_by -> users.id (delete unspecified; update unspecified), showcase_item_id -> showcase_items.id (delete unspecified; update unspecified), traveler_id -> users.id (delete unspecified; update unspecified) | None declared | ix_artisan_reservations_artisan_listing_id (artisan_reservations.artisan_listing_id), ix_artisan_reservations_reservation_status (artisan_reservations.reservation_status), ix_artisan_reservations_showcase_item_id (artisan_reservations.showcase_item_id), ix_artisan_reservations_traveler_id (artisan_reservations.traveler_id) | None declared | artisan_listing -> artisan_listings (merge,save-update; passive_deletes=False), canceller -> users (merge,save-update; passive_deletes=False), showcase_item -> showcase_items (merge,save-update; passive_deletes=False), traveler -> users (merge,save-update; passive_deletes=False) |
| booking_references / BookingReference ([app/models/shared.py:46](../../../app/models/shared.py#L46)) | id | provider_profile_id -> provider_profiles.id (delete unspecified; update unspecified), user_id -> users.id (delete unspecified; update unspecified) | unnamed (booking_reference), UQ_booking_references_target (module_type, target_booking_id) | ix_booking_references_provider_profile_id (booking_references.provider_profile_id), ix_booking_references_user_id (booking_references.user_id) | None declared | None declared |
| booking_status_history / BookingStatusHistory ([app/models/shared.py:356](../../../app/models/shared.py#L356)) | id | booking_reference_id -> booking_references.id (delete CASCADE; update unspecified), changed_by -> users.id (delete unspecified; update unspecified) | None declared | ix_booking_status_history_booking_reference_id (booking_status_history.booking_reference_id) | None declared | None declared |
| community_comments / CommunityComment ([app/models/community.py:112](../../../app/models/community.py#L112)) | id | author_id -> users.id (delete unspecified; update unspecified), parent_comment_id -> community_comments.id (delete unspecified; update unspecified), post_id -> community_posts.id (delete CASCADE; update unspecified) | None declared | ix_community_comments_author_id (community_comments.author_id), ix_community_comments_parent_comment_id (community_comments.parent_comment_id), ix_community_comments_post_id (community_comments.post_id) | CK_community_comments_status: status IN ('active', 'deleted_by_author', 'removed_by_moderator') | author -> users (merge,save-update; passive_deletes=False), post -> community_posts (merge,save-update; passive_deletes=False) |
| community_follows / CommunityFollow ([app/models/community.py:168](../../../app/models/community.py#L168)) | id | follower_id -> users.id (delete unspecified; update unspecified), following_id -> users.id (delete unspecified; update unspecified) | UQ_community_follows (follower_id, following_id) | ix_community_follows_follower_id (community_follows.follower_id), ix_community_follows_following_id (community_follows.following_id) | CK_community_follows_distinct: follower_id <> following_id | follower -> users (merge,save-update; passive_deletes=False), following -> users (merge,save-update; passive_deletes=False) |
| community_hashtags / CommunityHashtag ([app/models/community.py:218](../../../app/models/community.py#L218)) | id | None declared | unnamed (tag) | None declared | CK_community_hashtags_block_feature: NOT (is_blocked = 1 AND is_featured = 1), CK_community_hashtags_usage: usage_count >= 0 | None declared |
| community_post_hashtags / CommunityPostHashtag ([app/models/community.py:276](../../../app/models/community.py#L276)) | id | hashtag_id -> community_hashtags.id (delete CASCADE; update unspecified), post_id -> community_posts.id (delete CASCADE; update unspecified) | UQ_community_post_hashtags (post_id, hashtag_id) | ix_community_post_hashtags_hashtag_id (community_post_hashtags.hashtag_id) | None declared | None declared |
| community_post_likes / CommunityPostLike ([app/models/community.py:90](../../../app/models/community.py#L90)) | id | post_id -> community_posts.id (delete CASCADE; update unspecified), user_id -> users.id (delete unspecified; update unspecified) | UQ_community_post_likes (post_id, user_id) | ix_community_post_likes_user_id (community_post_likes.user_id) | None declared | post -> community_posts (merge,save-update; passive_deletes=False), user -> users (merge,save-update; passive_deletes=False) |
| community_post_media / CommunityPostMedia ([app/models/community.py:65](../../../app/models/community.py#L65)) | id | post_id -> community_posts.id (delete CASCADE; update unspecified) | None declared | ix_community_post_media_post_id (community_post_media.post_id) | CK_community_post_media_order: display_order >= 0, CK_community_post_media_type: media_type IN ('image', 'video') | post -> community_posts (merge,save-update; passive_deletes=False) |
| community_posts / CommunityPost ([app/models/community.py:22](../../../app/models/community.py#L22)) | id | author_id -> users.id (delete CASCADE; update unspecified), listing_reference_id -> listing_references.id (delete SET NULL; update unspecified) | None declared | ix_community_posts_author_id (community_posts.author_id), ix_community_posts_created_at (community_posts.created_at), ix_community_posts_district (community_posts.district) | CK_community_posts_comments: comments_count >= 0, CK_community_posts_likes: likes_count >= 0, CK_community_posts_mod_status: moderation_status IN ('active', 'hidden_by_admin', 'removed_by_author') | author -> users (merge,save-update; passive_deletes=False), comments -> community_comments (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=False), listing_reference -> listing_references (merge,save-update; passive_deletes=False), media -> community_post_media (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=False) |
| community_reports / CommunityReport ([app/models/community.py:191](../../../app/models/community.py#L191)) | id | reporter_id -> users.id (delete unspecified; update unspecified), reviewed_by -> users.id (delete unspecified; update unspecified) | None declared | ix_community_reports_report_status (community_reports.report_status), ix_community_reports_reporter_id (community_reports.reporter_id) | CK_community_reports_status: report_status IN ('pending', 'investigating', 'resolved', 'dismissed'), CK_community_reports_type: entity_type IN ('post', 'comment', 'user') | None declared |
| community_saved_posts / CommunitySavedPost ([app/models/community.py:146](../../../app/models/community.py#L146)) | id | post_id -> community_posts.id (delete unspecified; update unspecified), user_id -> users.id (delete CASCADE; update unspecified) | UQ_community_saved_posts (user_id, post_id) | ix_community_saved_posts_post_id (community_saved_posts.post_id), ix_community_saved_posts_user_id (community_saved_posts.user_id) | None declared | post -> community_posts (merge,save-update; passive_deletes=False), user -> users (merge,save-update; passive_deletes=False) |
| community_user_profiles / CommunityUserProfile ([app/models/community.py:239](../../../app/models/community.py#L239)) | user_id | suspended_by -> users.id (delete unspecified; update unspecified), user_id -> users.id (delete unspecified; update unspecified) | None declared | None declared | CK_community_user_profiles_suspension: is_community_suspended = 0 OR (suspended_by IS NOT NULL AND suspended_at IS NOT NULL AND suspension_reason IS NOT NULL), CK_community_user_profiles_until: suspended_until IS NULL OR suspended_at IS NULL OR suspended_until > suspended_at | moderator -> users (merge,save-update; passive_deletes=False), user -> users (merge,save-update; passive_deletes=False) |
| eco_age_limits / EcoAgeLimit ([app/models/eco_reservation.py:145](../../../app/models/eco_reservation.py#L145)) | eco_experience_id | eco_experience_id -> eco_experiences.id (delete unspecified; update unspecified) | None declared | None declared | CK_eco_maximum_age: maximum_age BETWEEN 0 AND 100 | None declared |
| eco_availability_slots / EcoAvailabilitySlot ([app/models/eco_reservation.py:23](../../../app/models/eco_reservation.py#L23)) | id | eco_experience_id -> eco_experiences.id (delete unspecified; update unspecified) | None declared | IX_eco_slot_schedule (eco_availability_slots.eco_experience_id, eco_availability_slots.starts_at) | CK_eco_slot_capacity: capacity BETWEEN 1 AND 50 AND reserved_count BETWEEN 0 AND capacity, CK_eco_slot_times: ends_at > starts_at | None declared |
| eco_cancellation_policies / EcoCancellationPolicy ([app/models/eco_reservation.py:98](../../../app/models/eco_reservation.py#L98)) | eco_experience_id | eco_experience_id -> eco_experiences.id (delete unspecified; update unspecified) | None declared | None declared | CK_eco_policy_percent: late_refund_percent BETWEEN 0 AND 100 | None declared |
| eco_cancellation_snapshots / EcoCancellationSnapshot ([app/models/eco_reservation.py:113](../../../app/models/eco_reservation.py#L113)) | reservation_id | reservation_id -> eco_reservations.id (delete unspecified; update unspecified) | None declared | None declared | CK_eco_policy_snapshot_percent: late_refund_percent BETWEEN 0 AND 100 | None declared |
| eco_evidences / EcoEvidence ([app/models/eco_experience.py:649](../../../app/models/eco_experience.py#L649)) | id | eco_practice_id -> eco_practices.id (delete CASCADE; update unspecified) | None declared | ix_eco_evidences_eco_practice_id (eco_evidences.eco_practice_id) | CK_eco_evidences_document_type: document_type IN ('certificate', 'permit', 'photo_proof', 'audit_report') | eco_practice -> eco_practices (merge,save-update; passive_deletes=False) |
| eco_experience_images / EcoExperienceImage ([app/models/eco_experience.py:434](../../../app/models/eco_experience.py#L434)) | id | eco_experience_id -> eco_experiences.id (delete CASCADE; update unspecified) | None declared | ix_eco_experience_images_eco_experience_id (eco_experience_images.eco_experience_id) | CK_eco_experience_images_sort_order: sort_order >= 0 | eco_experience -> eco_experiences (merge,save-update; passive_deletes=False) |
| eco_experiences / EcoExperience ([app/models/eco_experience.py:100](../../../app/models/eco_experience.py#L100)) | id | approval_reviewed_by -> users.id (delete unspecified; update unspecified), linked_homestay_id -> homestays.id (delete unspecified; update unspecified), provider_profile_id -> provider_profiles.id (delete unspecified; update unspecified) | None declared | ix_eco_experiences_district (eco_experiences.district), ix_eco_experiences_eco_badge_status (eco_experiences.eco_badge_status), ix_eco_experiences_eco_category (eco_experiences.eco_category), ix_eco_experiences_is_published (eco_experiences.is_published), ix_eco_experiences_linked_homestay_id (eco_experiences.linked_homestay_id), ix_eco_experiences_listing_status (eco_experiences.listing_status), ix_eco_experiences_provider_profile_id (eco_experiences.provider_profile_id) | CK_eco_experiences_badge_status: eco_badge_status IN ('none', 'pending', 'granted', 'rejected', 'revoked'), CK_eco_experiences_base_price: base_price >= 0, CK_eco_experiences_cancellation_hours: free_cancellation_hours >= 0, CK_eco_experiences_category: eco_category IN ('Forest & Nature Trails', 'Wildlife & Birdwatching', 'Sustainable Village Experiences', 'Conservation Activities', 'Eco-Friendly Stays', 'Community Eco-Tourism'), CK_eco_experiences_duration_hours: duration_hours > 0, CK_eco_experiences_latitude: latitude IS NULL OR (latitude >= -90.0 AND latitude <= 90.0), CK_eco_experiences_listing_status: listing_status IN ('draft', 'pending_approval', 'approved', 'rejected', 'deactivated'), CK_eco_experiences_longitude: longitude IS NULL OR (longitude >= -180.0 AND longitude <= 180.0), CK_eco_experiences_max_group_size: max_group_size > 0 AND max_group_size <= 50, CK_eco_experiences_min_age: min_age IS NULL OR (min_age >= 0 AND min_age <= 100), CK_eco_experiences_min_participants: min_participants > 0 AND min_participants <= max_group_size, CK_eco_experiences_pricing_type: pricing_type IN ('per_person', 'per_group') | approval_reviewer -> users (merge,save-update; passive_deletes=False), images -> eco_experience_images (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=True), linked_homestay -> homestays (merge,save-update; passive_deletes=False), practices -> eco_practices (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=True), provider_profile -> provider_profiles (merge,save-update; passive_deletes=False), safety_rules -> eco_safety_rules (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=True), season_restrictions -> eco_season_restrictions (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=True), verifications -> eco_verifications (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=True) |
| eco_practices / EcoPractice ([app/models/eco_experience.py:591](../../../app/models/eco_experience.py#L591)) | id | eco_experience_id -> eco_experiences.id (delete CASCADE; update unspecified) | None declared | ix_eco_practices_eco_experience_id (eco_practices.eco_experience_id), ix_eco_practices_practice_category (eco_practices.practice_category) | CK_eco_practices_category: practice_category IN ('waste_reduction', 'renewable_energy', 'community_employment', 'wildlife_protection', 'habitat_restoration', 'sustainable_sourcing') | eco_experience -> eco_experiences (merge,save-update; passive_deletes=False), evidences -> eco_evidences (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=True) |
| eco_promotion_analytics / EcoPromotionAnalytics ([app/models/eco_promotion.py:311](../../../app/models/eco_promotion.py#L311)) | id | eco_experience_id -> eco_experiences.id (delete NO ACTION; update unspecified), promotion_id -> eco_sponsored_promotions.id (delete CASCADE; update unspecified) | UQ_eco_promotion_analytics (promotion_id, metric_date) | ix_eco_promotion_analytics_eco_experience_id (eco_promotion_analytics.eco_experience_id), ix_eco_promotion_analytics_metric_date (eco_promotion_analytics.metric_date), ix_eco_promotion_analytics_promotion_id (eco_promotion_analytics.promotion_id) | CK_eco_promo_counts: impressions_count >= 0 AND clicks_count >= 0 | eco_experience -> eco_experiences (merge,save-update; passive_deletes=False), promotion -> eco_sponsored_promotions (merge,save-update; passive_deletes=False) |
| eco_promotion_attributions / EcoPromotionAttribution ([app/models/eco_promotion.py:62](../../../app/models/eco_promotion.py#L62)) | booking_reference_id | booking_reference_id -> booking_references.id (delete unspecified; update unspecified), promotion_id -> eco_sponsored_promotions.id (delete unspecified; update unspecified) | unnamed (nonce) | ix_eco_promotion_attributions_promotion_id (eco_promotion_attributions.promotion_id) | None declared | None declared |
| eco_promotion_event_receipts / EcoPromotionEventReceipt ([app/models/eco_promotion.py:51](../../../app/models/eco_promotion.py#L51)) | nonce, event_type | promotion_id -> eco_sponsored_promotions.id (delete unspecified; update unspecified) | None declared | None declared | CK_eco_event_type: event_type IN ('impression', 'click') | None declared |
| eco_promotion_packages / EcoPromotionPackage ([app/models/eco_promotion.py:104](../../../app/models/eco_promotion.py#L104)) | id | None declared | UQ_eco_promotion_packages_name (name) | ix_eco_promotion_packages_is_active (eco_promotion_packages.is_active) | CK_eco_promo_pkg_days: duration_days > 0, CK_eco_promo_pkg_price: price >= 0.0, CK_eco_promo_pkg_tier: placement_tier IN ('standard', 'featured', 'spotlight') | promotions -> eco_sponsored_promotions (merge,save-update; passive_deletes=False) |
| eco_refund_reconciliations / EcoRefundReconciliation ([app/models/eco_reservation.py:126](../../../app/models/eco_reservation.py#L126)) | refund_id | refund_id -> payment_refunds.id (delete unspecified; update unspecified) | None declared | UX_eco_refund_gateway_reference (eco_refund_reconciliations.gateway_refund_reference) UNIQUE where=gateway_refund_reference IS NOT NULL | CK_eco_refund_reconciliation_state: state IN ('processing', 'succeeded', 'failed', 'reconciliation_required') | None declared |
| eco_reservations / EcoReservation ([app/models/eco_reservation.py:46](../../../app/models/eco_reservation.py#L46)) | id | slot_id -> eco_availability_slots.id (delete unspecified; update unspecified), traveler_id -> users.id (delete unspecified; update unspecified) | None declared | IX_eco_reservation_traveler (eco_reservations.traveler_id, eco_reservations.id), UX_eco_reservation_active_traveler (eco_reservations.slot_id, eco_reservations.traveler_id) UNIQUE where=holds_capacity = 1 | CK_eco_reservation_amount: total_price >= 0, CK_eco_reservation_participants: participants BETWEEN 1 AND 50 AND capacity_held BETWEEN participants AND 50, CK_eco_reservation_payment: payment_status = 'unpaid' OR (payment_status = 'paid' AND status IN ('confirmed', 'completed')) OR (payment_status IN ('refund_pending', 'refund_rejected', 'no_refund_due', 'refund_failed', 'refunded', 'partially_refunded') AND status = 'cancelled'), CK_eco_reservation_state: (holds_capacity = 1 AND status IN ('pending', 'confirmed', 'completed')) OR (holds_capacity = 0 AND status IN ('rejected', 'cancelled')), unnamed: free_cancellation_hours >= 0 | None declared |
| eco_safety_rules / EcoSafetyRule ([app/models/eco_experience.py:479](../../../app/models/eco_experience.py#L479)) | id | eco_experience_id -> eco_experiences.id (delete CASCADE; update unspecified) | None declared | ix_eco_safety_rules_eco_experience_id (eco_safety_rules.eco_experience_id), ix_eco_safety_rules_rule_type (eco_safety_rules.rule_type) | CK_eco_safety_rules_type: rule_type IN ('wildlife_no_feeding', 'environmental_conduct', 'operator_instruction', 'equipment_requirement', 'health_age_restriction') | eco_experience -> eco_experiences (merge,save-update; passive_deletes=False) |
| eco_season_restrictions / EcoSeasonRestriction ([app/models/eco_experience.py:528](../../../app/models/eco_experience.py#L528)) | id | created_by -> users.id (delete unspecified; update unspecified), eco_experience_id -> eco_experiences.id (delete CASCADE; update unspecified) | None declared | ix_eco_season_restrictions_eco_experience_id (eco_season_restrictions.eco_experience_id), ix_eco_season_restrictions_is_active (eco_season_restrictions.is_active), ix_eco_season_restrictions_restriction_type (eco_season_restrictions.restriction_type) | CK_eco_season_restrictions_dates: end_date >= start_date, CK_eco_season_restrictions_type: restriction_type IN ('monsoon_season', 'wildlife_breeding', 'weather_alert', 'park_closure') | creator -> users (merge,save-update; passive_deletes=False), eco_experience -> eco_experiences (merge,save-update; passive_deletes=False) |
| eco_slot_clearances / EcoSlotClearance ([app/models/eco_reservation.py:157](../../../app/models/eco_reservation.py#L157)) | slot_id, kind | slot_id -> eco_availability_slots.id (delete unspecified; update unspecified), verified_by -> users.id (delete unspecified; update unspecified) | None declared | None declared | CK_eco_clearance_kind: kind IN ('permit', 'guide') | None declared |
| eco_sponsored_promotions / EcoSponsoredPromotion ([app/models/eco_promotion.py:159](../../../app/models/eco_promotion.py#L159)) | id | eco_experience_id -> eco_experiences.id (delete CASCADE; update unspecified), package_id -> eco_promotion_packages.id (delete NO ACTION; update unspecified), provider_profile_id -> provider_profiles.id (delete NO ACTION; update unspecified), reviewed_by -> users.id (delete SET NULL; update unspecified) | None declared | ix_eco_sponsored_promotions_campaign_status (eco_sponsored_promotions.campaign_status), ix_eco_sponsored_promotions_eco_experience_id (eco_sponsored_promotions.eco_experience_id), ix_eco_sponsored_promotions_ends_at (eco_sponsored_promotions.ends_at), ix_eco_sponsored_promotions_package_id (eco_sponsored_promotions.package_id), ix_eco_sponsored_promotions_payment_status (eco_sponsored_promotions.payment_status), ix_eco_sponsored_promotions_provider_profile_id (eco_sponsored_promotions.provider_profile_id), ix_eco_sponsored_promotions_reviewed_by (eco_sponsored_promotions.reviewed_by), ix_eco_sponsored_promotions_starts_at (eco_sponsored_promotions.starts_at) | CK_eco_spon_active_consistent: campaign_status <> 'active' OR (starts_at IS NOT NULL AND ends_at IS NOT NULL AND payment_status = 'paid'), CK_eco_spon_dates: ends_at IS NULL OR starts_at IS NULL OR ends_at >= starts_at, CK_eco_spon_days: duration_days_snapshot > 0, CK_eco_spon_payment: payment_status IN ('unpaid', 'pending_verification', 'paid', 'failed', 'refunded'), CK_eco_spon_price: package_price_snapshot >= 0.0, CK_eco_spon_status: campaign_status IN ('pending_approval', 'approved', 'active', 'rejected', 'expired', 'cancelled') | analytics -> eco_promotion_analytics (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=False), eco_experience -> eco_experiences (merge,save-update; passive_deletes=False), package -> eco_promotion_packages (merge,save-update; passive_deletes=False), provider_profile -> provider_profiles (merge,save-update; passive_deletes=False), reviewer -> users (merge,save-update; passive_deletes=False) |
| eco_sponsorship_captures / EcoSponsorshipCapture ([app/models/eco_promotion.py:34](../../../app/models/eco_promotion.py#L34)) | gateway_id | promotion_id -> eco_sponsored_promotions.id (delete unspecified; update unspecified) | None declared | UX_eco_sponsor_applied (eco_sponsorship_captures.promotion_id) UNIQUE where=applied = 1, ix_eco_sponsorship_captures_promotion_id (eco_sponsorship_captures.promotion_id) | None declared | None declared |
| eco_verifications / EcoVerification ([app/models/eco_experience.py:698](../../../app/models/eco_experience.py#L698)) | id | eco_experience_id -> eco_experiences.id (delete CASCADE; update unspecified), reviewed_by_admin_id -> users.id (delete unspecified; update unspecified), submitted_by_provider_id -> provider_profiles.id (delete unspecified; update unspecified) | None declared | ix_eco_verifications_decision (eco_verifications.decision), ix_eco_verifications_eco_experience_id (eco_verifications.eco_experience_id), ix_eco_verifications_reviewed_by_admin_id (eco_verifications.reviewed_by_admin_id), ix_eco_verifications_submitted_by_provider_id (eco_verifications.submitted_by_provider_id) | CK_eco_verifications_decision: decision IN ('submitted', 'granted', 'rejected', 'revoked') | eco_experience -> eco_experiences (merge,save-update; passive_deletes=False), reviewed_by_admin -> users (merge,save-update; passive_deletes=False), submitted_by_provider -> provider_profiles (merge,save-update; passive_deletes=False) |
| eco_wishlists / EcoWishlist ([app/models/eco_wishlist.py:14](../../../app/models/eco_wishlist.py#L14)) | id | eco_experience_id -> eco_experiences.id (delete CASCADE; update unspecified), user_id -> users.id (delete CASCADE; update unspecified) | UQ_eco_wishlists_user_experience (user_id, eco_experience_id) | ix_eco_wishlists_eco_experience_id (eco_wishlists.eco_experience_id), ix_eco_wishlists_user_id (eco_wishlists.user_id) | None declared | eco_experience -> eco_experiences (merge,save-update; passive_deletes=False), user -> users (merge,save-update; passive_deletes=False) |
| homestay_amenities / HomestayAmenity ([app/models/homestay.py:297](../../../app/models/homestay.py#L297)) | id | homestay_id -> homestays.id (delete CASCADE; update unspecified) | UQ_homestay_amenities_name (homestay_id, name) | ix_homestay_amenities_homestay_id (homestay_amenities.homestay_id) | None declared | homestay -> homestays (merge,save-update; passive_deletes=False) |
| homestay_availability_blocks / HomestayAvailabilityBlock ([app/models/homestay.py:332](../../../app/models/homestay.py#L332)) | id | created_by -> users.id (delete unspecified; update unspecified), homestay_id -> homestays.id (delete CASCADE; update unspecified) | None declared | ix_homestay_availability_blocks_homestay_id (homestay_availability_blocks.homestay_id), ix_homestay_availability_blocks_is_active (homestay_availability_blocks.is_active) | CK_homestay_availability_blocks_dates: end_date > start_date, CK_homestay_availability_blocks_type: block_type IN ('maintenance', 'owner_unavailable', 'admin_block', 'renovation', 'seasonal_closure', 'other') | creator -> users (merge,save-update; passive_deletes=False), homestay -> homestays (merge,save-update; passive_deletes=False) |
| homestay_bookings / Booking ([app/models/booking.py:24](../../../app/models/booking.py#L24)) | id | cancelled_by -> users.id (delete unspecified; update unspecified), homestay_id -> homestays.id (delete unspecified; update unspecified), user_id -> users.id (delete unspecified; update unspecified) | None declared | ix_homestay_bookings_homestay_id (homestay_bookings.homestay_id), ix_homestay_bookings_payment_expires_at (homestay_bookings.payment_expires_at), ix_homestay_bookings_user_id (homestay_bookings.user_id) | CK_bookings_cancellation_hours_snapshot: free_cancellation_hours_snapshot >= 0, CK_bookings_dates: check_out > check_in, CK_bookings_guests: guests > 0, CK_bookings_payment_method: payment_method IN ('online', 'pay_at_location'), CK_bookings_payment_status: payment_status IN ('unpaid', 'pending', 'paid', 'refund_pending', 'refunded', 'failed'), CK_bookings_status: status IN ('pending', 'awaiting_payment', 'confirmed', 'in_progress', 'completed', 'cancelled', 'rejected', 'expired'), CK_bookings_total_price: total_price >= 0 | canceller -> users (merge,save-update; passive_deletes=False), homestay -> homestays (merge,save-update; passive_deletes=False), user -> users (merge,save-update; passive_deletes=False) |
| homestay_images / HomestayImage ([app/models/homestay.py:264](../../../app/models/homestay.py#L264)) | id | homestay_id -> homestays.id (delete CASCADE; update unspecified) | None declared | ix_homestay_images_homestay_id (homestay_images.homestay_id) | None declared | homestay -> homestays (merge,save-update; passive_deletes=False) |
| homestay_promotions / HomestayPromotion ([app/models/promotion.py:124](../../../app/models/promotion.py#L124)) | id | homestay_id -> homestays.id (delete unspecified; update unspecified) | None declared | ix_homestay_promotions_homestay_id (homestay_promotions.homestay_id) | CK_homestay_promotions_dates: end_date >= start_date, CK_homestay_promotions_discount_valid: ((discount_type = 'percentage' AND discount_percent IS NOT NULL AND discount_percent > 0 AND discount_percent <= 100 AND discount_amount IS NULL) OR (discount_type = 'fixed' AND discount_amount IS NOT NULL AND discount_amount > 0 AND discount_percent IS NULL)), CK_homestay_promotions_type: discount_type IN ('percentage', 'fixed') | homestay -> homestays (merge,save-update; passive_deletes=False) |
| homestays / Homestay ([app/models/homestay.py:28](../../../app/models/homestay.py#L28)) | id | owner_id -> users.id (delete unspecified; update unspecified), provider_profile_id -> provider_profiles.id (delete unspecified; update unspecified), reviewed_by -> users.id (delete unspecified; update unspecified) | None declared | ix_homestays_district (homestays.district), ix_homestays_owner_id (homestays.owner_id), ix_homestays_provider_profile_id (homestays.provider_profile_id), ix_homestays_reviewed_by (homestays.reviewed_by) | CK_homestays_approval_status: approval_status IN ('draft', 'pending', 'approved', 'rejected', 'suspended'), CK_homestays_cancellation_hours: free_cancellation_hours >= 0, CK_homestays_coords: (latitude IS NULL AND longitude IS NULL) OR (latitude BETWEEN -90.0 AND 90.0 AND longitude BETWEEN -180.0 AND 180.0), CK_homestays_guests: max_guests > 0, CK_homestays_price: price_per_night >= 0, CK_homestays_verification_status: verification_status IN ('unverified', 'pending', 'verified', 'rejected', 'suspended') | amenities -> homestay_amenities (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=True), availability_blocks -> homestay_availability_blocks (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=True), bookings -> homestay_bookings (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=True), images -> homestay_images (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=True), linked_eco_experiences -> eco_experiences (merge,save-update; passive_deletes=False), owner -> users (merge,save-update; passive_deletes=False), provider_profile -> provider_profiles (merge,save-update; passive_deletes=False), reviewer -> users (merge,save-update; passive_deletes=False) |
| listing_references / ListingReference ([app/models/shared.py:23](../../../app/models/shared.py#L23)) | id | provider_profile_id -> provider_profiles.id (delete unspecified; update unspecified) | UQ_listing_references (module_type, target_id) | ix_listing_references_district (listing_references.district), ix_listing_references_provider_profile_id (listing_references.provider_profile_id) | None declared | None declared |
| notifications / Notification ([app/models/shared.py:165](../../../app/models/shared.py#L165)) | id | user_id -> users.id (delete unspecified; update unspecified) | None declared | ix_notifications_user_id (notifications.user_id) | None declared | None declared |
| password_resets / PasswordReset ([app/models/password_reset.py:9](../../../app/models/password_reset.py#L9)) | id | user_id -> users.id (delete CASCADE; update unspecified) | None declared | ix_password_resets_token_hash (password_resets.token_hash), ix_password_resets_user_id (password_resets.user_id) | None declared | user -> users (merge,save-update; passive_deletes=False) |
| payment_refunds / PaymentRefund ([app/models/shared.py:126](../../../app/models/shared.py#L126)) | id | payment_transaction_id -> payment_transactions.id (delete unspecified; update unspecified), processed_by -> users.id (delete unspecified; update unspecified) | None declared | ix_payment_refunds_payment_transaction_id (payment_refunds.payment_transaction_id) | None declared | None declared |
| payment_transactions / PaymentTransaction ([app/models/shared.py:101](../../../app/models/shared.py#L101)) | id | booking_reference_id -> booking_references.id (delete unspecified; update unspecified), user_id -> users.id (delete unspecified; update unspecified) | None declared | ix_payment_transactions_booking_reference_id (payment_transactions.booking_reference_id), ix_payment_transactions_user_id (payment_transactions.user_id) | None declared | None declared |
| promotion_campaigns / PromotionCampaign ([app/models/shared.py:267](../../../app/models/shared.py#L267)) | id | listing_reference_id -> listing_references.id (delete SET NULL; update unspecified), provider_profile_id -> provider_profiles.id (delete CASCADE; update unspecified) | None declared | ix_promotion_campaigns_listing_reference_id (promotion_campaigns.listing_reference_id), ix_promotion_campaigns_provider_profile_id (promotion_campaigns.provider_profile_id) | None declared | advertising_payments -> advertising_payments (merge,save-update; passive_deletes=False), listing_reference -> listing_references (merge,save-update; passive_deletes=False) |
| promotion_events / PromotionEvent ([app/models/shared.py:333](../../../app/models/shared.py#L333)) | id | booking_reference_id -> booking_references.id (delete SET NULL; update unspecified), campaign_id -> promotion_campaigns.id (delete CASCADE; update unspecified), user_id -> users.id (delete unspecified; update unspecified) | None declared | ix_promotion_events_booking_reference_id (promotion_events.booking_reference_id), ix_promotion_events_campaign_id (promotion_events.campaign_id) | None declared | booking_reference -> booking_references (merge,save-update; passive_deletes=False) |
| promotion_packages / PromotionPackage ([app/models/shared.py:247](../../../app/models/shared.py#L247)) | id | provider_profile_id -> provider_profiles.id (delete CASCADE; update unspecified) | None declared | ix_promotion_packages_provider_profile_id (promotion_packages.provider_profile_id) | None declared | None declared |
| promotions / Promotion ([app/models/promotion.py:28](../../../app/models/promotion.py#L28)) | id | artisan_listing_id -> artisan_listings.id (delete unspecified; update unspecified), showcase_item_id -> showcase_items.id (delete unspecified; update unspecified) | None declared | ix_promotions_artisan_listing_id (promotions.artisan_listing_id), ix_promotions_showcase_item_id (promotions.showcase_item_id) | None declared | artisan_listing -> artisan_listings (merge,save-update; passive_deletes=False), showcase_item -> showcase_items (merge,save-update; passive_deletes=False) |
| provider_email_otps / ProviderEmailOtp ([app/models/provider_email_otp.py:9](../../../app/models/provider_email_otp.py#L9)) | id | None declared | None declared | ix_provider_email_otps_email (provider_email_otps.email), ix_provider_email_otps_expires_at (provider_email_otps.expires_at), ix_provider_email_otps_purpose (provider_email_otps.purpose) | None declared | None declared |
| provider_profiles / ProviderProfile ([app/models/provider_profile.py:9](../../../app/models/provider_profile.py#L9)) | id | reviewed_by -> users.id (delete unspecified; update unspecified), user_id -> users.id (delete unspecified; update unspecified) | None declared | ix_provider_profiles_approval_status (provider_profiles.approval_status), ix_provider_profiles_nic_number (provider_profiles.nic_number), ix_provider_profiles_provider_type (provider_profiles.provider_type), ix_provider_profiles_user_id (provider_profiles.user_id) UNIQUE | None declared | artisan_listings -> artisan_listings (merge,save-update; passive_deletes=False), eco_experiences -> eco_experiences (merge,save-update; passive_deletes=False), homestays -> homestays (merge,save-update; passive_deletes=False), reviewer -> users (merge,save-update; passive_deletes=False), tour_guide -> tour_guides (merge,save-update; passive_deletes=False), user -> users (merge,save-update; passive_deletes=False) |
| provider_registration_authorizations / ProviderRegistrationAuthorization ([app/models/provider_registration_authorization.py:9](../../../app/models/provider_registration_authorization.py#L9)) | id | None declared | None declared | ix_provider_registration_authorizations_email (provider_registration_authorizations.email), ix_provider_registration_authorizations_expires_at (provider_registration_authorizations.expires_at), ix_provider_registration_authorizations_purpose (provider_registration_authorizations.purpose), ix_provider_registration_authorizations_token_hash (provider_registration_authorizations.token_hash) UNIQUE | None declared | None declared |
| provider_registration_drafts / ProviderRegistrationDraft ([app/models/provider_registration_draft.py:9](../../../app/models/provider_registration_draft.py#L9)) | id | None declared | None declared | ix_provider_registration_drafts_email (provider_registration_drafts.email) UNIQUE, ix_provider_registration_drafts_nic_number (provider_registration_drafts.nic_number), ix_provider_registration_drafts_provider_category (provider_registration_drafts.provider_category) | None declared | None declared |
| provider_settlements / ProviderSettlement ([app/models/shared.py:144](../../../app/models/shared.py#L144)) | id | provider_profile_id -> provider_profiles.id (delete unspecified; update unspecified) | None declared | ix_provider_settlements_provider_profile_id (provider_settlements.provider_profile_id) | None declared | None declared |
| rental_availability_blocks / RentalAvailabilityBlock ([app/models/rental.py:153](../../../app/models/rental.py#L153)) | id | vehicle_id -> rental_vehicles.id (delete CASCADE; update unspecified) | None declared | ix_rental_availability_blocks_vehicle_id (rental_availability_blocks.vehicle_id) | CK_rental_availability_blocks_dates: end_datetime > start_datetime, CK_rental_availability_blocks_type: block_type IN ('maintenance', 'service', 'accident_repair', 'owner_unavailable', 'administrative') | vehicle -> rental_vehicles (merge,save-update; passive_deletes=False) |
| rental_bookings / RentalBooking ([app/models/rental_booking.py:18](../../../app/models/rental_booking.py#L18)) | id | cancelled_by -> users.id (delete unspecified; update unspecified), pickup_location_id -> rental_locations.id (delete unspecified; update unspecified), return_location_id -> rental_locations.id (delete unspecified; update unspecified), user_id -> users.id (delete unspecified; update unspecified), vehicle_id -> rental_vehicles.id (delete unspecified; update unspecified) | None declared | ix_rental_bookings_user_id (rental_bookings.user_id), ix_rental_bookings_vehicle_id (rental_bookings.vehicle_id) | CK_rental_booking_dates: return_at > pickup_at, CK_rental_booking_price: total_price >= 0 | cancelled_by_user -> users (merge,save-update; passive_deletes=False), deposits -> rental_deposits (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=False), incidents -> rental_incidents (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=False), inspections -> rental_inspections (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=False), pickup_location -> rental_locations (merge,save-update; passive_deletes=False), return_location -> rental_locations (merge,save-update; passive_deletes=False), user -> users (merge,save-update; passive_deletes=False), vehicle -> rental_vehicles (merge,save-update; passive_deletes=False) |
| rental_condition_evidence / RentalConditionEvidence ([app/models/rental.py:262](../../../app/models/rental.py#L262)) | id | inspection_id -> rental_inspections.id (delete CASCADE; update unspecified) | None declared | ix_rental_condition_evidence_inspection_id (rental_condition_evidence.inspection_id) | CK_rental_evidence_type: evidence_type IN ('photo', 'video', 'document') | inspection -> rental_inspections (merge,save-update; passive_deletes=False) |
| rental_deposits / RentalDeposit ([app/models/rental.py:330](../../../app/models/rental.py#L330)) | id | booking_id -> rental_bookings.id (delete CASCADE; update unspecified) | None declared | ix_rental_deposits_booking_id (rental_deposits.booking_id) | CK_rental_deposits_amount: deposit_amount >= 0, CK_rental_deposits_deducted: deducted_amount >= 0, CK_rental_deposits_refunded: refunded_amount >= 0, CK_rental_deposits_status: deposit_status IN ('held', 'partially_refunded', 'fully_refunded', 'forfeited', 'deducted') | booking -> rental_bookings (merge,save-update; passive_deletes=False) |
| rental_eligibility_checks / RentalEligibilityCheck ([app/models/rental.py:183](../../../app/models/rental.py#L183)) | id | reviewed_by -> users.id (delete unspecified; update unspecified), user_id -> users.id (delete unspecified; update unspecified) | None declared | ix_rental_eligibility_checks_user_id (rental_eligibility_checks.user_id) | CK_rental_eligibility_status: verification_status IN ('pending', 'approved', 'rejected', 'expired') | reviewer -> users (merge,save-update; passive_deletes=False), user -> users (merge,save-update; passive_deletes=False) |
| rental_incidents / RentalIncident ([app/models/rental.py:287](../../../app/models/rental.py#L287)) | id | booking_id -> rental_bookings.id (delete CASCADE; update unspecified) | None declared | ix_rental_incidents_booking_id (rental_incidents.booking_id) | CK_rental_incidents_act_cost: actual_repair_cost IS NULL OR actual_repair_cost >= 0, CK_rental_incidents_est_cost: estimated_damage_cost >= 0, CK_rental_incidents_status: resolution_status IN ('reported', 'under_investigation', 'insurance_claimed', 'customer_billed', 'resolved', 'dismissed'), CK_rental_incidents_type: incident_type IN ('scratch', 'dent', 'accident', 'mechanical_failure', 'theft', 'late_return', 'traffic_fine') | booking -> rental_bookings (merge,save-update; passive_deletes=False) |
| rental_inspections / RentalInspection ([app/models/rental.py:219](../../../app/models/rental.py#L219)) | id | booking_id -> rental_bookings.id (delete CASCADE; update unspecified), inspected_by -> users.id (delete unspecified; update unspecified) | None declared | ix_rental_inspections_booking_id (rental_inspections.booking_id) | CK_rental_inspections_fuel: fuel_level_percent BETWEEN 0 AND 100, CK_rental_inspections_odometer: odometer_reading >= 0, CK_rental_inspections_type: inspection_type IN ('pickup', 'return') | booking -> rental_bookings (merge,save-update; passive_deletes=False), condition_evidence -> rental_condition_evidence (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=False), inspector -> users (merge,save-update; passive_deletes=False) |
| rental_locations / RentalLocation ([app/models/rental.py:22](../../../app/models/rental.py#L22)) | id | provider_id -> provider_profiles.id (delete unspecified; update unspecified) | None declared | ix_rental_locations_district (rental_locations.district), ix_rental_locations_provider_id (rental_locations.provider_id) | CK_rental_locations_coords: (latitude IS NULL AND longitude IS NULL) OR (latitude BETWEEN -90.0 AND 90.0 AND longitude BETWEEN -180.0 AND 180.0) | provider -> provider_profiles (merge,save-update; passive_deletes=False), vehicles -> rental_vehicles (merge,save-update; passive_deletes=False) |
| rental_rate_plans / RentalRatePlan ([app/models/rental.py:51](../../../app/models/rental.py#L51)) | id | vehicle_id -> rental_vehicles.id (delete CASCADE; update unspecified) | None declared | ix_rental_rate_plans_vehicle_id (rental_rate_plans.vehicle_id) | CK_rental_rate_plans_extra_km: extra_km_charge IS NULL OR extra_km_charge >= 0, CK_rental_rate_plans_rate: rate >= 0, CK_rental_rate_plans_type: duration_type IN ('hourly', 'daily', 'weekly', 'monthly') | vehicle -> rental_vehicles (merge,save-update; passive_deletes=False) |
| rental_vehicle_documents / RentalVehicleDocument ([app/models/rental.py:112](../../../app/models/rental.py#L112)) | id | vehicle_id -> rental_vehicles.id (delete CASCADE; update unspecified) | None declared | ix_rental_vehicle_documents_vehicle_id (rental_vehicle_documents.vehicle_id) | CK_rental_vehicle_docs_coverage: coverage_amount IS NULL OR coverage_amount >= 0, CK_rental_vehicle_docs_deductible: deductible_amount IS NULL OR deductible_amount >= 0, CK_rental_vehicle_documents_type: document_type IN ('revenue_license', 'insurance_certificate', 'vcr', 'fitness_certificate', 'other') | vehicle -> rental_vehicles (merge,save-update; passive_deletes=False) |
| rental_vehicle_images / RentalVehicleImage ([app/models/rental.py:87](../../../app/models/rental.py#L87)) | id | vehicle_id -> rental_vehicles.id (delete CASCADE; update unspecified) | None declared | ix_rental_vehicle_images_vehicle_id (rental_vehicle_images.vehicle_id) | CK_rental_vehicle_images_order: display_order >= 0 | vehicle -> rental_vehicles (merge,save-update; passive_deletes=False) |
| rental_vehicles / RentalVehicle ([app/models/rental_vehicle.py:19](../../../app/models/rental_vehicle.py#L19)) | id | location_id -> rental_locations.id (delete unspecified; update unspecified), provider_id -> provider_profiles.id (delete unspecified; update unspecified), reviewed_by -> users.id (delete unspecified; update unspecified) | None declared | ix_rental_vehicles_provider_id (rental_vehicles.provider_id) | CK_rental_vehicle_price: price_per_day >= 0, CK_rental_vehicle_type: vehicle_type IN ('bike', 'bicycle', 'tuk_tuk'), CK_rental_vehicles_fuel_policy: fuel_policy IS NULL OR fuel_policy IN ('same_to_same', 'full_to_full', 'not_applicable'), CK_rental_vehicles_helmet_count: helmet_count IS NULL OR helmet_count >= 0, CK_rental_vehicles_security_deposit: security_deposit IS NULL OR security_deposit >= 0 | availability_blocks -> rental_availability_blocks (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=False), bookings -> rental_bookings (merge,save-update; passive_deletes=False), documents -> rental_vehicle_documents (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=False), images -> rental_vehicle_images (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=False), location -> rental_locations (merge,save-update; passive_deletes=False), provider -> provider_profiles (merge,save-update; passive_deletes=False), rate_plans -> rental_rate_plans (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=False), reviewer -> users (merge,save-update; passive_deletes=False) |
| reviews / Review ([app/models/shared.py:181](../../../app/models/shared.py#L181)) | id | booking_reference_id -> booking_references.id (delete SET NULL; update unspecified), listing_reference_id -> listing_references.id (delete CASCADE; update unspecified), user_id -> users.id (delete unspecified; update unspecified) | None declared | ix_reviews_booking_reference_id (reviews.booking_reference_id), ix_reviews_listing_reference_id (reviews.listing_reference_id), ix_reviews_user_id (reviews.user_id) | CK_reviews_rating: rating >= 1.00 AND rating <= 5.00 | booking_reference -> booking_references (merge,save-update; passive_deletes=False), listing_reference -> listing_references (merge,save-update; passive_deletes=False), user -> users (merge,save-update; passive_deletes=False) |
| showcase_items / ShowcaseItem ([app/models/showcase_item.py:11](../../../app/models/showcase_item.py#L11)) | id | artisan_listing_id -> artisan_listings.id (delete unspecified; update unspecified) | None declared | ix_showcase_items_approval_status (showcase_items.approval_status), ix_showcase_items_artisan_listing_id (showcase_items.artisan_listing_id), ix_showcase_items_item_type (showcase_items.item_type) | None declared | artisan_listing -> artisan_listings (merge,save-update; passive_deletes=False), product_images -> artisan_product_images (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=False), promotions -> promotions (merge,save-update; passive_deletes=False), reservations -> artisan_reservations (merge,save-update; passive_deletes=False) |
| tour_guide_availability / TourGuideAvailability ([app/models/tour_guide.py:424](../../../app/models/tour_guide.py#L424)) | id | tour_guide_id -> tour_guides.id (delete CASCADE; update unspecified) | UQ_tour_guide_availability_slot (tour_guide_id, available_date, start_time, end_time) | ix_tour_guide_availability_tour_guide_id (tour_guide_availability.tour_guide_id) | CK_tour_guide_availability_times: (start_time IS NULL AND end_time IS NULL) OR (start_time IS NOT NULL AND end_time IS NOT NULL AND end_time > start_time) | tour_guide -> tour_guides (merge,save-update; passive_deletes=False) |
| tour_guide_bookings / TourGuideBooking ([app/models/tour_guide.py:463](../../../app/models/tour_guide.py#L463)) | id | cancelled_by -> users.id (delete unspecified; update unspecified), package_id -> tour_guide_packages.id (delete NO ACTION; update unspecified), tour_guide_id -> tour_guide_packages.tour_guide_id (delete NO ACTION; update unspecified), tour_guide_id -> tour_guides.id (delete unspecified; update unspecified), traveler_id -> users.id (delete unspecified; update unspecified) | None declared | ix_tour_guide_bookings_booking_date (tour_guide_bookings.booking_date), ix_tour_guide_bookings_booking_status (tour_guide_bookings.booking_status), ix_tour_guide_bookings_expires_at (tour_guide_bookings.expires_at), ix_tour_guide_bookings_package_id (tour_guide_bookings.package_id), ix_tour_guide_bookings_start_datetime (tour_guide_bookings.start_datetime), ix_tour_guide_bookings_tour_guide_id (tour_guide_bookings.tour_guide_id), ix_tour_guide_bookings_traveler_id (tour_guide_bookings.traveler_id) | CK_tour_guide_bookings_cancellation_hours_snapshot: free_cancellation_hours_snapshot >= 0, CK_tour_guide_bookings_datetimes: end_datetime > start_datetime, CK_tour_guide_bookings_payment_method: payment_method IN ('online', 'pay_at_location'), CK_tour_guide_bookings_payment_status: payment_status IN ('unpaid', 'pending', 'paid', 'refund_pending', 'refunded', 'failed'), CK_tour_guide_bookings_people: number_of_people > 0, CK_tour_guide_bookings_price: total_price >= 0, CK_tour_guide_bookings_status: booking_status IN ('pending', 'confirmed', 'in_progress', 'completed', 'cancelled', 'rejected', 'expired') | cancelled_by_user -> users (merge,save-update; passive_deletes=False), package -> tour_guide_packages (merge,save-update; passive_deletes=False), tour_guide -> tour_guides (merge,save-update; passive_deletes=False), traveler -> users (merge,save-update; passive_deletes=False) |
| tour_guide_credentials / TourGuideCredential ([app/models/tour_guide.py:258](../../../app/models/tour_guide.py#L258)) | id | tour_guide_id -> tour_guides.id (delete CASCADE; update unspecified) | UQ_tour_guide_credentials_guide (id, tour_guide_id) | ix_tour_guide_credentials_tour_guide_id (tour_guide_credentials.tour_guide_id) | CK_tour_guide_credentials_dates: expiry_date IS NULL OR issue_date IS NULL OR expiry_date >= issue_date, CK_tour_guide_credentials_status: verification_status IN ('pending', 'approved', 'rejected'), CK_tour_guide_credentials_type: credential_type IN ('sltda_license', 'first_aid', 'degree', 'wildlife_permit', 'driving_license', 'other') | tour_guide -> tour_guides (merge,save-update; passive_deletes=False), verifications -> tour_guide_verifications (merge,save-update; passive_deletes=False) |
| tour_guide_images / TourGuideImage ([app/models/tour_guide.py:322](../../../app/models/tour_guide.py#L322)) | id | tour_guide_id -> tour_guides.id (delete CASCADE; update unspecified) | None declared | UQ_tour_guide_images_profile (tour_guide_images.tour_guide_id) UNIQUE where=is_profile = 1, ix_tour_guide_images_tour_guide_id (tour_guide_images.tour_guide_id) | CK_tour_guide_images_order: display_order >= 0 | tour_guide -> tour_guides (merge,save-update; passive_deletes=False) |
| tour_guide_languages / TourGuideLanguage ([app/models/tour_guide.py:198](../../../app/models/tour_guide.py#L198)) | id | tour_guide_id -> tour_guides.id (delete CASCADE; update unspecified) | UQ_tour_guide_languages_code (tour_guide_id, language_code) | ix_tour_guide_languages_tour_guide_id (tour_guide_languages.tour_guide_id) | CK_tour_guide_languages_proficiency: proficiency_level IN ('basic', 'conversational', 'fluent', 'native') | tour_guide -> tour_guides (merge,save-update; passive_deletes=False) |
| tour_guide_packages / TourGuidePackage ([app/models/tour_guide.py:356](../../../app/models/tour_guide.py#L356)) | id | tour_guide_id -> tour_guides.id (delete CASCADE; update unspecified) | UQ_tour_guide_packages_guide (id, tour_guide_id) | ix_tour_guide_packages_tour_guide_id (tour_guide_packages.tour_guide_id) | CK_tour_guide_packages_coords: (meeting_latitude IS NULL AND meeting_longitude IS NULL) OR (meeting_latitude IS NOT NULL AND meeting_longitude IS NOT NULL AND meeting_latitude BETWEEN -90.0 AND 90.0 AND meeting_longitude BETWEEN -180.0 AND 180.0), CK_tour_guide_packages_duration: duration_hours > 0, CK_tour_guide_packages_max_people: max_people > 0, CK_tour_guide_packages_price: price >= 0, CK_tour_guide_packages_pricing_type: pricing_type IN ('per_person', 'per_group') | bookings -> tour_guide_bookings (merge,save-update; passive_deletes=False), tour_guide -> tour_guides (merge,save-update; passive_deletes=False) |
| tour_guide_service_areas / TourGuideServiceArea ([app/models/tour_guide.py:231](../../../app/models/tour_guide.py#L231)) | id | tour_guide_id -> tour_guides.id (delete CASCADE; update unspecified) | UQ_tour_guide_service_areas (tour_guide_id, district, city) | ix_tour_guide_service_areas_district (tour_guide_service_areas.district), ix_tour_guide_service_areas_tour_guide_id (tour_guide_service_areas.tour_guide_id) | None declared | tour_guide -> tour_guides (merge,save-update; passive_deletes=False) |
| tour_guide_verifications / TourGuideVerification ([app/models/tour_guide.py:585](../../../app/models/tour_guide.py#L585)) | id | credential_id -> tour_guide_credentials.id (delete NO ACTION; update unspecified), reviewed_by -> users.id (delete unspecified; update unspecified), tour_guide_id -> tour_guide_credentials.tour_guide_id (delete NO ACTION; update unspecified), tour_guide_id -> tour_guides.id (delete unspecified; update unspecified) | None declared | ix_tour_guide_verifications_credential_id (tour_guide_verifications.credential_id), ix_tour_guide_verifications_reviewed_by (tour_guide_verifications.reviewed_by), ix_tour_guide_verifications_tour_guide_id (tour_guide_verifications.tour_guide_id) | CK_tour_guide_verifications_status: status IN ('approved', 'rejected', 'requires_info') | credential -> tour_guide_credentials (merge,save-update; passive_deletes=False), reviewer -> users (merge,save-update; passive_deletes=False), tour_guide -> tour_guides (merge,save-update; passive_deletes=False) |
| tour_guides / TourGuide ([app/models/tour_guide.py:30](../../../app/models/tour_guide.py#L30)) | id | provider_profile_id -> provider_profiles.id (delete unspecified; update unspecified), reviewed_by -> users.id (delete unspecified; update unspecified) | None declared | ix_tour_guides_approval_status (tour_guides.approval_status), ix_tour_guides_base_district (tour_guides.base_district), ix_tour_guides_provider_profile_id (tour_guides.provider_profile_id) UNIQUE, ix_tour_guides_reviewed_by (tour_guides.reviewed_by), ix_tour_guides_verification_status (tour_guides.verification_status) | CK_tour_guides_approval_status: approval_status IN ('draft', 'pending', 'approved', 'rejected', 'suspended'), CK_tour_guides_cancellation_hours: free_cancellation_hours >= 0, CK_tour_guides_coords: (base_latitude IS NULL AND base_longitude IS NULL) OR (base_latitude IS NOT NULL AND base_longitude IS NOT NULL AND base_latitude BETWEEN -90.0 AND 90.0 AND base_longitude BETWEEN -180.0 AND 180.0), CK_tour_guides_daily_rate: daily_rate >= 0, CK_tour_guides_experience: years_of_experience >= 0, CK_tour_guides_group_size: max_group_size > 0, CK_tour_guides_hourly_rate: hourly_rate IS NULL OR hourly_rate >= 0, CK_tour_guides_license_type: license_type IS NULL OR license_type IN ('national', 'chauffeur', 'site', 'area'), CK_tour_guides_rating: rating_average BETWEEN 0.00 AND 5.00, CK_tour_guides_review_count: review_count >= 0, CK_tour_guides_verification_status: verification_status IN ('unverified', 'pending', 'verified', 'rejected', 'suspended') | availabilities -> tour_guide_availability (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=False), bookings -> tour_guide_bookings (merge,save-update; passive_deletes=False), credentials -> tour_guide_credentials (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=False), images -> tour_guide_images (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=False), languages -> tour_guide_languages (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=False), packages -> tour_guide_packages (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=False), provider_profile -> provider_profiles (merge,save-update; passive_deletes=False), reviewer -> users (merge,save-update; passive_deletes=False), service_areas -> tour_guide_service_areas (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=False), verifications -> tour_guide_verifications (merge,save-update; passive_deletes=False) |
| travel_plan_days / TravelPlanDay ([app/models/ai_planner.py:126](../../../app/models/ai_planner.py#L126)) | id | travel_plan_id -> travel_plans.id (delete CASCADE; update unspecified) | UQ_travel_plan_days (travel_plan_id, day_number) | ix_travel_plan_days_travel_plan_id (travel_plan_days.travel_plan_id) | CK_travel_plan_days_number: day_number > 0 | items -> travel_plan_items (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=False), travel_plan -> travel_plans (merge,save-update; passive_deletes=False) |
| travel_plan_items / TravelPlanItem ([app/models/ai_planner.py:153](../../../app/models/ai_planner.py#L153)) | id | listing_reference_id -> listing_references.id (delete SET NULL; update unspecified), plan_day_id -> travel_plan_days.id (delete CASCADE; update unspecified) | None declared | ix_travel_plan_items_listing_reference_id (travel_plan_items.listing_reference_id), ix_travel_plan_items_plan_day_id (travel_plan_items.plan_day_id) | CK_travel_plan_items_cost: estimated_cost IS NULL OR estimated_cost >= 0, CK_travel_plan_items_order: item_order >= 0, CK_travel_plan_items_type: item_type IN ('attraction', 'activity', 'homestay', 'restaurant', 'transit', 'guide') | plan_day -> travel_plan_days (merge,save-update; passive_deletes=False) |
| travel_plans / TravelPlan ([app/models/ai_planner.py:92](../../../app/models/ai_planner.py#L92)) | id | ai_session_id -> ai_chat_sessions.id (delete SET NULL; update unspecified), user_id -> users.id (delete unspecified; update unspecified) | unnamed (share_token) | ix_travel_plans_user_id (travel_plans.user_id) | CK_travel_plans_budget: estimated_budget IS NULL OR estimated_budget >= 0, CK_travel_plans_dates: end_date >= start_date, CK_travel_plans_days: total_days > 0 | days -> travel_plan_days (delete,delete-orphan,expunge,merge,refresh-expire,save-update; passive_deletes=False), session -> ai_chat_sessions (merge,save-update; passive_deletes=False), user -> users (merge,save-update; passive_deletes=False) |
| travel_preferences / TravelPreference ([app/models/ai_planner.py:64](../../../app/models/ai_planner.py#L64)) | id | user_id -> users.id (delete CASCADE; update unspecified) | unnamed (user_id), UQ_travel_preferences_user (user_id) | None declared | CK_travel_pref_budget: preferred_budget_level IN ('budget', 'medium', 'luxury'), CK_travel_pref_pace: pace IN ('relaxed', 'moderate', 'fast_paced'), CK_travel_pref_party: travel_party_type IN ('solo', 'couple', 'family', 'group') | user -> users (merge,save-update; passive_deletes=False) |
| users / User ([app/models/user.py:12](../../../app/models/user.py#L12)) | id | None declared | unnamed (email) | ix_users_google_sub (users.google_sub) UNIQUE | CK_users_credential_version_nonnegative: credential_version >= 0 | artisan_reservations -> artisan_reservations (merge,save-update; passive_deletes=False), password_resets -> password_resets (merge,save-update; passive_deletes=False), provider_profile -> provider_profiles (merge,save-update; passive_deletes=False) |
| wishlists / Wishlist ([app/models/shared.py:222](../../../app/models/shared.py#L222)) | id | listing_reference_id -> listing_references.id (delete CASCADE; update unspecified), user_id -> users.id (delete CASCADE; update unspecified) | UQ_wishlists_user_listing (user_id, listing_reference_id) | ix_wishlists_listing_reference_id (wishlists.listing_reference_id), ix_wishlists_user_id (wishlists.user_id) | None declared | listing_reference -> listing_references (merge,save-update; passive_deletes=False), user -> users (merge,save-update; passive_deletes=False) |

## Historical migration inventory

Raw SHA-256 over isolated checkout bytes. The second digest normalizes CRLF and CR to LF as the pinned migration runner does. DDL excerpts are source-line markers. Inspect original statements and verify the isolated catalog before relying on filters, FK actions or constraints.

| File | SHA-256 | Mapped names seen |
|---|---|---|
| [001_create_password_resets.sql](../../../migrations/001_create_password_resets.sql) | 27c437a71dfaba493c97eaee8c9518f827db439ec66553b69ff66b754b43618a | 26db215d34a136f17c25995d1acf96707c3b6a239a3f0fb096aff895c7bb9ab7 | password_resets, users |
| [002_add_google_sign_in_to_users.sql](../../../migrations/002_add_google_sign_in_to_users.sql) | 761e45740b4fab0f09820e42b88e4c3d19add45175379381d6e5fba77475a028 | ec6ae4f57b0bfab10623bcc9fb8901c8629d88bae3bf1a9b68e7c672ff39b4bf | users |
| [003_create_provider_profiles.sql](../../../migrations/003_create_provider_profiles.sql) | bd9b077644aad598a288255b74bca2b808aab4c13e6214d042ff47b6806b420a | 9fefd3f0acc775ec0c96ea31a200c4ba3dd83c0cb9f370b938272861e6568861 | provider_profiles, users |
| [004_extend_provider_registration.sql](../../../migrations/004_extend_provider_registration.sql) | 10861e95f699860947d58d2ec0aa691c0fa715bf55baa6daedc257cf7b105756 | 4fab997d24e5733e4ae2f64377b97d294fd6ca47911ed8ef1addee575744c5e2 | provider_email_otps, provider_profiles, provider_registration_authorizations |
| [005_provider_registration_drafts.sql](../../../migrations/005_provider_registration_drafts.sql) | c6462cfea74e870aacddc9493dae9c61d3621e6cd469cf3d6db7692aae3f4ea4 | 376a93980ad87e36e34dac8767d145c7d1c67f3f8eaa2ec4cadac14d3adb9955 | provider_registration_drafts |
| [006_add_personal_details_fields.sql](../../../migrations/006_add_personal_details_fields.sql) | 6901d07beeefd307373e3d851a8cad50d07f2f6d2a9273b9fb273803d1013c37 | 6d5edeca989502a3bc41819ad4f5131f879e2a3c98cb088e500fbe3860e40369 | provider_profiles, provider_registration_drafts |
| [007_create_homestay_tables.sql](../../../migrations/007_create_homestay_tables.sql) | 175de8df46cd6481dac9233dc2419984564760cfdf226d9f69a9bc7e10031bb7 | 6dd954d5f7cc9dfdab3c4fc176f22b7f1601f6e37d8b6417ae89301cb112dcbe | homestay_images, homestays |
| [008_create_homestay_amenities.sql](../../../migrations/008_create_homestay_amenities.sql) | 7d2968580388c795dfcfea49cc25d35aa2833ed4effa1da90d832a80bdd1816e | 5c6b11d4b8d294c56043d66795ea8ebeca23bba42c7351c158efacd95d5a4f1f | homestay_amenities, homestays |
| [009_add_homestay_owner.sql](../../../migrations/009_add_homestay_owner.sql) | 9b649a6c3b15784f189b054d676ee75bed273babb8f3959c0df942e922270a55 | f4df13d04bb1ea35202d705aa35cda9552be04a8baa16ad7606566303f392ee2 | homestays, users |
| [010_create_homestay_bookings.sql](../../../migrations/010_create_homestay_bookings.sql) | c03375584611df3eae49f44885cfebacfbece79bcbd0e3fd9e29a58fbf206862 | 137ae5d63984879b9c54a4b15ed51853b05e01adde4fe23ddbb9cf5da99c7ecd | homestay_bookings, homestays, users |
| [011_create_homestay_promotions.sql](../../../migrations/011_create_homestay_promotions.sql) | 37c136526345e63492d28ffbd9bf54036e15d826206933fa67dd7cfd50416f16 | d4aee872d9103c279243327d47a701b01deddcde4358cf8513aeee4bb4f1ddda | homestay_promotions, homestays |
| [012_create_artisan_listings.sql](../../../migrations/012_create_artisan_listings.sql) | 9ccb3c7c92546ae45d21947cd56b8e3de067a08c7c48d4dc8a4b709e98e9de1c | 413293f829f14c53d82e0991ae6412c77ee376597c3050308279ba30a6237936 | artisan_listings, provider_profiles |
| [013_create_showcase_items.sql](../../../migrations/013_create_showcase_items.sql) | ba961c2bfdce380c73e6430892ade2f8c42c389fb6a6146e5bdce5b32da89f54 | 42cb5b68dfcdd23c47cebb53ad1a52f43f6eccab1eeab3c19d3b3f5083850fda | artisan_listings, showcase_items |
| [014_create_artisan_promotions.sql](../../../migrations/014_create_artisan_promotions.sql) | 03b568555d7329a474168cc9d69d4e255dc5e40f4bea9854effa28dcc1ff0dc1 | 3d1b95350690dabc7a10d838ef580253eefa72769e4890a4bb44338ae09ff950 | artisan_listings, promotions, showcase_items |
| [015_add_homestay_provider_profile.sql](../../../migrations/015_add_homestay_provider_profile.sql) | 8780ccb042ebefbe60201eaa09b700948a890bd4505b00d700f867aa23c001ef | c0abeb7f8f1728e3c9e3d91bfbc2553920ee8c6c2ad0a6d44b50b9b6e53cf9b8 | homestays, provider_profiles, users |
| [016_add_rental_provider_type.sql](../../../migrations/016_add_rental_provider_type.sql) | 4bf0089963619accdb4a65c11176363dac744313c9d38e9f5913d00fa56098b6 | b7d69776c71116df137d3f5e72deb6efc584e4dd6d0dcf29b10d658835377258 | provider_profiles |
| [017_create_eco_tourism_core.sql](../../../migrations/017_create_eco_tourism_core.sql) | c44eaddd938656660e433ebc89639268d5cef6fe2877d16e9930bbba72ab3a4e | 2b4b51075db1e8c514c0e1786d66f130e08cbf3bccd33539c5de40a7e5a569da | activities, eco_evidences, eco_experience_images, eco_experiences, eco_practices, eco_safety_rules, eco_season_restrictions, eco_verifications, homestays, provider_profiles, users |
| [018_create_eco_wishlists.sql](../../../migrations/018_create_eco_wishlists.sql) | ec080d6bea6fa3ea1c0e1047f63639aadbcf386e30af90149a74d809e227ecb3 | 40df06c3c4274e8ed8bb51782e7f5d2432ac7aebaf20addd4a8ba09944df55bf | eco_experiences, eco_wishlists, users |
| [019_create_rental_tables.sql](../../../migrations/019_create_rental_tables.sql) | 185f00fc9ff79e59c07d3e7b6a73dd855b6fef938faf2e4668a13fe196a90d7a | d0e426cd469cb49e68f706d2921df607e55d291766dd68bac228efacff751e84 | provider_profiles, rental_bookings, rental_vehicles, users |
| [020_create_eco_promotions.sql](../../../migrations/020_create_eco_promotions.sql) | 558014ef41e40c3d7e2fc6e89e77b387747efb20f32c82f688cf133e1e154514 | 25dc88abd77c50abea1c5ae1ac78c0d6a30de48332f26e51dbd6ec9bd2ce95d7 | eco_experiences, eco_promotion_analytics, eco_promotion_packages, eco_sponsored_promotions, provider_profiles, users |
| [021_create_activities_module.sql](../../../migrations/021_create_activities_module.sql) | ce62ca9a49b57a391fb260d6f5d9365fec5e4ae5409e7b9fa1bd09eab82d81c2 | ee0983a717aca636af394a801582dd6fbae8b5e43f680d7cf6791c3f0a131f1a | activities, activity_bookings, activity_images, activity_slots, provider_profiles, users |
| [022_create_local_tour_guide_module.sql](../../../migrations/022_create_local_tour_guide_module.sql) | dcdef61f47bea6f1a5906e7768a8acbf398f7267c9fef9cbd4304cf3902deb76 | e58e573f2f775a299521cba474093a62c933f79a440f4fda35c48953f51f938b | provider_profiles, tour_guide_availability, tour_guide_bookings, tour_guide_credentials, tour_guide_images, tour_guide_languages, tour_guide_packages, tour_guide_service_areas, tour_guide_verifications, tour_guides, users |
| [023_extend_homestay_module.sql](../../../migrations/023_extend_homestay_module.sql) | 1d94aef4d01f17ec37437e02e77a34edefafaeb144c841c747d7fc58363c80d4 | 2bcd362f24f766291092d552e1912aeb8f376902dcd2a7c06f3718b57c3cfa46 | homestay_availability_blocks, homestay_bookings, homestays, users |
| [024_extend_artisan_module.sql](../../../migrations/024_extend_artisan_module.sql) | e34ca63cabff89089063e1647fff63e13b7d55789e3f323f9cc709a4041bcdc1 | 7f4fa7f950528839244747defb43678366e3113ac23a815a339a15e889b6a938 | artisan_business_hours, artisan_business_images, artisan_listings, artisan_product_images, artisan_reservations, showcase_items, users |
| [025_extend_activities_eco_module.sql](../../../migrations/025_extend_activities_eco_module.sql) | 2c4d5f27d2656ea56a18394fde2c3ff6c5bffeab47925071f9acbde786fffc92 | 5a8c86a5637b872099f11c29eab774e76f2213dff0dbdd040b6c5a9cd4a2ac48 | activity_booking_participants, activity_bookings, activity_slots, eco_experiences |
| [026_extend_rental_module.sql](../../../migrations/026_extend_rental_module.sql) | 843e2d0f1c19344ffdd46c79f9749c54972a4c83895e32f5e9d1e446879a8475 | 11994b44b14568f4cb70b38741b04e0af6ab88191caa0732d78dccd2f9a0e217 | provider_profiles, rental_availability_blocks, rental_bookings, rental_condition_evidence, rental_deposits, rental_eligibility_checks, rental_incidents, rental_inspections, rental_locations, rental_rate_plans, rental_vehicle_documents, rental_vehicle_images, rental_vehicles, users |
| [027_create_shared_infrastructure.sql](../../../migrations/027_create_shared_infrastructure.sql) | 6ab4f37d5f5f438487261475958015c76ffc98b72da38c075503af68afad9f53 | c739c6839222bb1d39e5d58a41001855c6878f984e551b1e8d55d785847f2dbb | admin_audit_logs, booking_references, booking_status_history, listing_references, notifications, payment_refunds, payment_transactions, promotion_campaigns, promotion_events, promotion_packages, provider_profiles, provider_settlements, reviews, users, wishlists |
| [028_create_travel_community_module.sql](../../../migrations/028_create_travel_community_module.sql) | 4fa15dc924de27d783e59442a789f632d88a88a4c12bf9e51704c2c720a94867 | 91f63ea11e7615cf369c0d7c10ab237772e5b4b6770f855c02a392e08037e140 | community_comments, community_follows, community_hashtags, community_post_hashtags, community_post_likes, community_post_media, community_posts, community_reports, community_saved_posts, users |
| [029_create_ai_travel_planner_module.sql](../../../migrations/029_create_ai_travel_planner_module.sql) | 391cca5890958029063ef34cbc98f2a8ccd1b8d5403b55e2ffda9c897c0a84e6 | 772fa37f6806ccd2ff036325a4deed977602e7a3e9fe3a97ff84aaf6b5e2a217 | ai_action_audit, ai_chat_messages, ai_chat_sessions, ai_pending_actions, listing_references, travel_plan_days, travel_plan_items, travel_plans, travel_preferences, users |
| [030_extend_homestay_final_requirements.sql](../../../migrations/030_extend_homestay_final_requirements.sql) | 6afa724589967b1195411513e7af18592416d856365515c0f4f9f59c8a04e318 | 2b9a771fce80033fba9ffa4d1a9f73d4562d2565d5ca25dcb124dd7207b5d086 | advertising_payments, homestay_bookings, homestay_promotions, listing_references, payment_transactions, promotion_campaigns, provider_profiles |
| [031_extend_promotion_booking_attribution.sql](../../../migrations/031_extend_promotion_booking_attribution.sql) | e9d6d7b3f06e3d954ff44ddb75612a06f84f0e1bcbc81f72d1804806babbcf52 | 6a6820b7d16adcb8edee22882a03857352ade62d2926f57d7152c9d5ef2c13b4 | booking_references, promotion_campaigns, promotion_events |
| [032_enforce_tour_guide_coordinate_pairs.sql](../../../migrations/032_enforce_tour_guide_coordinate_pairs.sql) | 2338a1b04caee858a8ee1f2738b83c74f2811c785919ce57d4f7ce03d3914234 | 2f3aba38ec85be241c5e37a99b23258c973315c3ad9f2cfdec2ed326a58d6539 | tour_guide_packages, tour_guides |
| [033_extend_rental_module_final_gaps.sql](../../../migrations/033_extend_rental_module_final_gaps.sql) | 517aaea10311ca9a1d4f8c277cc2364c81f8195d58d129ca4106e3c5d5bb405c | b4543894e43f92b55d3efc071648ee9a4568fb6bd189f09f03ef0437765b626f | rental_eligibility_checks, rental_inspections, rental_vehicle_documents, rental_vehicles |
| [034_create_eco_reservations.sql](../../../migrations/034_create_eco_reservations.sql) | 2c2b405fed72f4830a95f5f19e4a5f061342c05e7e89f6526bed80ed6ef35b38 | cfb7ebba1ab103b3249cc5ecff277cad018aadef84d32b1b7c2cba07899c5736 | booking_references, eco_availability_slots, eco_experiences, eco_reservations, users |
| [035_extend_tour_guide_module_final_gaps.sql](../../../migrations/035_extend_tour_guide_module_final_gaps.sql) | f3e7748ccd803472f1b1eb035552dfe5ac375f90cce15479eb83a55ab514dcd9 | 686b8d8fcb941ef05203d74fab8397eef659471873338114c48cc8995a124530 | tour_guide_bookings, tour_guide_packages, tour_guides |
| [036_align_shared_payment_status_constraints.sql](../../../migrations/036_align_shared_payment_status_constraints.sql) | d6bc3850b0fdd19dd9fedc4445f301c45a4a2c1ce3c82e3c2bbe4d7c7a600756 | 5b49686f50124829889a6d80c4be2d1aecb98f7f94bbcd6034aa91b58057ae4a | payment_refunds, payment_transactions, provider_settlements |
| [037_extend_artisan_reservation_rejection.sql](../../../migrations/037_extend_artisan_reservation_rejection.sql) | f52c90caa6ce7ccf7df88e3c2847006be1187bd2a1cddf26328a6b8910905fad | f0c37760c7fd37de72fd8ec53362f7e6ef11920c1b5323418cded8402a90ee5d | artisan_reservations, users |
| [038_create_artisan_product_favorites.sql](../../../migrations/038_create_artisan_product_favorites.sql) | 89dd3a123c1d5837ae4863271a35638ae56c2cad5da360bdb0ef5808c7162e38 | 8091bbef8dab6fe54bb1f34ae51bfd5b7a9349b275a04b8b260ce4ce03e55dd1 | artisan_product_favorites, showcase_items, users |
| [039_extend_travel_community_module.sql](../../../migrations/039_extend_travel_community_module.sql) | 05791ca69991dea29f9d00880f2c61253f519a402cbfdc22ac1de6d095e737be | 7fa522a532628f50cc90d79e622b12934d59c6727bb178b4e4ab44a876b74e11 | community_comments, community_hashtags, community_posts, community_user_profiles, listing_references, users |
| [040_add_users_credential_version.sql](../../../migrations/040_add_users_credential_version.sql) | 9ab202c3d7718a45e0bddb9f58b751042d266671ec1ff8daf5903dbb22a10489 | 75eb8a4486bbd29378e97c1bc2dd01b967ea12298fd6c9d93e80fe4f55ad02d5 | users |
| [041_extend_eco_native_safety_and_refunds.sql](../../../migrations/041_extend_eco_native_safety_and_refunds.sql) | 85cad3510a93ede800dc1eae701f08f43aecf4b9f0b6c5508db8860293f63726 | 76fd3b706f5cbb37242f31c773b7c4af2f2a34d0a6e2ca16aac672d4182892f0 | eco_age_limits, eco_availability_slots, eco_cancellation_policies, eco_cancellation_snapshots, eco_experiences, eco_refund_reconciliations, eco_reservations, eco_slot_clearances, payment_refunds, users |
| [042_create_eco_sponsorship_ledger.sql](../../../migrations/042_create_eco_sponsorship_ledger.sql) | cc9d36f897df5dd94f5a9ade1051e72a97765609dc0dc2bead8ed7681e81c424 | 2bd3ca54d8f019b4e9fdccc8883a3bac8827d72d2e98346beaabd97dada7e2b3 | booking_references, eco_promotion_analytics, eco_promotion_attributions, eco_promotion_event_receipts, eco_sponsored_promotions, eco_sponsorship_captures |
| [043_add_user_auth_version.sql](../../../migrations/043_add_user_auth_version.sql) | 7f28187b9f95560310c056dc46ca3a5f53fb6e555192370ef15e373040e66d33 | 5eb77c8f10b171e1e4627cdbf96f3faae8235b7a1ce8ffe721548db0e1973191 | users |

## Historical DDL and ledger markers

### 001_create_password_resets.sql

~~~text
10 CREATE TABLE dbo.password_resets (
18 CONSTRAINT PK_password_resets PRIMARY KEY CLUSTERED (id),
19 CONSTRAINT FK_password_resets_users FOREIGN KEY (user_id)
20 REFERENCES dbo.users (id) ON DELETE CASCADE
23 CREATE NONCLUSTERED INDEX IX_password_resets_token_hash
26 CREATE NONCLUSTERED INDEX IX_password_resets_user_id
~~~

### 002_add_google_sign_in_to_users.sql

~~~text
12 ALTER TABLE dbo.users ALTER COLUMN password_hash NVARCHAR(255) NULL;
26 ALTER TABLE dbo.users ADD google_sub NVARCHAR(255) NULL;
40 CREATE UNIQUE NONCLUSTERED INDEX UQ_users_google_sub
43 PRINT 'Created unique filtered index UQ_users_google_sub.';
~~~

### 003_create_provider_profiles.sql

~~~text
12 CREATE TABLE dbo.provider_profiles (
33 PRIMARY KEY CLUSTERED (id),
36 UNIQUE (user_id),
39 FOREIGN KEY (user_id)
40 REFERENCES dbo.users (id),
43 FOREIGN KEY (reviewed_by)
44 REFERENCES dbo.users (id),
63 CREATE NONCLUSTERED INDEX IX_provider_profiles_status
66 CREATE NONCLUSTERED INDEX IX_provider_profiles_type
~~~

### 004_extend_provider_registration.sql

~~~text
20 ALTER TABLE dbo.provider_profiles ADD nic_number NVARCHAR(20) NULL;
35 ALTER TABLE dbo.provider_profiles ADD permanent_province NVARCHAR(100) NULL;
45 ALTER TABLE dbo.provider_profiles ADD permanent_district NVARCHAR(100) NULL;
55 ALTER TABLE dbo.provider_profiles ADD permanent_city NVARCHAR(100) NULL;
65 ALTER TABLE dbo.provider_profiles ADD permanent_landmark NVARCHAR(255) NULL;
75 ALTER TABLE dbo.provider_profiles ADD permanent_postal_code NVARCHAR(20) NULL;
86 ALTER TABLE dbo.provider_profiles ADD secondary_phone NVARCHAR(25) NULL;
97 ALTER TABLE dbo.provider_profiles ADD business_province NVARCHAR(100) NULL;
107 ALTER TABLE dbo.provider_profiles ADD business_district NVARCHAR(100) NULL;
117 ALTER TABLE dbo.provider_profiles ADD business_city NVARCHAR(100) NULL;
127 ALTER TABLE dbo.provider_profiles ADD business_landmark NVARCHAR(255) NULL;
137 ALTER TABLE dbo.provider_profiles ADD business_postal_code NVARCHAR(20) NULL;
148 ALTER TABLE dbo.provider_profiles ADD business_registration_number NVARCHAR(50) NULL;
159 ALTER TABLE dbo.provider_profiles ADD selfie_image_path NVARCHAR(255) NULL;
169 ALTER TABLE dbo.provider_profiles ADD nic_front_image_path NVARCHAR(255) NULL;
179 ALTER TABLE dbo.provider_profiles ADD nic_back_image_path NVARCHAR(255) NULL;
190 CREATE NONCLUSTERED INDEX IX_provider_profiles_nic_number
207 CREATE TABLE dbo.provider_email_otps (
220 PRIMARY KEY CLUSTERED (id),
226 CREATE NONCLUSTERED INDEX IX_provider_email_otps_email_purpose
229 CREATE NONCLUSTERED INDEX IX_provider_email_otps_expires_at
250 CREATE TABLE dbo.provider_registration_authorizations (
263 PRIMARY KEY CLUSTERED (id),
266 UNIQUE (token_hash),
272 CREATE NONCLUSTERED INDEX IX_provider_reg_auth_email_purpose
275 CREATE NONCLUSTERED INDEX IX_provider_reg_auth_expires_at
~~~

### 005_provider_registration_drafts.sql

~~~text
18 CREATE TABLE dbo.provider_registration_drafts (
46 CONSTRAINT PK_provider_registration_drafts PRIMARY KEY CLUSTERED (id ASC),
47 CONSTRAINT UQ_provider_registration_drafts_email UNIQUE NONCLUSTERED (email ASC)
68 CREATE NONCLUSTERED INDEX IX_provider_registration_drafts_nic_number
86 CREATE NONCLUSTERED INDEX IX_provider_registration_drafts_provider_category
~~~

### 006_add_personal_details_fields.sql

~~~text
27 ALTER TABLE dbo.provider_registration_drafts ADD date_of_birth DATE NULL;
41 ALTER TABLE dbo.provider_registration_drafts ADD gender NVARCHAR(20) NULL;
55 ALTER TABLE dbo.provider_registration_drafts ADD nationality NVARCHAR(50) NULL;
69 ALTER TABLE dbo.provider_registration_drafts ADD identity_document_type NVARCHAR(20) NOT NULL
86 ALTER TABLE dbo.provider_registration_drafts
108 ALTER TABLE dbo.provider_profiles ADD date_of_birth DATE NULL;
122 ALTER TABLE dbo.provider_profiles ADD gender NVARCHAR(20) NULL;
136 ALTER TABLE dbo.provider_profiles ADD nationality NVARCHAR(50) NULL;
150 ALTER TABLE dbo.provider_profiles ADD identity_document_type NVARCHAR(20) NOT NULL
167 ALTER TABLE dbo.provider_profiles
191 CREATE NONCLUSTERED INDEX IX_provider_registration_drafts_doc_lookup
212 CREATE UNIQUE NONCLUSTERED INDEX UQ_provider_profiles_doc_type_number
216 PRINT 'Created UNIQUE filtered index UQ_provider_profiles_doc_type_number on dbo.provider_profiles.';
~~~

### 007_create_homestay_tables.sql

~~~text
8 CREATE TABLE dbo.homestays (
18 CONSTRAINT PK_homestays PRIMARY KEY (id),
35 CREATE TABLE dbo.homestay_images (
41 CONSTRAINT PK_homestay_images PRIMARY KEY (id),
43 FOREIGN KEY (homestay_id)
44 REFERENCES dbo.homestays(id)
45 ON DELETE CASCADE
48 CREATE INDEX IX_homestay_images_homestay_id
~~~

### 008_create_homestay_amenities.sql

~~~text
6 CREATE TABLE dbo.homestay_amenities (
8 PRIMARY KEY,
15 FOREIGN KEY (homestay_id)
16 REFERENCES dbo.homestays(id)
17 ON DELETE CASCADE,
20 UNIQUE (homestay_id, name)
23 CREATE INDEX IX_homestay_amenities_homestay_id
~~~

### 009_add_homestay_owner.sql

~~~text
7 ALTER TABLE dbo.homestays
18 ALTER TABLE dbo.homestays
20 FOREIGN KEY (owner_id)
21 REFERENCES dbo.users(id);
31 CREATE INDEX IX_homestays_owner_id
~~~

### 010_create_homestay_bookings.sql

~~~text
8 CREATE TABLE dbo.homestay_bookings (
22 PRIMARY KEY (id),
25 FOREIGN KEY (homestay_id)
26 REFERENCES dbo.homestays(id),
29 FOREIGN KEY (user_id)
30 REFERENCES dbo.users(id),
48 CREATE INDEX IX_homestay_bookings_homestay_id
51 CREATE INDEX IX_homestay_bookings_user_id
~~~

### 011_create_homestay_promotions.sql

~~~text
10 CREATE TABLE dbo.homestay_promotions
35 PRIMARY KEY (id),
38 FOREIGN KEY (homestay_id)
39 REFERENCES dbo.homestays(id),
61 CREATE INDEX IX_homestay_promotions_homestay_id
~~~

### 012_create_artisan_listings.sql

~~~text
12 CREATE TABLE dbo.artisan_listings (
41 PRIMARY KEY CLUSTERED (id),
44 FOREIGN KEY (provider_profile_id)
45 REFERENCES dbo.provider_profiles (id)
46 ON DELETE NO ACTION,
56 CREATE NONCLUSTERED INDEX IX_artisan_listings_provider_profile_id
60 CREATE NONCLUSTERED INDEX IX_artisan_listings_district
63 CREATE NONCLUSTERED INDEX IX_artisan_listings_craft_category
67 CREATE NONCLUSTERED INDEX IX_artisan_listings_active_featured
~~~

### 013_create_showcase_items.sql

~~~text
12 CREATE TABLE dbo.showcase_items (
35 PRIMARY KEY CLUSTERED (id),
38 FOREIGN KEY (artisan_listing_id)
39 REFERENCES dbo.artisan_listings (id)
40 ON DELETE NO ACTION,
53 CREATE NONCLUSTERED INDEX IX_showcase_items_artisan_listing_id
57 CREATE NONCLUSTERED INDEX IX_showcase_items_type
61 CREATE NONCLUSTERED INDEX IX_showcase_items_discovery
~~~

### 014_create_artisan_promotions.sql

~~~text
12 CREATE TABLE dbo.promotions (
33 PRIMARY KEY CLUSTERED (id),
36 FOREIGN KEY (artisan_listing_id)
37 REFERENCES dbo.artisan_listings (id)
38 ON DELETE NO ACTION,
41 FOREIGN KEY (showcase_item_id)
42 REFERENCES dbo.showcase_items (id)
43 ON DELETE NO ACTION,
60 CREATE NONCLUSTERED INDEX IX_promotions_artisan_listing_id
64 CREATE NONCLUSTERED INDEX IX_promotions_showcase_item_id
69 CREATE NONCLUSTERED INDEX IX_promotions_active_dates
~~~

### 015_add_homestay_provider_profile.sql

~~~text
20 - DDL statements (ALTER TABLE, ADD CONSTRAINT, CREATE INDEX) and data backfills
29 THROW;
36 CONSTRAINT UQ_provider_profiles_user_id UNIQUE (user_id)
38 - This database-enforced unique constraint guarantees that at most ONE provider
49 ALTER TABLE dbo.homestays
72 ALTER TABLE dbo.homestays
74 FOREIGN KEY (provider_profile_id)
75 REFERENCES dbo.provider_profiles (id)
76 ON DELETE NO ACTION;
78 PRINT 'Foreign key FK_homestays_provider_profile created with ON DELETE NO ACTION.';
82 PRINT 'Foreign key FK_homestays_provider_profile already exists.';
96 CREATE NONCLUSTERED INDEX IX_homestays_provider_profile_id
131 RAISERROR('Pre-condition failed: dbo.provider_profiles must have a UNIQUE constraint on user_id.', 16, 1);
~~~

### 016_add_rental_provider_type.sql

~~~text
7 THROW 50001, 'provider_profiles table does not exist.', 1;
18 ALTER TABLE dbo.provider_profiles
19 DROP CONSTRAINT CK_provider_profiles_type;
23 ALTER TABLE dbo.provider_profiles
~~~

### 017_create_eco_tourism_core.sql

~~~text
26 CREATE TABLE dbo.eco_experiences (
91 PRIMARY KEY CLUSTERED (id),
94 FOREIGN KEY (provider_profile_id)
95 REFERENCES dbo.provider_profiles (id)
96 ON DELETE NO ACTION,
99 FOREIGN KEY (linked_homestay_id)
100 REFERENCES dbo.homestays (id)
101 ON DELETE NO ACTION,
104 FOREIGN KEY (approval_reviewed_by)
105 REFERENCES dbo.users (id)
106 ON DELETE NO ACTION,
164 CREATE NONCLUSTERED INDEX IX_eco_experiences_provider_profile_id
167 CREATE NONCLUSTERED INDEX IX_eco_experiences_linked_homestay_id
171 CREATE NONCLUSTERED INDEX IX_eco_experiences_discovery
174 CREATE NONCLUSTERED INDEX IX_eco_experiences_badge_status
192 CREATE TABLE dbo.eco_experience_images (
205 PRIMARY KEY CLUSTERED (id),
208 FOREIGN KEY (eco_experience_id)
209 REFERENCES dbo.eco_experiences (id)
210 ON DELETE CASCADE,
216 CREATE NONCLUSTERED INDEX IX_eco_experience_images_eco_experience_id
234 CREATE TABLE dbo.eco_safety_rules (
246 PRIMARY KEY CLUSTERED (id),
249 FOREIGN KEY (eco_experience_id)
250 REFERENCES dbo.eco_experiences (id)
251 ON DELETE CASCADE,
263 CREATE NONCLUSTERED INDEX IX_eco_safety_rules_eco_experience_id
281 CREATE TABLE dbo.eco_season_restrictions (
295 PRIMARY KEY CLUSTERED (id),
298 FOREIGN KEY (eco_experience_id)
299 REFERENCES dbo.eco_experiences (id)
300 ON DELETE CASCADE,
303 FOREIGN KEY (created_by)
304 REFERENCES dbo.users (id)
305 ON DELETE NO ACTION,
319 CREATE NONCLUSTERED INDEX IX_eco_season_restrictions_experience_dates
337 CREATE TABLE dbo.eco_practices (
348 PRIMARY KEY CLUSTERED (id),
351 FOREIGN KEY (eco_experience_id)
352 REFERENCES dbo.eco_experiences (id)
353 ON DELETE CASCADE,
366 CREATE NONCLUSTERED INDEX IX_eco_practices_eco_experience_id
384 CREATE TABLE dbo.eco_evidences (
394 PRIMARY KEY CLUSTERED (id),
397 FOREIGN KEY (eco_practice_id)
398 REFERENCES dbo.eco_practices (id)
399 ON DELETE CASCADE,
410 CREATE NONCLUSTERED INDEX IX_eco_evidences_eco_practice_id
428 CREATE TABLE dbo.eco_verifications (
441 PRIMARY KEY CLUSTERED (id),
444 FOREIGN KEY (eco_experience_id)
445 REFERENCES dbo.eco_experiences (id)
446 ON DELETE CASCADE,
449 FOREIGN KEY (submitted_by_provider_id)
450 REFERENCES dbo.provider_profiles (id)
451 ON DELETE NO ACTION,
454 FOREIGN KEY (reviewed_by_admin_id)
455 REFERENCES dbo.users (id)
456 ON DELETE NO ACTION,
467 CREATE NONCLUSTERED INDEX IX_eco_verifications_experience_created
~~~

### 018_create_eco_wishlists.sql

~~~text
12 CREATE TABLE dbo.eco_wishlists (
20 PRIMARY KEY CLUSTERED (id),
23 FOREIGN KEY (user_id)
24 REFERENCES dbo.users (id)
25 ON DELETE CASCADE,
28 FOREIGN KEY (eco_experience_id)
29 REFERENCES dbo.eco_experiences (id)
30 ON DELETE CASCADE,
33 UNIQUE NONCLUSTERED (user_id, eco_experience_id)
36 CREATE NONCLUSTERED INDEX IX_eco_wishlists_user_created
40 CREATE NONCLUSTERED INDEX IX_eco_wishlists_experience_id
~~~

### 019_create_rental_tables.sql

~~~text
7 CREATE TABLE dbo.rental_vehicles (
8 id INT IDENTITY(1,1) PRIMARY KEY,
19 FOREIGN KEY (provider_id)
20 REFERENCES dbo.provider_profiles(id),
29 CREATE INDEX IX_rental_vehicles_provider_id
42 CREATE TABLE dbo.rental_bookings (
43 id INT IDENTITY(1,1) PRIMARY KEY,
54 FOREIGN KEY (vehicle_id)
55 REFERENCES dbo.rental_vehicles(id),
58 FOREIGN KEY (user_id)
59 REFERENCES dbo.users(id),
68 CREATE INDEX IX_rental_bookings_vehicle_id
71 CREATE INDEX IX_rental_bookings_user_id
~~~

### 020_create_eco_promotions.sql

~~~text
25 CREATE TABLE dbo.eco_promotion_packages (
37 CONSTRAINT PK_eco_promotion_packages PRIMARY KEY CLUSTERED (id),
38 CONSTRAINT UQ_eco_promotion_packages_name UNIQUE NONCLUSTERED (name),
44 CREATE NONCLUSTERED INDEX IX_eco_promotion_packages_active
87 CREATE TABLE dbo.eco_sponsored_promotions (
116 CONSTRAINT PK_eco_sponsored_promotions PRIMARY KEY CLUSTERED (id),
117 CONSTRAINT FK_eco_spon_experience FOREIGN KEY (eco_experience_id)
118 REFERENCES dbo.eco_experiences (id) ON DELETE CASCADE,
119 CONSTRAINT FK_eco_spon_provider FOREIGN KEY (provider_profile_id)
120 REFERENCES dbo.provider_profiles (id) ON DELETE NO ACTION,
121 CONSTRAINT FK_eco_spon_package FOREIGN KEY (package_id)
122 REFERENCES dbo.eco_promotion_packages (id) ON DELETE NO ACTION,
123 CONSTRAINT FK_eco_spon_reviewer FOREIGN KEY (reviewed_by)
124 REFERENCES dbo.users (id) ON DELETE SET NULL,
142 CREATE NONCLUSTERED INDEX IX_eco_sponsored_active_window
146 CREATE NONCLUSTERED INDEX IX_eco_sponsored_experience_status
149 CREATE NONCLUSTERED INDEX IX_eco_sponsored_provider
167 CREATE TABLE dbo.eco_promotion_analytics (
177 CONSTRAINT PK_eco_promotion_analytics PRIMARY KEY CLUSTERED (id),
178 CONSTRAINT FK_eco_analytics_promotion FOREIGN KEY (promotion_id)
179 REFERENCES dbo.eco_sponsored_promotions (id) ON DELETE CASCADE,
180 CONSTRAINT FK_eco_analytics_experience FOREIGN KEY (eco_experience_id)
181 REFERENCES dbo.eco_experiences (id) ON DELETE NO ACTION,
182 CONSTRAINT UQ_eco_promotion_analytics UNIQUE NONCLUSTERED (promotion_id, metric_date),
186 CREATE NONCLUSTERED INDEX IX_eco_promotion_analytics_experience
~~~

### 021_create_activities_module.sql

~~~text
11 CREATE TABLE dbo.activities (
47 CONSTRAINT PK_activities PRIMARY KEY (id),
50 FOREIGN KEY (provider_profile_id)
51 REFERENCES dbo.provider_profiles(id),
54 FOREIGN KEY (reviewed_by)
55 REFERENCES dbo.users(id),
93 CREATE INDEX IX_activities_provider
96 CREATE INDEX IX_activities_discovery
108 CREATE TABLE dbo.activity_images (
116 CONSTRAINT PK_activity_images PRIMARY KEY (id),
119 FOREIGN KEY (activity_id)
120 REFERENCES dbo.activities(id),
126 CREATE INDEX IX_activity_images_activity
138 CREATE TABLE dbo.activity_slots (
152 CONSTRAINT PK_activity_slots PRIMARY KEY (id),
155 FOREIGN KEY (activity_id)
156 REFERENCES dbo.activities(id),
168 CREATE INDEX IX_activity_slots_activity_time
180 CREATE TABLE dbo.activity_bookings (
200 CONSTRAINT PK_activity_bookings PRIMARY KEY (id),
203 FOREIGN KEY (activity_slot_id)
204 REFERENCES dbo.activity_slots(id),
207 FOREIGN KEY (user_id)
208 REFERENCES dbo.users(id),
239 CREATE INDEX IX_activity_bookings_slot
242 CREATE INDEX IX_activity_bookings_user
245 CREATE INDEX IX_activity_bookings_status
~~~

### 022_create_local_tour_guide_module.sql

~~~text
12 CREATE TABLE dbo.tour_guides (
62 CONSTRAINT PK_tour_guides PRIMARY KEY (id),
65 UNIQUE (provider_profile_id),
68 FOREIGN KEY (provider_profile_id)
69 REFERENCES dbo.provider_profiles(id)
70 ON DELETE NO ACTION,
73 FOREIGN KEY (reviewed_by)
74 REFERENCES dbo.users(id)
75 ON DELETE NO ACTION,
132 CREATE INDEX IX_tour_guides_provider
135 CREATE INDEX IX_tour_guides_reviewer
138 CREATE INDEX IX_tour_guides_discovery
154 CREATE TABLE dbo.tour_guide_languages (
163 CONSTRAINT PK_tour_guide_languages PRIMARY KEY (id),
166 FOREIGN KEY (tour_guide_id)
167 REFERENCES dbo.tour_guides(id)
168 ON DELETE CASCADE,
171 UNIQUE (tour_guide_id, language_code),
182 CREATE INDEX IX_tour_guide_languages_guide
198 CREATE TABLE dbo.tour_guide_service_areas (
207 CONSTRAINT PK_tour_guide_service_areas PRIMARY KEY (id),
210 FOREIGN KEY (tour_guide_id)
211 REFERENCES dbo.tour_guides(id)
212 ON DELETE CASCADE,
215 UNIQUE (tour_guide_id, district, city)
218 CREATE INDEX IX_tour_guide_service_areas_guide
221 CREATE INDEX IX_tour_guide_service_areas_district
237 CREATE TABLE dbo.tour_guide_credentials (
255 CONSTRAINT PK_tour_guide_credentials PRIMARY KEY (id),
258 FOREIGN KEY (tour_guide_id)
259 REFERENCES dbo.tour_guides(id)
260 ON DELETE CASCADE,
264 UNIQUE (id, tour_guide_id),
287 CREATE INDEX IX_tour_guide_credentials_guide
303 CREATE TABLE dbo.tour_guide_images (
312 CONSTRAINT PK_tour_guide_images PRIMARY KEY (id),
315 FOREIGN KEY (tour_guide_id)
316 REFERENCES dbo.tour_guides(id)
317 ON DELETE CASCADE,
323 CREATE INDEX IX_tour_guide_images_guide
327 CREATE UNIQUE NONCLUSTERED INDEX UQ_tour_guide_images_profile
344 CREATE TABLE dbo.tour_guide_packages (
370 CONSTRAINT PK_tour_guide_packages PRIMARY KEY (id),
373 FOREIGN KEY (tour_guide_id)
374 REFERENCES dbo.tour_guides(id)
375 ON DELETE CASCADE,
379 UNIQUE (id, tour_guide_id),
401 CREATE INDEX IX_tour_guide_packages_guide
417 CREATE TABLE dbo.tour_guide_availability (
427 CONSTRAINT PK_tour_guide_availability PRIMARY KEY (id),
430 FOREIGN KEY (tour_guide_id)
431 REFERENCES dbo.tour_guides(id)
432 ON DELETE CASCADE,
435 UNIQUE (tour_guide_id, available_date, start_time, end_time),
446 CREATE INDEX IX_tour_guide_availability_guide
462 CREATE TABLE dbo.tour_guide_bookings (
499 CONSTRAINT PK_tour_guide_bookings PRIMARY KEY (id),
502 FOREIGN KEY (tour_guide_id)
503 REFERENCES dbo.tour_guides(id)
504 ON DELETE NO ACTION,
507 FOREIGN KEY (traveler_id)
508 REFERENCES dbo.users(id)
509 ON DELETE NO ACTION,
512 FOREIGN KEY (cancelled_by)
513 REFERENCES dbo.users(id)
514 ON DELETE NO ACTION,
518 FOREIGN KEY (package_id, tour_guide_id)
519 REFERENCES dbo.tour_guide_packages(id, tour_guide_id)
520 ON DELETE NO ACTION,
562 CREATE INDEX IX_tour_guide_bookings_guide
565 CREATE INDEX IX_tour_guide_bookings_traveler
568 CREATE INDEX IX_tour_guide_bookings_package
571 CREATE INDEX IX_tour_guide_bookings_status
574 CREATE INDEX IX_tour_guide_bookings_expires
591 CREATE TABLE dbo.tour_guide_verifications (
605 CONSTRAINT PK_tour_guide_verifications PRIMARY KEY (id),
608 FOREIGN KEY (tour_guide_id)
609 REFERENCES dbo.tour_guides(id)
610 ON DELETE NO ACTION,
613 FOREIGN KEY (reviewed_by)
614 REFERENCES dbo.users(id)
615 ON DELETE NO ACTION,
619 FOREIGN KEY (credential_id, tour_guide_id)
620 REFERENCES dbo.tour_guide_credentials(id, tour_guide_id)
621 ON DELETE NO ACTION,
631 CREATE INDEX IX_tour_guide_verifications_guide
634 CREATE INDEX IX_tour_guide_verifications_credential
637 CREATE INDEX IX_tour_guide_verifications_reviewer
~~~

### 023_extend_homestay_module.sql

~~~text
12 ALTER TABLE dbo.homestays
18 ALTER TABLE dbo.homestays
24 ALTER TABLE dbo.homestays
30 ALTER TABLE dbo.homestays
36 ALTER TABLE dbo.homestays
42 ALTER TABLE dbo.homestays
48 ALTER TABLE dbo.homestays
55 ALTER TABLE dbo.homestays
62 ALTER TABLE dbo.homestays
69 ALTER TABLE dbo.homestays
76 ALTER TABLE dbo.homestays
82 ALTER TABLE dbo.homestays
88 ALTER TABLE dbo.homestays
94 ALTER TABLE dbo.homestays
100 ALTER TABLE dbo.homestays
107 ALTER TABLE dbo.homestays
122 ALTER TABLE dbo.homestays
124 FOREIGN KEY (reviewed_by)
125 REFERENCES dbo.users(id)
126 ON DELETE NO ACTION;
135 ALTER TABLE dbo.homestays
150 ALTER TABLE dbo.homestays
167 ALTER TABLE dbo.homestays
184 ALTER TABLE dbo.homestays
195 CREATE NONCLUSTERED INDEX IX_homestays_reviewer
206 CREATE NONCLUSTERED INDEX IX_homestays_district
216 CREATE NONCLUSTERED INDEX IX_homestays_discovery
226 ALTER TABLE dbo.homestay_bookings
233 ALTER TABLE dbo.homestay_bookings
240 ALTER TABLE dbo.homestay_bookings
247 ALTER TABLE dbo.homestay_bookings
253 ALTER TABLE dbo.homestay_bookings
259 ALTER TABLE dbo.homestay_bookings
265 ALTER TABLE dbo.homestay_bookings
271 ALTER TABLE dbo.homestay_bookings
277 ALTER TABLE dbo.homestay_bookings
283 ALTER TABLE dbo.homestay_bookings
290 ALTER TABLE dbo.homestay_bookings
306 ALTER TABLE dbo.homestay_bookings
307 DROP CONSTRAINT CK_bookings_status;
310 ALTER TABLE dbo.homestay_bookings
328 ALTER TABLE dbo.homestay_bookings
339 ALTER TABLE dbo.homestay_bookings
350 ALTER TABLE dbo.homestay_bookings
361 ALTER TABLE dbo.homestay_bookings
363 FOREIGN KEY (cancelled_by)
364 REFERENCES dbo.users(id)
365 ON DELETE NO ACTION;
374 CREATE NONCLUSTERED INDEX IX_homestay_bookings_status
384 CREATE NONCLUSTERED INDEX IX_homestay_bookings_dates
394 CREATE NONCLUSTERED INDEX IX_homestay_bookings_expires
405 CREATE TABLE dbo.homestay_availability_blocks (
417 CONSTRAINT PK_homestay_availability_blocks PRIMARY KEY (id),
420 FOREIGN KEY (homestay_id)
421 REFERENCES dbo.homestays(id)
422 ON DELETE CASCADE,
425 FOREIGN KEY (created_by)
426 REFERENCES dbo.users(id)
427 ON DELETE NO ACTION,
443 CREATE NONCLUSTERED INDEX IX_homestay_availability_blocks_homestay
446 CREATE NONCLUSTERED INDEX IX_homestay_availability_blocks_active
~~~

### 024_extend_artisan_module.sql

~~~text
12 ALTER TABLE dbo.artisan_listings
19 ALTER TABLE dbo.artisan_listings
25 ALTER TABLE dbo.artisan_listings
31 ALTER TABLE dbo.artisan_listings
37 ALTER TABLE dbo.artisan_listings
52 ALTER TABLE dbo.artisan_listings
54 FOREIGN KEY (reviewed_by)
55 REFERENCES dbo.users(id)
56 ON DELETE NO ACTION;
65 ALTER TABLE dbo.artisan_listings
76 ALTER TABLE dbo.artisan_listings
87 CREATE NONCLUSTERED INDEX IX_artisan_listings_discovery
97 ALTER TABLE dbo.showcase_items
104 ALTER TABLE dbo.showcase_items
110 ALTER TABLE dbo.showcase_items
117 ALTER TABLE dbo.showcase_items
132 ALTER TABLE dbo.showcase_items
143 ALTER TABLE dbo.showcase_items
154 CREATE TABLE dbo.artisan_business_images (
163 CONSTRAINT PK_artisan_business_images PRIMARY KEY (id),
166 FOREIGN KEY (artisan_listing_id)
167 REFERENCES dbo.artisan_listings(id)
168 ON DELETE CASCADE,
174 CREATE NONCLUSTERED INDEX IX_artisan_business_images_listing
190 CREATE TABLE dbo.artisan_product_images (
199 CONSTRAINT PK_artisan_product_images PRIMARY KEY (id),
202 FOREIGN KEY (showcase_item_id)
203 REFERENCES dbo.showcase_items(id)
204 ON DELETE CASCADE,
210 CREATE NONCLUSTERED INDEX IX_artisan_product_images_item
226 CREATE TABLE dbo.artisan_reservations (
243 CONSTRAINT PK_artisan_reservations PRIMARY KEY (id),
246 FOREIGN KEY (showcase_item_id)
247 REFERENCES dbo.showcase_items(id)
248 ON DELETE NO ACTION,
251 FOREIGN KEY (artisan_listing_id)
252 REFERENCES dbo.artisan_listings(id)
253 ON DELETE NO ACTION,
256 FOREIGN KEY (traveler_id)
257 REFERENCES dbo.users(id)
258 ON DELETE NO ACTION,
261 FOREIGN KEY (cancelled_by)
262 REFERENCES dbo.users(id)
263 ON DELETE NO ACTION,
275 CREATE NONCLUSTERED INDEX IX_artisan_reservations_listing
278 CREATE NONCLUSTERED INDEX IX_artisan_reservations_traveler
281 CREATE NONCLUSTERED INDEX IX_artisan_reservations_status
297 CREATE TABLE dbo.artisan_business_hours (
306 CONSTRAINT PK_artisan_business_hours PRIMARY KEY (id),
309 FOREIGN KEY (artisan_listing_id)
310 REFERENCES dbo.artisan_listings(id)
311 ON DELETE CASCADE,
314 UNIQUE (artisan_listing_id, day_of_week),
323 CREATE NONCLUSTERED INDEX IX_artisan_business_hours_listing
~~~

### 025_extend_activities_eco_module.sql

~~~text
12 ALTER TABLE dbo.activity_slots
26 ALTER TABLE dbo.activity_slots
28 FOREIGN KEY (eco_experience_id)
29 REFERENCES dbo.eco_experiences(id)
30 ON DELETE NO ACTION;
39 CREATE NONCLUSTERED INDEX IX_activity_slots_eco_experience
50 ALTER TABLE dbo.activity_bookings
64 ALTER TABLE dbo.activity_bookings
66 FOREIGN KEY (eco_experience_id)
67 REFERENCES dbo.eco_experiences(id)
68 ON DELETE NO ACTION;
77 CREATE NONCLUSTERED INDEX IX_activity_bookings_eco_experience
88 CREATE TABLE dbo.activity_booking_participants (
99 CONSTRAINT PK_activity_booking_participants PRIMARY KEY (id),
102 FOREIGN KEY (activity_booking_id)
103 REFERENCES dbo.activity_bookings(id)
104 ON DELETE CASCADE,
110 CREATE NONCLUSTERED INDEX IX_activity_booking_participants_booking
~~~

### 026_extend_rental_module.sql

~~~text
12 CREATE TABLE dbo.rental_locations (
24 CONSTRAINT PK_rental_locations PRIMARY KEY (id),
27 FOREIGN KEY (provider_id)
28 REFERENCES dbo.provider_profiles(id)
29 ON DELETE NO ACTION,
39 CREATE NONCLUSTERED INDEX IX_rental_locations_district
55 ALTER TABLE dbo.rental_vehicles
61 ALTER TABLE dbo.rental_vehicles
67 ALTER TABLE dbo.rental_vehicles
74 ALTER TABLE dbo.rental_vehicles
81 ALTER TABLE dbo.rental_vehicles
88 ALTER TABLE dbo.rental_vehicles
94 ALTER TABLE dbo.rental_vehicles
100 ALTER TABLE dbo.rental_vehicles
106 ALTER TABLE dbo.rental_vehicles
113 ALTER TABLE dbo.rental_vehicles
120 ALTER TABLE dbo.rental_vehicles
126 ALTER TABLE dbo.rental_vehicles
132 ALTER TABLE dbo.rental_vehicles
138 ALTER TABLE dbo.rental_vehicles
144 ALTER TABLE dbo.rental_vehicles
151 ALTER TABLE dbo.rental_vehicles
166 ALTER TABLE dbo.rental_vehicles
168 FOREIGN KEY (location_id)
169 REFERENCES dbo.rental_locations(id)
170 ON DELETE NO ACTION;
179 ALTER TABLE dbo.rental_vehicles
181 FOREIGN KEY (reviewed_by)
182 REFERENCES dbo.users(id)
183 ON DELETE NO ACTION;
192 ALTER TABLE dbo.rental_vehicles
203 ALTER TABLE dbo.rental_vehicles
214 ALTER TABLE dbo.rental_vehicles
225 ALTER TABLE dbo.rental_vehicles
236 CREATE NONCLUSTERED INDEX IX_rental_vehicles_discovery
246 CREATE TABLE dbo.rental_rate_plans (
258 CONSTRAINT PK_rental_rate_plans PRIMARY KEY (id),
261 FOREIGN KEY (vehicle_id)
262 REFERENCES dbo.rental_vehicles(id)
263 ON DELETE CASCADE,
275 CREATE NONCLUSTERED INDEX IX_rental_rate_plans_vehicle
283 CREATE TABLE dbo.rental_vehicle_images (
292 CONSTRAINT PK_rental_vehicle_images PRIMARY KEY (id),
295 FOREIGN KEY (vehicle_id)
296 REFERENCES dbo.rental_vehicles(id)
297 ON DELETE CASCADE,
303 CREATE NONCLUSTERED INDEX IX_rental_vehicle_images_vehicle
311 CREATE TABLE dbo.rental_vehicle_documents (
321 CONSTRAINT PK_rental_vehicle_documents PRIMARY KEY (id),
324 FOREIGN KEY (vehicle_id)
325 REFERENCES dbo.rental_vehicles(id)
326 ON DELETE CASCADE,
332 CREATE NONCLUSTERED INDEX IX_rental_vehicle_documents_vehicle
340 CREATE TABLE dbo.rental_availability_blocks (
350 CONSTRAINT PK_rental_availability_blocks PRIMARY KEY (id),
353 FOREIGN KEY (vehicle_id)
354 REFERENCES dbo.rental_vehicles(id)
355 ON DELETE CASCADE,
364 CREATE NONCLUSTERED INDEX IX_rental_availability_blocks_vehicle
376 ALTER TABLE dbo.rental_bookings
382 ALTER TABLE dbo.rental_bookings
388 ALTER TABLE dbo.rental_bookings
395 ALTER TABLE dbo.rental_bookings
402 ALTER TABLE dbo.rental_bookings
409 ALTER TABLE dbo.rental_bookings
415 ALTER TABLE dbo.rental_bookings
421 ALTER TABLE dbo.rental_bookings
427 ALTER TABLE dbo.rental_bookings
433 ALTER TABLE dbo.rental_bookings
439 ALTER TABLE dbo.rental_bookings
445 ALTER TABLE dbo.rental_bookings
452 ALTER TABLE dbo.rental_bookings
467 ALTER TABLE dbo.rental_bookings
469 FOREIGN KEY (pickup_location_id)
470 REFERENCES dbo.rental_locations(id)
471 ON DELETE NO ACTION;
480 ALTER TABLE dbo.rental_bookings
482 FOREIGN KEY (return_location_id)
483 REFERENCES dbo.rental_locations(id)
484 ON DELETE NO ACTION;
493 ALTER TABLE dbo.rental_bookings
495 FOREIGN KEY (cancelled_by)
496 REFERENCES dbo.users(id)
497 ON DELETE NO ACTION;
506 CREATE NONCLUSTERED INDEX IX_rental_bookings_dates
516 CREATE TABLE dbo.rental_eligibility_checks (
531 CONSTRAINT PK_rental_eligibility_checks PRIMARY KEY (id),
534 FOREIGN KEY (user_id)
535 REFERENCES dbo.users(id)
536 ON DELETE NO ACTION,
539 FOREIGN KEY (reviewed_by)
540 REFERENCES dbo.users(id)
541 ON DELETE NO ACTION,
547 CREATE NONCLUSTERED INDEX IX_rental_eligibility_user
555 CREATE TABLE dbo.rental_inspections (
567 CONSTRAINT PK_rental_inspections PRIMARY KEY (id),
570 FOREIGN KEY (booking_id)
571 REFERENCES dbo.rental_bookings(id)
572 ON DELETE CASCADE,
575 FOREIGN KEY (inspected_by)
576 REFERENCES dbo.users(id)
577 ON DELETE NO ACTION,
589 CREATE NONCLUSTERED INDEX IX_rental_inspections_booking
597 CREATE TABLE dbo.rental_condition_evidence (
606 CONSTRAINT PK_rental_condition_evidence PRIMARY KEY (id),
609 FOREIGN KEY (inspection_id)
610 REFERENCES dbo.rental_inspections(id)
611 ON DELETE CASCADE,
617 CREATE NONCLUSTERED INDEX IX_rental_condition_evidence_inspection
625 CREATE TABLE dbo.rental_incidents (
638 CONSTRAINT PK_rental_incidents PRIMARY KEY (id),
641 FOREIGN KEY (booking_id)
642 REFERENCES dbo.rental_bookings(id)
643 ON DELETE CASCADE,
658 CREATE NONCLUSTERED INDEX IX_rental_incidents_booking
666 CREATE TABLE dbo.rental_deposits (
678 CONSTRAINT PK_rental_deposits PRIMARY KEY (id),
681 FOREIGN KEY (booking_id)
682 REFERENCES dbo.rental_bookings(id)
683 ON DELETE CASCADE,
698 CREATE NONCLUSTERED INDEX IX_rental_deposits_booking
~~~

### 027_create_shared_infrastructure.sql

~~~text
12 CREATE TABLE dbo.listing_references (
25 CONSTRAINT PK_listing_references PRIMARY KEY (id),
28 FOREIGN KEY (provider_profile_id)
29 REFERENCES dbo.provider_profiles(id)
30 ON DELETE NO ACTION,
33 UNIQUE (module_type, target_id),
39 CREATE NONCLUSTERED INDEX IX_listing_references_provider
42 CREATE NONCLUSTERED INDEX IX_listing_references_location
50 CREATE TABLE dbo.booking_references (
63 CONSTRAINT PK_booking_references PRIMARY KEY (id),
66 UNIQUE (booking_reference),
69 UNIQUE (module_type, target_booking_id),
72 FOREIGN KEY (user_id)
73 REFERENCES dbo.users(id)
74 ON DELETE NO ACTION,
77 FOREIGN KEY (provider_profile_id)
78 REFERENCES dbo.provider_profiles(id)
79 ON DELETE NO ACTION,
88 CREATE NONCLUSTERED INDEX IX_booking_references_user
91 CREATE NONCLUSTERED INDEX IX_booking_references_provider
103 CREATE TABLE dbo.payment_transactions (
117 CONSTRAINT PK_payment_transactions PRIMARY KEY (id),
120 FOREIGN KEY (booking_reference_id)
121 REFERENCES dbo.booking_references(id)
122 ON DELETE NO ACTION,
125 FOREIGN KEY (user_id)
126 REFERENCES dbo.users(id)
127 ON DELETE NO ACTION,
136 CREATE NONCLUSTERED INDEX IX_payment_transactions_booking
139 CREATE NONCLUSTERED INDEX IX_payment_transactions_user
147 CREATE TABLE dbo.payment_refunds (
158 CONSTRAINT PK_payment_refunds PRIMARY KEY (id),
161 FOREIGN KEY (payment_transaction_id)
162 REFERENCES dbo.payment_transactions(id)
163 ON DELETE NO ACTION,
166 FOREIGN KEY (processed_by)
167 REFERENCES dbo.users(id)
168 ON DELETE NO ACTION,
177 CREATE NONCLUSTERED INDEX IX_payment_refunds_transaction
189 CREATE TABLE dbo.provider_settlements (
203 CONSTRAINT PK_provider_settlements PRIMARY KEY (id),
206 FOREIGN KEY (provider_profile_id)
207 REFERENCES dbo.provider_profiles(id)
208 ON DELETE NO ACTION,
223 CREATE NONCLUSTERED INDEX IX_provider_settlements_provider
231 CREATE TABLE dbo.notifications (
242 CONSTRAINT PK_notifications PRIMARY KEY (id),
245 FOREIGN KEY (user_id)
246 REFERENCES dbo.users(id)
247 ON DELETE CASCADE
250 CREATE NONCLUSTERED INDEX IX_notifications_user
262 CREATE TABLE dbo.reviews (
275 CONSTRAINT PK_reviews PRIMARY KEY (id),
278 FOREIGN KEY (listing_reference_id)
279 REFERENCES dbo.listing_references(id)
280 ON DELETE CASCADE,
283 FOREIGN KEY (booking_reference_id)
284 REFERENCES dbo.booking_references(id)
285 ON DELETE SET NULL,
288 FOREIGN KEY (user_id)
289 REFERENCES dbo.users(id)
290 ON DELETE NO ACTION,
296 CREATE NONCLUSTERED INDEX IX_reviews_listing
299 CREATE NONCLUSTERED INDEX IX_reviews_user
307 CREATE TABLE dbo.wishlists (
314 CONSTRAINT PK_wishlists PRIMARY KEY (id),
317 FOREIGN KEY (user_id)
318 REFERENCES dbo.users(id)
319 ON DELETE CASCADE,
322 FOREIGN KEY (listing_reference_id)
323 REFERENCES dbo.listing_references(id)
324 ON DELETE CASCADE,
327 UNIQUE (user_id, listing_reference_id)
330 CREATE NONCLUSTERED INDEX IX_wishlists_user
342 CREATE TABLE dbo.promotion_packages (
355 CONSTRAINT PK_promotion_packages PRIMARY KEY (id),
358 FOREIGN KEY (provider_profile_id)
359 REFERENCES dbo.provider_profiles(id)
360 ON DELETE CASCADE,
372 CREATE NONCLUSTERED INDEX IX_promotion_packages_provider
380 CREATE TABLE dbo.promotion_campaigns (
393 CONSTRAINT PK_promotion_campaigns PRIMARY KEY (id),
396 FOREIGN KEY (provider_profile_id)
397 REFERENCES dbo.provider_profiles(id)
398 ON DELETE CASCADE,
413 CREATE NONCLUSTERED INDEX IX_promotion_campaigns_provider
421 CREATE TABLE dbo.promotion_events (
429 CONSTRAINT PK_promotion_events PRIMARY KEY (id),
432 FOREIGN KEY (campaign_id)
433 REFERENCES dbo.promotion_campaigns(id)
434 ON DELETE CASCADE,
437 FOREIGN KEY (user_id)
438 REFERENCES dbo.users(id)
439 ON DELETE NO ACTION,
445 CREATE NONCLUSTERED INDEX IX_promotion_events_campaign
457 CREATE TABLE dbo.booking_status_history (
466 CONSTRAINT PK_booking_status_history PRIMARY KEY (id),
469 FOREIGN KEY (booking_reference_id)
470 REFERENCES dbo.booking_references(id)
471 ON DELETE CASCADE,
474 FOREIGN KEY (changed_by)
475 REFERENCES dbo.users(id)
476 ON DELETE NO ACTION
479 CREATE NONCLUSTERED INDEX IX_booking_status_history_booking
487 CREATE TABLE dbo.admin_audit_logs (
497 CONSTRAINT PK_admin_audit_logs PRIMARY KEY (id),
500 FOREIGN KEY (admin_user_id)
501 REFERENCES dbo.users(id)
502 ON DELETE NO ACTION
505 CREATE NONCLUSTERED INDEX IX_admin_audit_logs_admin
508 CREATE NONCLUSTERED INDEX IX_admin_audit_logs_entity
~~~

### 028_create_travel_community_module.sql

~~~text
12 CREATE TABLE dbo.community_posts (
26 CONSTRAINT PK_community_posts PRIMARY KEY (id),
29 FOREIGN KEY (author_id)
30 REFERENCES dbo.users(id)
31 ON DELETE CASCADE,
40 CREATE NONCLUSTERED INDEX IX_community_posts_author
43 CREATE NONCLUSTERED INDEX IX_community_posts_created
46 CREATE NONCLUSTERED INDEX IX_community_posts_district
54 CREATE TABLE dbo.community_post_media (
62 CONSTRAINT PK_community_post_media PRIMARY KEY (id),
65 FOREIGN KEY (post_id)
66 REFERENCES dbo.community_posts(id)
67 ON DELETE CASCADE,
76 CREATE NONCLUSTERED INDEX IX_community_post_media_post
88 CREATE TABLE dbo.community_post_likes (
94 CONSTRAINT PK_community_post_likes PRIMARY KEY (id),
97 FOREIGN KEY (post_id)
98 REFERENCES dbo.community_posts(id)
99 ON DELETE CASCADE,
103 FOREIGN KEY (user_id)
104 REFERENCES dbo.users(id)
105 ON DELETE NO ACTION,
108 UNIQUE (post_id, user_id)
111 CREATE NONCLUSTERED INDEX IX_community_post_likes_user
119 CREATE TABLE dbo.community_comments (
128 CONSTRAINT PK_community_comments PRIMARY KEY (id),
131 FOREIGN KEY (post_id)
132 REFERENCES dbo.community_posts(id)
133 ON DELETE CASCADE,
137 FOREIGN KEY (author_id)
138 REFERENCES dbo.users(id)
139 ON DELETE NO ACTION,
142 FOREIGN KEY (parent_comment_id)
143 REFERENCES dbo.community_comments(id)
144 ON DELETE NO ACTION
147 CREATE NONCLUSTERED INDEX IX_community_comments_post
150 CREATE NONCLUSTERED INDEX IX_community_comments_author
153 CREATE NONCLUSTERED INDEX IX_community_comments_parent
165 CREATE TABLE dbo.community_saved_posts (
171 CONSTRAINT PK_community_saved_posts PRIMARY KEY (id),
174 FOREIGN KEY (user_id)
175 REFERENCES dbo.users(id)
176 ON DELETE CASCADE,
179 FOREIGN KEY (post_id)
180 REFERENCES dbo.community_posts(id)
181 ON DELETE NO ACTION,
184 UNIQUE (user_id, post_id)
187 CREATE NONCLUSTERED INDEX IX_community_saved_posts_post
195 CREATE TABLE dbo.community_follows (
201 CONSTRAINT PK_community_follows PRIMARY KEY (id),
204 FOREIGN KEY (follower_id)
205 REFERENCES dbo.users(id)
206 ON DELETE NO ACTION,
209 FOREIGN KEY (following_id)
210 REFERENCES dbo.users(id)
211 ON DELETE NO ACTION,
214 UNIQUE (follower_id, following_id),
220 CREATE NONCLUSTERED INDEX IX_community_follows_follower
223 CREATE NONCLUSTERED INDEX IX_community_follows_following
235 CREATE TABLE dbo.community_reports (
248 CONSTRAINT PK_community_reports PRIMARY KEY (id),
251 FOREIGN KEY (reporter_id)
252 REFERENCES dbo.users(id)
253 ON DELETE NO ACTION,
256 FOREIGN KEY (reviewed_by)
257 REFERENCES dbo.users(id)
258 ON DELETE NO ACTION,
267 CREATE NONCLUSTERED INDEX IX_community_reports_entity
270 CREATE NONCLUSTERED INDEX IX_community_reports_status
273 CREATE NONCLUSTERED INDEX IX_community_reports_reporter
285 CREATE TABLE dbo.community_hashtags (
291 CONSTRAINT PK_community_hashtags PRIMARY KEY (id),
294 UNIQUE (tag),
300 CREATE NONCLUSTERED INDEX IX_community_hashtags_usage
308 CREATE TABLE dbo.community_post_hashtags (
314 CONSTRAINT PK_community_post_hashtags PRIMARY KEY (id),
317 FOREIGN KEY (post_id)
318 REFERENCES dbo.community_posts(id)
319 ON DELETE CASCADE,
322 FOREIGN KEY (hashtag_id)
323 REFERENCES dbo.community_hashtags(id)
324 ON DELETE CASCADE,
327 UNIQUE (post_id, hashtag_id)
330 CREATE NONCLUSTERED INDEX IX_community_post_hashtags_hashtag
~~~

### 029_create_ai_travel_planner_module.sql

~~~text
12 CREATE TABLE dbo.ai_chat_sessions (
20 CONSTRAINT PK_ai_chat_sessions PRIMARY KEY (id),
23 FOREIGN KEY (user_id)
24 REFERENCES dbo.users(id)
25 ON DELETE CASCADE
28 CREATE NONCLUSTERED INDEX IX_ai_chat_sessions_user
36 CREATE TABLE dbo.ai_chat_messages (
44 CONSTRAINT PK_ai_chat_messages PRIMARY KEY (id),
47 FOREIGN KEY (session_id)
48 REFERENCES dbo.ai_chat_sessions(id)
49 ON DELETE CASCADE,
55 CREATE NONCLUSTERED INDEX IX_ai_chat_messages_session
67 CREATE TABLE dbo.travel_preferences (
79 CONSTRAINT PK_travel_preferences PRIMARY KEY (id),
82 FOREIGN KEY (user_id)
83 REFERENCES dbo.users(id)
84 ON DELETE CASCADE,
87 UNIQUE (user_id),
108 CREATE TABLE dbo.travel_plans (
124 CONSTRAINT PK_travel_plans PRIMARY KEY (id),
128 FOREIGN KEY (user_id)
129 REFERENCES dbo.users(id)
130 ON DELETE NO ACTION,
133 FOREIGN KEY (ai_session_id)
134 REFERENCES dbo.ai_chat_sessions(id)
135 ON DELETE SET NULL,
138 UNIQUE (share_token),
150 CREATE NONCLUSTERED INDEX IX_travel_plans_user
153 CREATE NONCLUSTERED INDEX IX_travel_plans_dates
161 CREATE TABLE dbo.travel_plan_days (
171 CONSTRAINT PK_travel_plan_days PRIMARY KEY (id),
174 FOREIGN KEY (travel_plan_id)
175 REFERENCES dbo.travel_plans(id)
176 ON DELETE CASCADE,
179 UNIQUE (travel_plan_id, day_number),
185 CREATE NONCLUSTERED INDEX IX_travel_plan_days_plan
197 CREATE TABLE dbo.travel_plan_items (
213 CONSTRAINT PK_travel_plan_items PRIMARY KEY (id),
216 FOREIGN KEY (plan_day_id)
217 REFERENCES dbo.travel_plan_days(id)
218 ON DELETE CASCADE,
221 FOREIGN KEY (listing_reference_id)
222 REFERENCES dbo.listing_references(id)
223 ON DELETE SET NULL,
235 CREATE NONCLUSTERED INDEX IX_travel_plan_items_day
238 CREATE NONCLUSTERED INDEX IX_travel_plan_items_listing
250 CREATE TABLE dbo.ai_pending_actions (
261 CONSTRAINT PK_ai_pending_actions PRIMARY KEY (id),
264 FOREIGN KEY (session_id)
265 REFERENCES dbo.ai_chat_sessions(id)
266 ON DELETE CASCADE,
270 FOREIGN KEY (user_id)
271 REFERENCES dbo.users(id)
272 ON DELETE NO ACTION,
278 CREATE NONCLUSTERED INDEX IX_ai_pending_actions_session
281 CREATE NONCLUSTERED INDEX IX_ai_pending_actions_user_status
289 CREATE TABLE dbo.ai_action_audit (
297 CONSTRAINT PK_ai_action_audit PRIMARY KEY (id),
300 FOREIGN KEY (pending_action_id)
301 REFERENCES dbo.ai_pending_actions(id)
302 ON DELETE CASCADE,
305 FOREIGN KEY (user_id)
306 REFERENCES dbo.users(id)
307 ON DELETE NO ACTION,
313 CREATE NONCLUSTERED INDEX IX_ai_action_audit_action
~~~

### 030_extend_homestay_final_requirements.sql

~~~text
12 ALTER TABLE dbo.homestay_bookings
18 ALTER TABLE dbo.homestay_bookings
24 ALTER TABLE dbo.homestay_bookings
30 ALTER TABLE dbo.homestay_bookings
36 ALTER TABLE dbo.homestay_bookings
42 ALTER TABLE dbo.homestay_bookings
56 ALTER TABLE dbo.homestay_bookings
57 DROP CONSTRAINT CK_bookings_status;
60 ALTER TABLE dbo.homestay_bookings
83 ALTER TABLE dbo.homestay_bookings
98 CREATE NONCLUSTERED INDEX IX_homestay_bookings_payment_expiry
109 CREATE NONCLUSTERED INDEX IX_homestay_bookings_approved_at
120 CREATE TABLE dbo.advertising_payments (
141 CONSTRAINT PK_advertising_payments PRIMARY KEY (id),
144 FOREIGN KEY (campaign_id)
145 REFERENCES dbo.promotion_campaigns(id)
146 ON DELETE NO ACTION,
149 FOREIGN KEY (provider_profile_id)
150 REFERENCES dbo.provider_profiles(id)
151 ON DELETE NO ACTION,
154 UNIQUE (order_reference),
174 CREATE UNIQUE NONCLUSTERED INDEX UQ_advertising_payments_gateway_tx
185 CREATE NONCLUSTERED INDEX IX_advertising_payments_campaign
195 CREATE NONCLUSTERED INDEX IX_advertising_payments_provider
205 CREATE NONCLUSTERED INDEX IX_advertising_payments_status
215 ALTER TABLE dbo.promotion_campaigns
229 ALTER TABLE dbo.promotion_campaigns
231 FOREIGN KEY (listing_reference_id)
232 REFERENCES dbo.listing_references(id)
233 ON DELETE SET NULL;
246 CREATE NONCLUSTERED INDEX IX_promotion_campaigns_listing_active
257 ALTER TABLE dbo.homestay_promotions
264 ALTER TABLE dbo.homestay_promotions
278 ALTER TABLE dbo.homestay_promotions
279 DROP CONSTRAINT CK_homestay_promotions_discount;
282 ALTER TABLE dbo.homestay_promotions
295 ALTER TABLE dbo.homestay_promotions
310 ALTER TABLE dbo.homestay_promotions
325 ALTER TABLE dbo.payment_transactions
331 ALTER TABLE dbo.payment_transactions
337 ALTER TABLE dbo.payment_transactions
343 ALTER TABLE dbo.payment_transactions
~~~

### 031_extend_promotion_booking_attribution.sql

~~~text
12 ALTER TABLE dbo.promotion_events
26 ALTER TABLE dbo.promotion_events
28 FOREIGN KEY (booking_reference_id)
29 REFERENCES dbo.booking_references(id)
30 ON DELETE SET NULL;
43 CREATE UNIQUE NONCLUSTERED INDEX UQ_promotion_events_campaign_booking
58 CREATE NONCLUSTERED INDEX IX_promotion_events_booking_reference
73 ALTER TABLE dbo.promotion_campaigns
74 DROP CONSTRAINT CK_promotion_campaigns_status;
78 ALTER TABLE dbo.promotion_campaigns
~~~

### 032_enforce_tour_guide_coordinate_pairs.sql

~~~text
12 THROW 50300, 'Migration 032 requires the Tour Guide tables from migration 022.', 1;
22 THROW 50301, 'Invalid base coordinates in dbo.tour_guides. Review existing rows before retrying.', 1;
32 THROW 50302, 'Invalid meeting coordinates in dbo.tour_guide_packages. Review existing rows before retrying.', 1;
34 ALTER TABLE dbo.tour_guides DROP CONSTRAINT CK_tour_guides_coords;
36 ALTER TABLE dbo.tour_guides WITH CHECK ADD CONSTRAINT CK_tour_guides_coords
44 ALTER TABLE dbo.tour_guide_packages DROP CONSTRAINT CK_tour_guide_packages_coords;
46 ALTER TABLE dbo.tour_guide_packages WITH CHECK ADD CONSTRAINT CK_tour_guide_packages_coords
~~~

### 033_extend_rental_module_final_gaps.sql

~~~text
12 ALTER TABLE dbo.rental_eligibility_checks
19 ALTER TABLE dbo.rental_eligibility_checks
27 ALTER TABLE dbo.rental_eligibility_checks
34 ALTER TABLE dbo.rental_eligibility_checks
44 ALTER TABLE dbo.rental_vehicles
51 ALTER TABLE dbo.rental_vehicles
59 ALTER TABLE dbo.rental_vehicles
66 ALTER TABLE dbo.rental_vehicles
73 ALTER TABLE dbo.rental_vehicles
80 ALTER TABLE dbo.rental_vehicles
88 ALTER TABLE dbo.rental_vehicles
95 ALTER TABLE dbo.rental_vehicles
106 ALTER TABLE dbo.rental_inspections
113 ALTER TABLE dbo.rental_inspections
123 ALTER TABLE dbo.rental_vehicle_documents
130 ALTER TABLE dbo.rental_vehicle_documents
138 ALTER TABLE dbo.rental_vehicle_documents
145 ALTER TABLE dbo.rental_vehicle_documents
153 ALTER TABLE dbo.rental_vehicle_documents
160 ALTER TABLE dbo.rental_vehicle_documents
~~~

### 034_create_eco_reservations.sql

~~~text
10 THROW 50033, 'Eco reservations require the existing trusted booking schema.', 1;
14 THROW 50034, 'Unexpected pre-existing Eco reservation tables.', 1;
16 CREATE TABLE dbo.eco_availability_slots (
17 id INT IDENTITY(1,1) CONSTRAINT PK_eco_availability_slots PRIMARY KEY,
18 eco_experience_id INT NOT NULL REFERENCES dbo.eco_experiences(id),
30 CREATE INDEX IX_eco_slot_schedule ON dbo.eco_availability_slots(eco_experience_id, starts_at);
32 CREATE TABLE dbo.eco_reservations (
33 id INT IDENTITY(1,1) CONSTRAINT PK_eco_reservations PRIMARY KEY,
34 slot_id INT NOT NULL REFERENCES dbo.eco_availability_slots(id),
35 traveler_id INT NOT NULL REFERENCES dbo.users(id),
60 CREATE UNIQUE INDEX UX_eco_reservation_active_traveler
62 CREATE INDEX IX_eco_reservation_traveler ON dbo.eco_reservations(traveler_id, id);
70 ALTER TABLE dbo.booking_references DROP CONSTRAINT CK_booking_references_type;
72 N'ALTER TABLE dbo.booking_references WITH CHECK ADD CONSTRAINT CK_booking_references_type CHECK ('
~~~

### 035_extend_tour_guide_module_final_gaps.sql

~~~text
23 ALTER TABLE dbo.tour_guide_bookings
29 ALTER TABLE dbo.tour_guide_bookings
35 ALTER TABLE dbo.tour_guide_bookings
41 ALTER TABLE dbo.tour_guide_bookings
47 ALTER TABLE dbo.tour_guide_bookings
53 ALTER TABLE dbo.tour_guide_bookings
68 ALTER TABLE dbo.tour_guide_bookings
69 DROP CONSTRAINT CK_tour_guide_bookings_status;
72 ALTER TABLE dbo.tour_guide_bookings
95 ALTER TABLE dbo.tour_guide_bookings
106 ALTER TABLE dbo.tour_guide_bookings
117 ALTER TABLE dbo.tour_guide_bookings
128 CREATE NONCLUSTERED INDEX IX_tour_guide_bookings_payment_expiry
139 CREATE NONCLUSTERED INDEX IX_tour_guide_bookings_approved_at
150 ALTER TABLE dbo.tour_guide_packages
156 ALTER TABLE dbo.tour_guide_packages
162 ALTER TABLE dbo.tour_guide_packages
173 ALTER TABLE dbo.tour_guide_packages
185 ALTER TABLE dbo.tour_guides
191 ALTER TABLE dbo.tour_guides
198 ALTER TABLE dbo.tour_guides
209 ALTER TABLE dbo.tour_guides
223 ALTER TABLE dbo.tour_guides
~~~

### 036_align_shared_payment_status_constraints.sql

~~~text
26 ALTER TABLE dbo.payment_transactions
27 DROP CONSTRAINT CK_payment_transactions_status;
30 ALTER TABLE dbo.payment_transactions
53 ALTER TABLE dbo.payment_refunds
54 DROP CONSTRAINT CK_payment_refunds_status;
57 ALTER TABLE dbo.payment_refunds
79 ALTER TABLE dbo.provider_settlements
80 DROP CONSTRAINT CK_provider_settlements_status;
83 ALTER TABLE dbo.provider_settlements
~~~

### 037_extend_artisan_reservation_rejection.sql

~~~text
2 ALTER TABLE dbo.artisan_reservations
8 ALTER TABLE dbo.artisan_reservations WITH CHECK
10 FOREIGN KEY (rejected_by) REFERENCES dbo.users(id);
13 ALTER TABLE dbo.artisan_reservations
14 DROP CONSTRAINT CK_artisan_reservations_status;
17 ALTER TABLE dbo.artisan_reservations WITH CHECK
~~~

### 038_create_artisan_product_favorites.sql

~~~text
2 CREATE TABLE dbo.artisan_product_favorites (
8 CONSTRAINT PK_artisan_product_favorites PRIMARY KEY (id),
9 CONSTRAINT UQ_artisan_favorites_user_product UNIQUE (user_id, product_id),
11 FOREIGN KEY (user_id) REFERENCES dbo.users(id) ON DELETE CASCADE,
13 FOREIGN KEY (product_id) REFERENCES dbo.showcase_items(id) ON DELETE CASCADE
17 CREATE INDEX IX_artisan_product_favorites_product_id
~~~

### 039_extend_travel_community_module.sql

~~~text
25 ALTER TABLE dbo.community_posts
31 ALTER TABLE dbo.community_posts
38 ALTER TABLE dbo.community_posts
45 ALTER TABLE dbo.community_posts
63 ALTER TABLE dbo.community_posts
65 FOREIGN KEY (listing_reference_id)
66 REFERENCES dbo.listing_references(id)
67 ON DELETE SET NULL;
76 ALTER TABLE dbo.community_posts
87 CREATE NONCLUSTERED INDEX IX_community_posts_listing_ref
98 CREATE NONCLUSTERED INDEX IX_community_posts_promotional_feed
109 CREATE NONCLUSTERED INDEX IX_community_posts_featured_feed
122 CREATE TABLE dbo.community_user_profiles (
134 CONSTRAINT PK_community_user_profiles PRIMARY KEY CLUSTERED (user_id),
137 FOREIGN KEY (user_id)
138 REFERENCES dbo.users(id)
139 ON DELETE NO ACTION,
142 FOREIGN KEY (suspended_by)
143 REFERENCES dbo.users(id)
144 ON DELETE NO ACTION,
175 CREATE NONCLUSTERED INDEX IX_community_user_profiles_suspended
186 ALTER TABLE dbo.community_hashtags
193 ALTER TABLE dbo.community_hashtags
208 ALTER TABLE dbo.community_hashtags
219 CREATE NONCLUSTERED INDEX IX_community_hashtags_blocked
230 CREATE NONCLUSTERED INDEX IX_community_hashtags_featured
241 ALTER TABLE dbo.community_comments
259 ALTER TABLE dbo.community_comments
~~~

### 040_add_users_credential_version.sql

~~~text
2 ALTER TABLE dbo.users
7 ALTER TABLE dbo.users WITH CHECK
~~~

### 041_extend_eco_native_safety_and_refunds.sql

~~~text
10 IF OBJECT_ID(N'dbo.schema_migrations', N'U') IS NULL
11 THROW 51040, 'Eco migrations require the existing canonical migration ledger.', 1;
14 SELECT COUNT(*) FROM dbo.schema_migrations
17 THROW 51041, 'Eco migrations require a verified ledger through 040.', 1;
24 THROW 51042, 'Eco native prerequisites are missing.', 1;
32 THROW 51044, 'Expected a trusted shared refund status constraint.', 1;
39 THROW 51047, 'Unexpected existing Eco migration object. Reconcile before upgrade.', 1;
47 THROW 51001, 'Expected a trusted Eco reservation payment constraint.', 1;
58 THROW 51043, 'Eco reservation payment data does not match the canonical 034 baseline.', 1;
60 ALTER TABLE dbo.eco_reservations DROP CONSTRAINT CK_eco_reservation_payment;
61 ALTER TABLE dbo.eco_reservations WITH CHECK
73 CREATE TABLE dbo.eco_cancellation_policies (
74 eco_experience_id INT NOT NULL CONSTRAINT PK_eco_cancellation_policies PRIMARY KEY
75 CONSTRAINT FK_eco_policy_experience REFERENCES dbo.eco_experiences(id),
81 CREATE TABLE dbo.eco_cancellation_snapshots (
82 reservation_id INT NOT NULL CONSTRAINT PK_eco_cancellation_snapshots PRIMARY KEY
83 CONSTRAINT FK_eco_snapshot_reservation REFERENCES dbo.eco_reservations(id),
89 CREATE TABLE dbo.eco_age_limits (
90 eco_experience_id INT NOT NULL CONSTRAINT PK_eco_age_limits PRIMARY KEY
91 CONSTRAINT FK_eco_age_limit_experience REFERENCES dbo.eco_experiences(id),
95 CREATE TABLE dbo.eco_slot_clearances (
97 REFERENCES dbo.eco_availability_slots(id),
101 verified_by INT NOT NULL CONSTRAINT FK_eco_clearance_reviewer REFERENCES dbo.users(id),
106 CONSTRAINT PK_eco_slot_clearances PRIMARY KEY (slot_id, kind)
109 CREATE TABLE dbo.eco_refund_reconciliations (
110 refund_id INT NOT NULL CONSTRAINT PK_eco_refund_reconciliations PRIMARY KEY
111 CONSTRAINT FK_eco_reconciliation_refund REFERENCES dbo.payment_refunds(id),
118 CREATE UNIQUE INDEX UX_eco_refund_gateway_reference
~~~

### 042_create_eco_sponsorship_ledger.sql

~~~text
10 IF OBJECT_ID(N'dbo.schema_migrations', N'U') IS NULL
11 THROW 51040, 'Eco migrations require the existing canonical migration ledger.', 1;
14 SELECT COUNT(*) FROM dbo.schema_migrations
17 THROW 51041, 'Eco migrations require a verified ledger through 040.', 1;
20 SELECT 1 FROM dbo.schema_migrations WHERE version = '041'
22 THROW 51045, 'Eco sponsorship requires the canonical ledger through 041.', 1;
27 THROW 51046, 'Eco sponsorship prerequisites are missing.', 1;
32 THROW 51048, 'Unexpected existing Eco migration object. Reconcile before upgrade.', 1;
34 CREATE TABLE dbo.eco_sponsorship_captures (
35 gateway_id NVARCHAR(100) NOT NULL CONSTRAINT PK_eco_sponsorship_captures PRIMARY KEY,
37 REFERENCES dbo.eco_sponsored_promotions(id),
44 CREATE INDEX IX_eco_sponsor_capture_promotion
46 CREATE UNIQUE INDEX UX_eco_sponsor_applied
49 CREATE TABLE dbo.eco_promotion_event_receipts (
53 REFERENCES dbo.eco_sponsored_promotions(id),
54 CONSTRAINT PK_eco_promotion_event_receipts PRIMARY KEY (nonce, event_type),
58 CREATE TABLE dbo.eco_promotion_attributions (
59 booking_reference_id INT NOT NULL CONSTRAINT PK_eco_promotion_attributions PRIMARY KEY
60 CONSTRAINT FK_eco_attribution_booking REFERENCES dbo.booking_references(id),
61 nonce NVARCHAR(32) NOT NULL CONSTRAINT UQ_eco_attribution_nonce UNIQUE,
63 REFERENCES dbo.eco_sponsored_promotions(id),
67 CREATE INDEX IX_eco_attribution_promotion
~~~

### 043_add_user_auth_version.sql

~~~text
3 ALTER TABLE dbo.users ADD auth_version BIGINT NOT NULL
~~~
