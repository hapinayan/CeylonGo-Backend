# CeylonGo Academic MVP Master Requirements V1.1

Discover Local. Feel at Home.

Diploma in Software Development — Final Year Academic Project

Version 1.1 | Newly authored from the approved Academic MVP brief

## 1. Project Introduction and Objectives

CeylonGo is a Sri Lankan tourism platform connecting travelers with local tourism service providers. Travelers can discover and book homestays, bike and bicycle rentals, adventure activities, and local tour guides. AI travel planning, a social feed, maps, and weather support the discovery experience. Providers publish and manage services. Administrators verify providers, review listings, and manage the platform.

The academic objectives are to demonstrate a working traveler-to-provider booking flow, clear business rules, and a credible route to market. Prefer simple workflows that the team can explain, test, and demonstrate. This document defines approved scope from the supplied brief. Proposed endpoints, conceptual tables, and flagged assumptions are planning material and require reconciliation with the repositories before implementation.

## 2. Academic MVP Scope

| Audience | Approved modules | Count |
|---|---|---:|
| Traveler | TR-01 through TR-09 | 9 |
| Service Provider | SP-01 through SP-03 | 3 |
| Admin | AD-01 and AD-02 | 2 |

Booking applies to homestays, bike and bicycle rentals, adventure activities, and local tour guides. Simulated Online Payment and Pay at Location are shared booking options. Maps and Weather are separate Traveler modules. The MVP uses one simple Admin role.

| Layer | Approved technology |
|---|---|
| Backend and persistence | Python FastAPI, SQLAlchemy, Microsoft SQL Server |
| Authentication | JWT authentication |
| Website and mobile | Angular website, Flutter mobile app |
| External integrations | Maps, Weather API, AI travel planning |

Reuse the existing stacks and compatible implementations. Existing functionality outside the academic scope is preserved. This document does not assess implementation status.

## 3. Traveler Functional Requirements

### TR-01 Login and Registration

- TR-01.1 Register a traveler account.
- TR-01.2 Log in.
- TR-01.3 Log out.
- TR-01.4 View and edit the traveler's own profile.

### TR-02 Homestay Discovery and Booking

- TR-02.1 Search and browse homestays.
- TR-02.2 View homestay details.
- TR-02.3 View price, photos, and location.
- TR-02.4 Check availability.
- TR-02.5 Submit a homestay booking.
- TR-02.6 View own homestay booking history.

### TR-03 Bike and Bicycle Rental

- TR-03.1 Browse rental vehicles in the approved bike and bicycle scope.
- TR-03.2 View vehicle details.
- TR-03.3 View the rental price.
- TR-03.4 Select rental dates.
- TR-03.5 Submit a rental booking.
- TR-03.6 View own rental booking history.

### TR-04 Adventure Activities

- TR-04.1 Browse adventure activities.
- TR-04.2 View activity details.
- TR-04.3 View pricing and schedule.
- TR-04.4 Select a date and number of participants.
- TR-04.5 Submit an activity booking.
- TR-04.6 View own activity booking history.

### TR-05 Local Tour Guide

- TR-05.1 Browse available local guides.
- TR-05.2 View a guide's profile.
- TR-05.3 View guide pricing.
- TR-05.4 Check guide availability.
- TR-05.5 Submit a guide booking.
- TR-05.6 View own guide booking history.

### TR-06 AI Travel Planner

- TR-06.1 Enter a destination.
- TR-06.2 Enter travel duration.
- TR-06.3 Enter a budget.
- TR-06.4 Generate a simple day-by-day itinerary.
- TR-06.5 Display suggested destinations and activities.

### TR-07 Social Communication Feed

- TR-07.1 Create posts.
- TR-07.2 View posts.
- TR-07.3 Like posts.
- TR-07.4 Comment on posts.
- TR-07.5 Delete own posts.

### TR-08 Map Integration

