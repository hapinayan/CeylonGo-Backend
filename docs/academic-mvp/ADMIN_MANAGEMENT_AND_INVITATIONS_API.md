# Admin Management & Invitations API (Academic MVP V1.0.10)

## Overview

The Admin Management and Invitations subsystem provides secure, role-based administration for the CeylonGo Academic MVP platform. It enforces:
1. **Super Admin Exclusivity:** Only users with `role = 'super_admin'` can create, view, resend, or revoke administrative invitations.
2. **Cryptographic Token Security:** High-entropy 256-bit activation tokens generated via `secrets.token_urlsafe(32)`. Raw tokens are never stored in the database; only SHA-256 digests (`token_hash`) are persisted.
3. **Single-Use and Time-Limited Lifecycle:** Invitations expire after 48-72 hours. Activation transitions the invitation status to `accepted` within the same transaction that provisions the user account, preventing replay attacks.
4. **Persistent SQL Server Storage (V1.0.10):** All invitation operations in production and live development require persistent database storage in `dbo.admin_invitations`. In-memory fallback is strictly restricted to isolated automated unit tests.

---

## Schema Version 1.0.10 Migration

### Additive Changes
- **Target Database:** `CeylonGoDB` / `CeylonGoDB_Academic_MVP_Seeder_Test`
- **Script Path:** `scripts/mvp_upgrades/1.0.10_admin_roles_and_invitations.sql`
- **SHA-256 Checksum:** `b87f2bbbec4f4e25ad0971e0e4f7d009fa49fcdf66e2d7aa5deda861f5c49c29`
- **Ledger Version:** `1.0.10`

### 1. Extended Role Check Constraint
The constraint `ck_users_role` on `dbo.users` is updated:
```sql
ALTER TABLE dbo.users DROP CONSTRAINT ck_users_role;
ALTER TABLE dbo.users ADD CONSTRAINT ck_users_role
CHECK (role IN ('traveler', 'provider', 'admin', 'super_admin'));
```

### 2. New Table: `dbo.admin_invitations`
Adds one table to the 32-table V1.0.9 baseline, bringing the unified table count to **33 Base Tables**.

```sql
CREATE TABLE dbo.admin_invitations (
    id INT IDENTITY(1,1) PRIMARY KEY,
    email NVARCHAR(255) NOT NULL,
    full_name NVARCHAR(255) NOT NULL,
    role NVARCHAR(50) NOT NULL,
    department NVARCHAR(100) NULL,
    token_hash NVARCHAR(64) NOT NULL UNIQUE,
    status NVARCHAR(20) NOT NULL DEFAULT 'pending',
    invitation_note NVARCHAR(MAX) NULL,
    invited_by_id INT NOT NULL,
    invited_by_email NVARCHAR(255) NULL,
    expires_at DATETIME2 NOT NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    accepted_at DATETIME2 NULL,
    revoked_at DATETIME2 NULL,
    CONSTRAINT fk_admin_invitations_invited_by FOREIGN KEY (invited_by_id) REFERENCES dbo.users(id),
    CONSTRAINT ck_admin_invitations_status CHECK (status IN ('pending', 'accepted', 'revoked', 'expired')),
    CONSTRAINT ck_admin_invitations_role CHECK (role IN ('super_admin', 'operations_admin', 'finance_admin', 'content_moderator', 'support_admin', 'admin'))
);

CREATE NONCLUSTERED INDEX ix_admin_invitations_token_hash ON dbo.admin_invitations(token_hash);
CREATE NONCLUSTERED INDEX ix_admin_invitations_email ON dbo.admin_invitations(email);
CREATE NONCLUSTERED INDEX ix_admin_invitations_status ON dbo.admin_invitations(status);
```

### Execution via Migration Runner
Dry-run verification:
```powershell
.venv\Scripts\python.exe scripts/bootstrap_mvp_schema.py CeylonGoDB upgrade --server '.\SQLEXPRESS' --target 1.0.10 --dry-run
```
Apply upgrade:
```powershell
.venv\Scripts\python.exe scripts/bootstrap_mvp_schema.py CeylonGoDB upgrade --server '.\SQLEXPRESS' --target 1.0.10 --yes
```

---

## API Endpoints

### 1. Issue Admin Invitation
- **Endpoint:** `POST /api/v1/admin/invitations`
- **Authorization:** `Bearer <JWT>` (Requires `super_admin`)
- **Request Body:**
  ```json
  {
    "email": "candidate@ceylongo.com",
    "full_name": "Operations Lead",
    "role": "operations_admin",
    "department": "Platform Operations",
    "invitation_note": "Approved for Academic MVP Operations"
  }
  ```
- **Response (201 Created):**
  ```json
  {
    "id": 1,
    "email": "candidate@ceylongo.com",
    "full_name": "Operations Lead",
    "role": "operations_admin",
    "department": "Platform Operations",
    "status": "pending",
    "invitation_note": "Approved for Academic MVP Operations",
    "invited_by_email": "superadmin@ceylongo.com",
    "expires_at": "2026-11-29T18:00:00",
    "created_at": "2026-11-27T18:00:00",
    "raw_token": null,
    "invitation_url": null,
    "email_delivered": true,
    "delivery_status": "sent"
  }
  ```
  *(Note: `raw_token` and `invitation_url` are strictly `null` in live environments to prevent token leakage).*

### 2. List Admin Invitations
- **Endpoint:** `GET /api/v1/admin/invitations?status_filter=all`
- **Authorization:** `Bearer <JWT>` (Requires `super_admin`)
- **Response (200 OK):** Array of `AdminInvitationResponse`.

### 3. Resend Invitation
- **Endpoint:** `POST /api/v1/admin/invitations/{id}/resend`
- **Authorization:** `Bearer <JWT>` (Requires `super_admin`)
- **Behavior:** Generates a new raw token and updates `token_hash` and `expires_at`. Invalidates previous token.

### 4. Revoke Invitation
- **Endpoint:** `POST /api/v1/admin/invitations/{id}/revoke`
- **Authorization:** `Bearer <JWT>` (Requires `super_admin`)
- **Behavior:** Sets `status = 'revoked'`, `revoked_at = SYSUTCDATETIME()`. Subsequent activation attempts return 400.

### 5. Verify Activation Token (Public / Invitee)
- **Endpoint:** `GET /api/v1/admin/auth/activate/verify?token=<raw_token>`
- **Authorization:** Public
- **Response (200 OK):**
  ```json
  {
    "valid": true,
    "email": "candidate@ceylongo.com",
    "full_name": "Operations Lead",
    "role": "operations_admin",
    "department": "Platform Operations",
    "expires_at": "2026-11-29T18:00:00"
  }
  ```

### 6. Activate Account (Public / Invitee)
- **Endpoint:** `POST /api/v1/admin/auth/activate`
- **Authorization:** Public
- **Request Body:**
  ```json
  {
    "token": "<raw_token>",
    "password": "SecurePassword123!@#"
  }
  ```
- **Response (200 OK):**
  ```json
  {
    "success": true,
    "message": "Administrator account successfully activated. You may now log in.",
    "email": "candidate@ceylongo.com"
  }
  ```
- **Replay Protection:** Re-submitting the same token yields `HTTP 400 Bad Request: "This invitation has already been accepted."`.
- **Expiration Protection:** Submitting an expired token yields `HTTP 410 Gone: "This invitation link has expired."`.
