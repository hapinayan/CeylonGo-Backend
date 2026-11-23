# Phase 02A database mapping

The application's 99 configured mappings are the inventory. This says nothing about the installed SQL Server schema. Classifications describe fit in the planned MVP and do not authorize removing anything. Shared references support native domains. Preserve provider ownership, listing approval, availability and inventory units, booking and payment state separation, and Community moderation integrity.

Use native listing, availability and booking tables for Homestay, Rental, Activity and Guide. Decide whether Adventure also needs native Eco listings and reservations before retiring any route. Reuse users, provider identity, shared listing/booking references, and history. Evaluate whether existing transaction constraints can express demo-only outcomes without confusing historical gateway records. Avoid new tables unless a proven incompatibility requires separately approved schema work. Preserve all 43 historical migrations and rollback files.

Review tables cover data that may still serve publication, eligibility, location, moderation, or remaining schema constraints. Planner persistence is optional for a bounded request/response workflow. Map and Weather do not need speculative new domain tables. FK targets are ORM declarations; migration catalog and live SQL Server definitions require separate evidence.

- ADVANCED / OUT OF SCOPE ? 33
- REQUIRED FOR MVP ? 20
- REVIEW REQUIRED ? 34
- SHARED DEPENDENCY ? 12

| Table | Mapped class / file | Classification | FK targets |
|---|---|---|---|
| activities | [Activity](../../../app/models/activity.py#L22) | REQUIRED FOR MVP | provider_profiles.id, users.id |
| activity_booking_participants | [ActivityBookingParticipant](../../../app/models/activity_booking.py#L144) | REQUIRED FOR MVP | activity_bookings.id |
| activity_bookings | [ActivityBooking](../../../app/models/activity_booking.py#L20) | REQUIRED FOR MVP | activity_slots.id, eco_experiences.id, users.id |
| activity_images | [ActivityImage](../../../app/models/activity_image.py#L18) | REQUIRED FOR MVP | activities.id |
| activity_slots | [ActivitySlot](../../../app/models/activity_slot.py#L18) | REQUIRED FOR MVP | activities.id, eco_experiences.id |
| admin_audit_logs | [AdminAuditLog](../../../app/models/shared.py#L377) | SHARED DEPENDENCY | users.id |
| advertising_payments | [AdvertisingPayment](../../../app/models/shared.py#L295) | ADVANCED / OUT OF SCOPE | promotion_campaigns.id, provider_profiles.id |
| ai_action_audit | [AIActionAudit](../../../app/models/ai_planner.py#L222) | ADVANCED / OUT OF SCOPE | ai_pending_actions.id, users.id |
| ai_chat_messages | [AIChatMessage](../../../app/models/ai_planner.py#L40) | ADVANCED / OUT OF SCOPE | ai_chat_sessions.id |
| ai_chat_sessions | [AIChatSession](../../../app/models/ai_planner.py#L23) | ADVANCED / OUT OF SCOPE | users.id |
| ai_pending_actions | [AIPendingAction](../../../app/models/ai_planner.py#L192) | ADVANCED / OUT OF SCOPE | ai_chat_sessions.id, users.id |
| artisan_business_hours | [ArtisanBusinessHours](../../../app/models/artisan.py#L209) | ADVANCED / OUT OF SCOPE | artisan_listings.id |
| artisan_business_images | [ArtisanBusinessImage](../../../app/models/artisan.py#L23) | ADVANCED / OUT OF SCOPE | artisan_listings.id |
| artisan_listings | [ArtisanListing](../../../app/models/artisan_listing.py#L11) | ADVANCED / OUT OF SCOPE | provider_profiles.id, users.id |
| artisan_product_favorites | [ArtisanProductFavorite](../../../app/models/artisan.py#L74) | ADVANCED / OUT OF SCOPE | showcase_items.id, users.id |
| artisan_product_images | [ArtisanProductImage](../../../app/models/artisan.py#L53) | ADVANCED / OUT OF SCOPE | showcase_items.id |
| artisan_reservations | [ArtisanReservation](../../../app/models/artisan.py#L92) | ADVANCED / OUT OF SCOPE | artisan_listings.id, showcase_items.id, users.id |
| booking_references | [BookingReference](../../../app/models/shared.py#L46) | SHARED DEPENDENCY | provider_profiles.id, users.id |
| booking_status_history | [BookingStatusHistory](../../../app/models/shared.py#L356) | SHARED DEPENDENCY | booking_references.id, users.id |
| community_comments | [CommunityComment](../../../app/models/community.py#L112) | REQUIRED FOR MVP | community_comments.id, community_posts.id, users.id |
| community_follows | [CommunityFollow](../../../app/models/community.py#L168) | ADVANCED / OUT OF SCOPE | users.id |
| community_hashtags | [CommunityHashtag](../../../app/models/community.py#L218) | REVIEW REQUIRED | - |
| community_post_hashtags | [CommunityPostHashtag](../../../app/models/community.py#L276) | REVIEW REQUIRED | community_hashtags.id, community_posts.id |
| community_post_likes | [CommunityPostLike](../../../app/models/community.py#L90) | REQUIRED FOR MVP | community_posts.id, users.id |
| community_post_media | [CommunityPostMedia](../../../app/models/community.py#L65) | REVIEW REQUIRED | community_posts.id |
| community_posts | [CommunityPost](../../../app/models/community.py#L22) | REQUIRED FOR MVP | listing_references.id, users.id |
| community_reports | [CommunityReport](../../../app/models/community.py#L191) | REVIEW REQUIRED | users.id |
| community_saved_posts | [CommunitySavedPost](../../../app/models/community.py#L146) | ADVANCED / OUT OF SCOPE | community_posts.id, users.id |
| community_user_profiles | [CommunityUserProfile](../../../app/models/community.py#L239) | REVIEW REQUIRED | users.id |
| eco_age_limits | [EcoAgeLimit](../../../app/models/eco_reservation.py#L145) | REVIEW REQUIRED | eco_experiences.id |
| eco_availability_slots | [EcoAvailabilitySlot](../../../app/models/eco_reservation.py#L23) | REVIEW REQUIRED | eco_experiences.id |
| eco_cancellation_policies | [EcoCancellationPolicy](../../../app/models/eco_reservation.py#L98) | REVIEW REQUIRED | eco_experiences.id |
| eco_cancellation_snapshots | [EcoCancellationSnapshot](../../../app/models/eco_reservation.py#L113) | REVIEW REQUIRED | eco_reservations.id |
| eco_evidences | [EcoEvidence](../../../app/models/eco_experience.py#L649) | REVIEW REQUIRED | eco_practices.id |
| eco_experience_images | [EcoExperienceImage](../../../app/models/eco_experience.py#L434) | REVIEW REQUIRED | eco_experiences.id |
| eco_experiences | [EcoExperience](../../../app/models/eco_experience.py#L100) | REVIEW REQUIRED | homestays.id, provider_profiles.id, users.id |
| eco_practices | [EcoPractice](../../../app/models/eco_experience.py#L591) | REVIEW REQUIRED | eco_experiences.id |
| eco_promotion_analytics | [EcoPromotionAnalytics](../../../app/models/eco_promotion.py#L311) | ADVANCED / OUT OF SCOPE | eco_experiences.id, eco_sponsored_promotions.id |
| eco_promotion_attributions | [EcoPromotionAttribution](../../../app/models/eco_promotion.py#L62) | ADVANCED / OUT OF SCOPE | booking_references.id, eco_sponsored_promotions.id |
| eco_promotion_event_receipts | [EcoPromotionEventReceipt](../../../app/models/eco_promotion.py#L51) | ADVANCED / OUT OF SCOPE | eco_sponsored_promotions.id |
| eco_promotion_packages | [EcoPromotionPackage](../../../app/models/eco_promotion.py#L104) | ADVANCED / OUT OF SCOPE | - |
| eco_refund_reconciliations | [EcoRefundReconciliation](../../../app/models/eco_reservation.py#L126) | ADVANCED / OUT OF SCOPE | payment_refunds.id |
| eco_reservations | [EcoReservation](../../../app/models/eco_reservation.py#L46) | REVIEW REQUIRED | eco_availability_slots.id, users.id |
| eco_safety_rules | [EcoSafetyRule](../../../app/models/eco_experience.py#L479) | REVIEW REQUIRED | eco_experiences.id |
| eco_season_restrictions | [EcoSeasonRestriction](../../../app/models/eco_experience.py#L528) | REVIEW REQUIRED | eco_experiences.id, users.id |
| eco_slot_clearances | [EcoSlotClearance](../../../app/models/eco_reservation.py#L157) | REVIEW REQUIRED | eco_availability_slots.id, users.id |
| eco_sponsored_promotions | [EcoSponsoredPromotion](../../../app/models/eco_promotion.py#L159) | ADVANCED / OUT OF SCOPE | eco_experiences.id, eco_promotion_packages.id, provider_profiles.id, users.id |
| eco_sponsorship_captures | [EcoSponsorshipCapture](../../../app/models/eco_promotion.py#L34) | ADVANCED / OUT OF SCOPE | eco_sponsored_promotions.id |
| eco_verifications | [EcoVerification](../../../app/models/eco_experience.py#L698) | REVIEW REQUIRED | eco_experiences.id, provider_profiles.id, users.id |
| eco_wishlists | [EcoWishlist](../../../app/models/eco_wishlist.py#L14) | ADVANCED / OUT OF SCOPE | eco_experiences.id, users.id |
| homestay_amenities | [HomestayAmenity](../../../app/models/homestay.py#L297) | REVIEW REQUIRED | homestays.id |
| homestay_availability_blocks | [HomestayAvailabilityBlock](../../../app/models/homestay.py#L332) | REQUIRED FOR MVP | homestays.id, users.id |
| homestay_bookings | [Booking](../../../app/models/booking.py#L24) | REQUIRED FOR MVP | homestays.id, users.id |
| homestay_images | [HomestayImage](../../../app/models/homestay.py#L264) | REQUIRED FOR MVP | homestays.id |
| homestay_promotions | [HomestayPromotion](../../../app/models/promotion.py#L124) | ADVANCED / OUT OF SCOPE | homestays.id |
| homestays | [Homestay](../../../app/models/homestay.py#L28) | REQUIRED FOR MVP | provider_profiles.id, users.id |
| listing_references | [ListingReference](../../../app/models/shared.py#L23) | SHARED DEPENDENCY | provider_profiles.id |
| notifications | [Notification](../../../app/models/shared.py#L165) | SHARED DEPENDENCY | users.id |
| password_resets | [PasswordReset](../../../app/models/password_reset.py#L9) | SHARED DEPENDENCY | users.id |
| payment_refunds | [PaymentRefund](../../../app/models/shared.py#L126) | REVIEW REQUIRED | payment_transactions.id, users.id |
| payment_transactions | [PaymentTransaction](../../../app/models/shared.py#L101) | SHARED DEPENDENCY | booking_references.id, users.id |
| promotion_campaigns | [PromotionCampaign](../../../app/models/shared.py#L267) | ADVANCED / OUT OF SCOPE | listing_references.id, provider_profiles.id |
| promotion_events | [PromotionEvent](../../../app/models/shared.py#L333) | ADVANCED / OUT OF SCOPE | booking_references.id, promotion_campaigns.id, users.id |
| promotion_packages | [PromotionPackage](../../../app/models/shared.py#L247) | ADVANCED / OUT OF SCOPE | provider_profiles.id |
| promotions | [Promotion](../../../app/models/promotion.py#L28) | ADVANCED / OUT OF SCOPE | artisan_listings.id, showcase_items.id |
| provider_email_otps | [ProviderEmailOtp](../../../app/models/provider_email_otp.py#L9) | SHARED DEPENDENCY | - |
| provider_profiles | [ProviderProfile](../../../app/models/provider_profile.py#L9) | SHARED DEPENDENCY | users.id |
| provider_registration_authorizations | [ProviderRegistrationAuthorization](../../../app/models/provider_registration_authorization.py#L9) | SHARED DEPENDENCY | - |
| provider_registration_drafts | [ProviderRegistrationDraft](../../../app/models/provider_registration_draft.py#L9) | SHARED DEPENDENCY | - |
| provider_settlements | [ProviderSettlement](../../../app/models/shared.py#L144) | ADVANCED / OUT OF SCOPE | provider_profiles.id |
| rental_availability_blocks | [RentalAvailabilityBlock](../../../app/models/rental.py#L153) | REQUIRED FOR MVP | rental_vehicles.id |
| rental_bookings | [RentalBooking](../../../app/models/rental_booking.py#L18) | REQUIRED FOR MVP | rental_locations.id, rental_vehicles.id, users.id |
| rental_condition_evidence | [RentalConditionEvidence](../../../app/models/rental.py#L262) | ADVANCED / OUT OF SCOPE | rental_inspections.id |
| rental_deposits | [RentalDeposit](../../../app/models/rental.py#L330) | ADVANCED / OUT OF SCOPE | rental_bookings.id |
| rental_eligibility_checks | [RentalEligibilityCheck](../../../app/models/rental.py#L183) | REVIEW REQUIRED | users.id |
| rental_incidents | [RentalIncident](../../../app/models/rental.py#L287) | ADVANCED / OUT OF SCOPE | rental_bookings.id |
| rental_inspections | [RentalInspection](../../../app/models/rental.py#L219) | ADVANCED / OUT OF SCOPE | rental_bookings.id, users.id |
| rental_locations | [RentalLocation](../../../app/models/rental.py#L22) | REVIEW REQUIRED | provider_profiles.id |
| rental_rate_plans | [RentalRatePlan](../../../app/models/rental.py#L51) | REVIEW REQUIRED | rental_vehicles.id |
| rental_vehicle_documents | [RentalVehicleDocument](../../../app/models/rental.py#L112) | REVIEW REQUIRED | rental_vehicles.id |
| rental_vehicle_images | [RentalVehicleImage](../../../app/models/rental.py#L87) | REQUIRED FOR MVP | rental_vehicles.id |
| rental_vehicles | [RentalVehicle](../../../app/models/rental_vehicle.py#L19) | REQUIRED FOR MVP | provider_profiles.id, rental_locations.id, users.id |
| reviews | [Review](../../../app/models/shared.py#L181) | REVIEW REQUIRED | booking_references.id, listing_references.id, users.id |
| showcase_items | [ShowcaseItem](../../../app/models/showcase_item.py#L11) | ADVANCED / OUT OF SCOPE | artisan_listings.id |
| tour_guide_availability | [TourGuideAvailability](../../../app/models/tour_guide.py#L424) | REQUIRED FOR MVP | tour_guides.id |
| tour_guide_bookings | [TourGuideBooking](../../../app/models/tour_guide.py#L463) | REQUIRED FOR MVP | tour_guide_packages.id, tour_guide_packages.tour_guide_id, tour_guides.id, users.id |
| tour_guide_credentials | [TourGuideCredential](../../../app/models/tour_guide.py#L258) | REVIEW REQUIRED | tour_guides.id |
| tour_guide_images | [TourGuideImage](../../../app/models/tour_guide.py#L322) | REQUIRED FOR MVP | tour_guides.id |
| tour_guide_languages | [TourGuideLanguage](../../../app/models/tour_guide.py#L198) | REVIEW REQUIRED | tour_guides.id |
| tour_guide_packages | [TourGuidePackage](../../../app/models/tour_guide.py#L356) | REVIEW REQUIRED | tour_guides.id |
| tour_guide_service_areas | [TourGuideServiceArea](../../../app/models/tour_guide.py#L231) | REVIEW REQUIRED | tour_guides.id |
| tour_guide_verifications | [TourGuideVerification](../../../app/models/tour_guide.py#L585) | REVIEW REQUIRED | tour_guide_credentials.id, tour_guide_credentials.tour_guide_id, tour_guides.id, users.id |
| tour_guides | [TourGuide](../../../app/models/tour_guide.py#L30) | REQUIRED FOR MVP | provider_profiles.id, users.id |
| travel_plan_days | [TravelPlanDay](../../../app/models/ai_planner.py#L126) | REVIEW REQUIRED | travel_plans.id |
| travel_plan_items | [TravelPlanItem](../../../app/models/ai_planner.py#L153) | REVIEW REQUIRED | listing_references.id, travel_plan_days.id |
| travel_plans | [TravelPlan](../../../app/models/ai_planner.py#L92) | REVIEW REQUIRED | ai_chat_sessions.id, users.id |
| travel_preferences | [TravelPreference](../../../app/models/ai_planner.py#L64) | REVIEW REQUIRED | users.id |
| users | [User](../../../app/models/user.py#L12) | SHARED DEPENDENCY | - |
| wishlists | [Wishlist](../../../app/models/shared.py#L222) | ADVANCED / OUT OF SCOPE | listing_references.id, users.id |