- TR-08.1 Display tourism service locations.
- TR-08.2 Show location markers.
- TR-08.3 Open directions using a map service.

### TR-09 Weather Integration

- TR-09.1 Display destination weather.
- TR-09.2 Display temperature and conditions.
- TR-09.3 Display a basic weather forecast.

## 4. Service Provider Functional Requirements

### SP-01 Registration and Login

- SP-01.1 Register a provider account.
- SP-01.2 Log in and log out.
- SP-01.3 Manage the provider's own profile.
- SP-01.4 Await administrator verification and distinguish the pending state from a decision.

### SP-02 Publish Service

- SP-02.1 Select an approved service category.
- SP-02.2 Enter service details.
- SP-02.3 Upload photos.
- SP-02.4 Set the price.
- SP-02.5 Set the location.
- SP-02.6 Set availability.
- SP-02.7 Submit the listing for approval.

### SP-03 Manage Service

- SP-03.1 View own service listings.
- SP-03.2 Edit own service details.
- SP-03.3 Update own service availability.
- SP-03.4 View booking requests for own services.
- SP-03.5 Accept or reject booking requests for own services.
- SP-03.6 View booking history for own services.

## 5. Admin Functional Requirements

Use one simple Admin role for the academic demonstration.

### AD-01 Verification

- AD-01.1 View pending provider applications.
- AD-01.2 Approve or reject providers.
- AD-01.3 Review submitted service listings.
- AD-01.4 Approve or reject submitted listings.

### AD-02 Management

- AD-02.1 View and manage travelers.
- AD-02.2 View and manage service providers.
- AD-02.3 Manage service listings.
- AD-02.4 View bookings.
- AD-02.5 Moderate social feed posts.

## 6. Booking Business Logic

- BK-01 A traveler discovers a service, views details, selects dates or a slot, checks availability, submits a booking, and chooses a payment option. The provider accepts or rejects. An accepted request becomes Confirmed if booking checks pass. A rejected request becomes Rejected.
- BK-02 An approved, active service is required at submission and confirmation. Verify the requesting traveler, provider ownership, availability, and applicable capacity on the server. Client availability is informational.
- BK-03 Prevent double-booking by making the confirmation check and capacity allocation atomic. Reject an unavailable request without allocating inventory. Capacity rules use the service's unit rather than a common assumption about inventory.
- BK-04 Preserve booking history for the authorized traveler and owning provider. Track booking status separately from payment status.
- BK-05 Proposed transition model for the demonstration. Pending is the initial status. Completed, Rejected, and Cancelled are terminal. Reject any transition absent from the table.

| Current status | Next status | Guard |
|---|---|---|
| Pending | Confirmed | Provider accepts, service is eligible, availability and capacity checks pass, and the chosen payment condition is met. |
| Pending | Rejected | Owning provider rejects. No confirmed inventory remains allocated. |
| Pending | Cancelled | Cancellation permitted by the agreed actor policy. Release any held capacity once. |
| Confirmed | Completed | Proposed assumption: owning provider marks the delivered service complete. |
| Confirmed | Cancelled | Cancellation permitted by the agreed actor policy. Release affected capacity once. |

| Service | Proposed availability check |
|---|---|
| Homestay | Requested stay overlaps an available room or bookable unit for the selected nights. |
| Bike and bicycle rental | A vehicle or a unit from the fleet remains available for the selected rental interval. |
| Adventure activity | The selected scheduled slot has capacity for the number of participants. |
| Local tour guide | The selected guide has no conflicting confirmed assignment for the requested interval. |

Design assumptions: sequential demo flow uses Pending requests and allocates capacity at confirmation. Reuse existing compatible hold rules when present. Completion and cancellation actors, cutoffs, and exact inventory units remain open decisions. Use a consistent local-time convention for inventory and avoid adding a cancellation or completion interface before role ownership is agreed.

