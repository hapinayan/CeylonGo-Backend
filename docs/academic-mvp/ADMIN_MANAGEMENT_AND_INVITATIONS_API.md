# Academic MVP Admin management and invitations

The Academic MVP supports two administrative roles. **Admin** retains the existing verification, management, wallet and commission functions. **Super Admin** also manages administrator invitations and account status. The existing traveler and provider roles are unaffected.

## V1.0.10 schema

The [migration](../../scripts/mvp_upgrades/1.0.10_admin_roles_and_invitations.sql) adds `super_admin` to the `dbo.users.role` check and creates `dbo.admin_invitations`. The invitation role check allows `admin` and `super_admin`. The table has a `BIGINT` identity primary key, a nullable foreign key from `invited_by_id` to `dbo.users.id`, a unique token hash, and indexes on `(email, status)` and `token_hash`. The expected upgrade is from V1.0.9 with 32 tables to V1.0.10 with 33 tables.

## API

| Method and path | Authorization |
| --- | --- |
| `POST /api/v1/admin/auth/login` | Active Admin or Super Admin credentials |
| `GET /api/v1/admin/auth/me` | Active Admin or Super Admin |
| `GET /api/v1/admin/management/admins` | Active Super Admin |
| `GET /api/v1/admin/management/invitations` | Active Super Admin |
| `POST /api/v1/admin/management/invitations` | Active Super Admin |
| `POST /api/v1/admin/management/invitations/{id}/resend` | Active Super Admin |
| `POST /api/v1/admin/management/invitations/{id}/revoke` | Active Super Admin |
| `POST /api/v1/admin/management/admins/{id}/status` | Active Super Admin |
| `GET /api/v1/admin/auth/invitations/verify?token=…` | Valid invitation from an active Super Admin |
| `POST /api/v1/admin/auth/invitations/activate` | Valid invitation from an active Super Admin |

Invitation creation accepts `email`, `full_name`, `role` (`admin` or `super_admin`), and optional `department` and `invitation_note`. Configure SMTP and set `ADMIN_INVITATION_ACTIVATION_URL` to the Angular Admin activation page over HTTPS, for example `https://admin.example.com/admin/activate`. Creation and resend report `delivery_status` as `sent`, `failed` or `unconfigured`. The activation link is delivered by email and expires after 48 hours. The database stores only its SHA-256 hash. Resending invalidates the prior link and records the authorizing Super Admin.

The invitee submits `token` and `password`. Activation uses the role assigned by the issuing Super Admin, rejects any existing account with the same email, and consumes the invitation in the same transaction that creates the account. The status endpoint accepts `{"is_active": false}` or `{"is_active": true}` for existing administrators. It protects the last active Super Admin and blocks self-deactivation. A status change revokes existing sessions for that account.

## Provision the first Super Admin after the approved migration

1. Obtain separate written authorization for the account promotion. Record the approver, the verified owner, the exact account ID and normalized email, and the maintenance window. Pause application writes, verify a full backup with checksum, and confirm that `CeylonGoDB` on the intended SQL Server instance has applied V1.0.10.
2. Have an authorized DBA start a transaction with `XACT_ABORT ON`. Lock `dbo.users` for the duration of the transaction, confirm there are zero active Super Admins, and verify the approved ID and email identify one active `admin`. Roll back if either condition fails.
3. Update just that account, guarded by its ID, email, active flag and current `admin` role. Set `role = 'super_admin'`, increment `auth_version` and `credential_version`, and update `updated_at` in UTC. Confirm exactly one row was affected and that exactly one active Super Admin exists before committing. Record the outcome in the change record.
4. Have the account owner sign in again through the Admin Website and confirm that the profile identifies them as `super_admin` and that they can open Admin Management. Verify normal Admin operational access and restore application traffic. Create subsequent Super Admin accounts through the invitation workflow.