Example: an adventure slot with four places has two confirmed participants. A request for three more cannot be confirmed. A concurrent request for the last available place cannot succeed twice.

## 7. Payment Business Logic

- PAY-01 Simulated Online Payment displays a demo interface clearly labeled as simulated. Allow success and failure. Record the demo result. No real money transfers, real card data collection, or production gateway calls occur.
- PAY-02 Pay at Location submits the booking without online payment. The provider receives the request. The traveler pays directly at the service location and the system records the unpaid, pay-at-location state.
- PAY-03 Keep the payment method and result distinct from the booking decision. A simulated success is a demonstration result. The provider still accepts or rejects the request.
- PAY-04 Never implement live PayHere processing, actual settlements, automatic provider payouts, or real refund processing for the MVP.

| Option | Proposed recorded state | Confirmation condition |
|---|---|---|
| Simulated Online Payment | Demo pending, demo succeeded, or demo failed | Proposed assumption: a failed simulation leaves the booking Pending and cannot confirm it. A successful demonstration can proceed to provider acceptance. |
| Pay at Location | Unpaid, pay at location | Provider acceptance and availability checks can confirm the booking while payment remains unpaid. |

Example: a traveler chooses Pay at Location for a homestay. The provider accepts and the booking becomes Confirmed. Its payment record still shows unpaid at location. Simulated payment produces no actual revenue.

## 8. System Workflows

| Flow | Demonstration sequence |
|---|---|
| Traveler account | Register, log in, open or edit own profile, log out. |
| Provider onboarding | Register, submit provider profile, await Admin decision, publish an eligible service. |
| Listing approval | Provider enters details and availability, submits listing, Admin reviews, eligible approved listing appears in discovery. |
| Bookable services | Traveler discovers service, checks details and availability, submits booking, chooses payment, provider accepts or rejects, traveler views history. |
| Trip planning | Traveler enters destination, duration, and budget. AI integration returns a simple day-by-day itinerary. |
| Social feed | Traveler creates, views, likes, or comments on posts. Author can delete own posts. Admin can moderate posts. |
| Map and weather | Traveler sees markers, opens directions, and views destination temperature, conditions, and forecast. |

## 9. Security and Non-Functional Requirements

- NFR-01 Use JWT authentication and enforce role and ownership on the server. Apply provider and listing approval checks where relevant.
- NFR-02 Validate input types and bounds, including booking dates, participant counts, budget, coordinates, and uploaded files. Limit photo uploads to approved types and limits drawn from existing policy.
- NFR-03 Hash passwords with the existing secure password hashing mechanism. Prevent sensitive values and tokens from appearing in logs or error responses.
- NFR-04 Minimize exposure of account, provider, and booking data. Do not send unnecessary personal information to Maps, Weather, or AI services. Keep external credentials server-side or apply existing restricted client-key controls where appropriate.
- NFR-05 Keep booking status, capacity, and payment-record updates consistent. Unauthorized actions, validation errors, and failed booking operations must leave unrelated records unchanged.
- NFR-06 Return predictable validation, authorization, and conflict errors. Bound external requests with timeouts and show a clear unavailable state without fabricating results.
- NFR-07 Support responsive website layouts and the approved Flutter user flows. Reuse existing project conventions and keep business logic testable.
- NFR-08 Use bounded listing and history reads and avoid avoidable repeated queries. Record repeatable demo response times for representative data. Numerical latency and load targets remain to be agreed.

## 10. API Planning

All paths below are illustrative under a proposed /api/v1 prefix. Map them to existing contracts before adding routes. Reuse shared booking, approval, and authentication behavior. Service keys and identifiers are validated server-side. Use consistent errors and pagination on list reads. The historical estimate of 65–85 operations is neither a limit nor a verified inventory.

| Module | HTTP method and proposed endpoint | Authorized role | Purpose and request → response |
|---|---|---|---|
| TR-01 | POST /auth/register, /auth/login, /auth/logout; GET and PATCH /me | Guest for registration and login, authenticated traveler for profile and logout | Account data or credentials → account/session result. Profile fields → updated profile. Logout request → session or client logout result per existing contract. |
| TR-02 | GET /homestays, /homestays/{id}, /homestays/{id}/availability; POST /bookings; GET /me/bookings | Public discovery, traveler bookings | Filters, listing or dates → cards, details, availability. Homestay selection → booking summary and history. |
| TR-03 | GET /bike-rentals, /bike-rentals/{id}; POST /bookings; GET /me/bookings | Public discovery, traveler bookings | Listing or dates → vehicle and pricing context. Vehicle and dates → booking summary and history. |
| TR-04 | GET /activities, /activities/{id}; POST /bookings; GET /me/bookings | Public discovery, traveler bookings | Listing → pricing and schedule. Slot and participants → booking summary and history. |
| TR-05 | GET /tour-guides, /tour-guides/{id}, /tour-guides/{id}/availability; POST /bookings; GET /me/bookings | Public discovery, traveler bookings | Guide or dates → profile, price, availability. Guide and dates → booking summary and history. |
| TR-06 | POST /itineraries/generate | Traveler | Destination, duration, budget → simple day-by-day suggestions. |
| TR-07 | GET and POST /feed/posts; POST /feed/posts/{id}/like, /feed/posts/{id}/comments; DELETE /feed/posts/{id} | Traveler, with owner check for delete | Post, like, or comment input → feed item or action result. Post ID → deletion result. |
| TR-08 | GET /map/locations | Traveler-facing read | Service type or map bounds → authorized location markers and direction target data. Client opens the map service. |
| TR-09 | GET /weather | Traveler-facing read | Destination or coordinates → current conditions, temperature, and basic forecast. |
| SP-01 | POST /providers/register, /providers/login, /providers/logout; GET and PATCH /provider/profile | Guest for registration and login, authenticated provider for profile and logout | Registration or credentials → account/session result. Profile fields → profile and verification status. |
| SP-02 | POST /provider/services, /provider/services/{id}/photos, /provider/services/{id}/submit; PATCH /provider/services/{id} | Verified provider, owned listing | Category, details, photos, price, location, and availability → saved listing and submission state. |
| SP-03 | GET /provider/services, /provider/bookings; PATCH /provider/services/{id}, /provider/services/{id}/availability; POST /provider/bookings/{id}/decision | Verified provider, owned records | Listing changes and booking decisions → persisted details, availability, requests, and history. |
| AD-01 | GET /admin/provider-applications, /admin/listing-submissions; POST /admin/provider-applications/{id}/decision, /admin/listing-submissions/{id}/decision | Admin | Review filters or approve/reject decision → queue or decision result. |
| AD-02 | GET /admin/travelers, /admin/providers, /admin/services, /admin/bookings; PATCH /admin/travelers/{id}, /admin/providers/{id}, /admin/services/{id}; POST /admin/feed/posts/{id}/moderation | Admin | Filters or allowed management action → list or updated record. |

Shared proposed booking operations: POST /bookings takes listing and service-specific selection, GET /me/bookings returns the traveler's history, and POST /bookings/{id}/payment selects a payment option or records a demo result for an owned booking. The server controls allowed transitions. The final route and method choices are subject to repository reconciliation.

## 11. Database Design

The following are logical entities, not observed schema. Names are descriptive suggestions. A shared entity may map to several existing tables. Do not create duplicate tables or modify applied migrations to match this model. The historical estimate of 20–28 tables is neither a target nor a verified existing count.

| Proposed logical table | PK and FKs | Important fields and relationships |
|---|---|---|
| Users | PK user_id | Name, email, password hash, role, active state. Traveler, provider, and Admin identity. |
| ProviderProfiles | PK provider_id; FK user_id → Users | Profile details, verification status, decision date. One provider profile per applicable user. |
| Services | PK service_id; FK provider_id → ProviderProfiles | Category, title, description, price, publication status, active state, and location coordinates. One provider owns many services. |
| ServicePhotos | PK photo_id; FK service_id → Services | Media reference and display order. A service can have several photos. |
| AccommodationUnits | PK unit_id; FK service_id → Services | Bookable unit, capacity, active state. Applicable to homestays. |
| RentalUnits | PK unit_id; FK service_id → Services | Vehicle or fleet unit reference, type, and active state. Applicable to bikes and bicycles. |
| ServiceAvailability | PK availability_id; FK service_id → Services | Start, end, capacity, availability state, and optional unit reference under the final model. Applies to each bookable category as needed. |
| Bookings | PK booking_id; FK traveler_user_id → Users; FK service_id → Services; optional FK availability_id → ServiceAvailability | Dates or slot, selected unit or participation quantity, quoted amount, currency, status, timestamps. Use approved service-specific model for allocations. |
| BookingStatusHistory | PK event_id; FK booking_id → Bookings; FK actor_user_id → Users | Prior and next state, timestamp, and permitted decision context. Proposed mapping to an existing audit or history mechanism where available. |
| BookingPayments | PK payment_id; FK booking_id → Bookings | Method, demo or unpaid state, optional display amount, timestamps. No real gateway, settlement, or refund fields are required for the MVP. |
| FeedPosts | PK post_id; FK author_user_id → Users | Content, visibility, creation time, and moderation state. |
| FeedLikes | PK like_id; FKs post_id → FeedPosts and user_id → Users | Unique post/user pair prevents duplicate likes. |
| FeedComments | PK comment_id; FKs post_id → FeedPosts and author_user_id → Users | Comment text and creation time. |

Store provider and listing review decisions using existing status or audit data where available. Use service and ownership keys to scope reads and enforce access. Validate foreign keys, uniqueness, status values, and availability constraints. Persist only the booking and payment data needed for the demonstration. Map coordinates and transient weather or itinerary responses do not require dedicated storage unless an approved implementation already provides it.

## 12. Business Model and Marketing Strategy

| Topic | Academic MVP position |
|---|---|
| Problem | Travelers can face fragmented discovery and booking information. Small local providers may lack a consistent online presence. Treat both as hypotheses to test. |
| Proposed solution | Offer one workflow for discovery, availability, booking requests, and basic trip planning. Give local providers a straightforward publishing and management flow. |
| Target customers | Domestic and international travelers planning local stays, transport rentals, activities, or guided visits in Sri Lanka. |
| Provider segments | Homestay operators, bike and bicycle rental operators, adventure activity providers, and local guides. |
| Value proposition | Travelers can compare essential service information. Providers gain a discovery channel and a manageable booking workflow. Admin verification provides a basic trust signal. |
| Revenue model and commission | Evaluate a per-completed-booking commission as a proposal. Illustrative scenario only: 5% of a hypothetical LKR 10,000 completed booking would be LKR 500. No percentage is an approved live fee. Simulated payments generate no actual revenue. |
| Marketing strategy | Test destination-oriented content, social channels, provider partnerships, and demonstrations with a focused Sri Lankan pilot. |
| Customer acquisition | Recruit an initial provider cohort, demonstrate listing setup, invite travelers to try the flow, and collect feedback on whether they can complete bookings. |
| Competitor analysis | Compare public booking platforms and local alternatives on coverage, price transparency, provider onboarding, booking friction, and trust. Record sources, dates, and evidence before asserting advantages. |
| Future expansion | Evaluate additional destinations and provider capacity after the pilot. Reassess unit economics and operational readiness before considering commercial payments. |

## 13. Out-of-Scope Features

- Real payment gateway processing, live PayHere payments, financial settlements, automatic provider payouts, and real refunds.
- Complex financial reporting, advanced analytics, and loyalty points.
- Advanced route optimization and autonomous AI agents.
- Multiple specialized Admin roles, complex promotion engines, and unnecessary enterprise workflows.

Preserve existing advanced code, APIs, and database tables unless a separately approved change is necessary. Exclusion from the MVP does not authorize deletion, reset, or disabling an existing production service.

## 14. AI Coding Agent Development Guidelines

- AG-01 Read this Master Requirements document before development.
- AG-02 Inspect the relevant repository and current Git status.
- AG-03 Classify requirements as complete, partial, or missing using evidence.
- AG-04 Reuse compatible existing implementation and shared infrastructure.
- AG-05 Avoid duplicate endpoints and tables.
- AG-06 Develop only approved academic MVP functionality.
- AG-07 Follow repository architecture and coding conventions.
- AG-08 Write and run relevant tests in approved isolated environments.
- AG-09 Never modify the main CeylonGoDB without explicit approval.
- AG-10 Request approval before migrations, destructive actions, or major architectural changes.
- AG-11 Preserve unrelated Git changes and previously completed work.
- AG-12 Report completed work, test evidence, blockers, and remaining tasks.

## 15. Testing and Acceptance Criteria

| Acceptance group | Minimum evidence |
|---|---|
| TR-01 and SP-01 accounts | Valid registration and login work, authorization is enforced, owner profile actions work, and logout follows the agreed contract. Provider verification state is visible. |
| TR-02 through TR-05 bookings | Demonstrate each service's browse, details, availability, request, decision, and history. Reject wrong-owner actions, invalid transitions, and requests that would overbook. |
| Payment | Demonstrate simulated success and failure with no live gateway call or real payment data. Confirm a pay-at-location booking while its payment remains unpaid. |
| TR-06 planning | Valid destination, duration, and budget produce a usable day-by-day itinerary. Invalid input or unavailable integration yields a clear result. |
| TR-07 social feed | Create, read, like, comment, and delete own post. Reject deletion of another author's post by an unauthorized traveler. |
| TR-08 maps | Display an approved service marker and open directions for the selected location. |
| TR-09 weather | Display temperature, conditions, and a basic forecast for a valid destination. Show an unavailable state on provider failure. |
| SP-02 and SP-03 services | Submit a listing and manage owned listings, inventory, and bookings. Deny cross-provider actions. |
| AD-01 and AD-02 administration | Demonstrate provider and listing decisions, account and listing management, booking view, and moderation with the single Admin role. Deny non-Admin access. |
| Cross-cutting quality | Verify privacy boundaries, expected error responses, booking consistency under competing requests, and responsive presentation in the demonstration. |

Use synthetic records and a verified isolated target. Record requirement ID, test, result, evidence, and open blocker. Distinguish mocked integrations from live integration acceptance. Academic sign-off requires evidence for each of the 14 modules and both payment options. Record unresolved assumptions and approved deviations before signing off.

## 16. GitHub Repository and Change Control

- GC-01 Backend, Frontend, and Flutter are independent Git repositories. The shared local copy belongs under CeylonGo/docs/academic-mvp/. The local parent folder is a workspace convention, not a distribution mechanism.
- GC-02 Proposed distribution: publish the approved pair in one team-accessible Git repository at a pinned tag or commit. Each application repository can reference the same revision from its README and AGENTS guidance after approval.
- GC-03 Propose scope changes with the affected requirement IDs, justification, impact on workflows, and acceptance tests. Obtain approval, version both document formats together, and preserve the last approved revision.
- GC-04 Review Git status before editing, keep work scoped, preserve unrelated changes, and use code review. Do not commit, push, or merge without authorization. Do not modify the main database or run migrations without separate authorization.
- GC-05 Open design decisions: cancellation and completion authority, inventory and date boundaries per service, detailed Admin management operations, exact route-to-code and schema mapping, external service selection and quotas, and numeric performance criteria. Document decisions explicitly before expanding or implementing those details.
