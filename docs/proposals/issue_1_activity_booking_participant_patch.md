# Shared ORM Model Correction Proposal: ActivityBookingParticipant (Issue 1)

**Target File**: `app/models/activity_booking.py`  
**Target Class**: `ActivityBookingParticipant` ([L144-L198](file:///c:/Users/komaruthan/Desktop/project%203/CeylonGo-Backend/app/models/activity_booking.py#L144-L198))  
**Audience**: Team Leader (Review & Approval Required)  
**Status**: STAGED PROPOSAL — Model remains unmodified until approval is granted.

---

## 1. Background & Problem Statement

In `migrations/025_extend_activities_eco_module.sql` (Batch 5), the physical database table `dbo.activity_booking_participants` was created with the following columns:
```sql
CREATE TABLE dbo.activity_booking_participants (
    id INT IDENTITY(1,1) NOT NULL,
    activity_booking_id INT NOT NULL,
    full_name NVARCHAR(150) NOT NULL,
    age INT NULL,
    emergency_contact_name NVARCHAR(150) NULL,
    emergency_contact_phone NVARCHAR(30) NULL,
    special_requirements NVARCHAR(1000) NULL,
    waiver_signed BIT NOT NULL DEFAULT 0,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    ...
);
```

However, the SQLAlchemy ORM model `ActivityBookingParticipant` in `app/models/activity_booking.py`:
1. Mapped a nonexistent column `id_or_passport: Mapped[str | None] = mapped_column(Unicode(50), nullable=True)`.
   - Any query attempting to load participants via standard ORM relationship (`booking.participants_list`) executes `SELECT ... id_or_passport ...` and fails in SQL Server with **Error 207: "Invalid column name 'id_or_passport'"**.
2. Omitted the physical database columns `emergency_contact_name`, `emergency_contact_phone`, and `waiver_signed`.
3. Declared `special_requirements` as `Unicode(500)` instead of `Unicode(1000)` (physical column is `NVARCHAR(1000)`).
4. Declared check constraint `age IS NULL OR age > 0`, whereas physical SQL Server check constraint is `age IS NULL OR age >= 0`.

---

## 2. Proposed Diff / Patch

```diff
--- a/app/models/activity_booking.py
+++ b/app/models/activity_booking.py
@@ -146,8 +146,8 @@ class ActivityBookingParticipant(Base):
 
     __table_args__ = (
         CheckConstraint(
-            "age IS NULL OR age > 0",
-            name="CK_activity_participants_age",
+            "age IS NULL OR age >= 0",
+            name="CK_activity_booking_participants_age",
         ),
     )
 
@@ -169,17 +169,27 @@ class ActivityBookingParticipant(Base):
         nullable=False,
     )
 
-    id_or_passport: Mapped[str | None] = mapped_column(
-        Unicode(50),
-        nullable=True,
-    )
-
     age: Mapped[int | None] = mapped_column(
         Integer,
         nullable=True,
     )
 
+    emergency_contact_name: Mapped[str | None] = mapped_column(
+        Unicode(150),
+        nullable=True,
+    )
+
+    emergency_contact_phone: Mapped[str | None] = mapped_column(
+        Unicode(30),
+        nullable=True,
+    )
+
     special_requirements: Mapped[str | None] = mapped_column(
-        Unicode(500),
+        Unicode(1000),
+        nullable=True,
+    )
+
+    waiver_signed: Mapped[bool] = mapped_column(
+        Boolean,
+        default=False,
+        nullable=False,
+    )
```

---

## 3. Impact Analysis & Backward Compatibility

1. **Module 03 (Standard Activities)**:
   - Module 03 currently uses `participants` count on `ActivityBooking` and does not rely on `id_or_passport`.
   - Aligning the ORM model to match the physical table restores standard relationship loading (`booking.participants_list`) across both Module 03 and Module 04 without triggering Error 207.
2. **Zero Migration Required**:
   - The physical table already contains `emergency_contact_name`, `emergency_contact_phone`, and `waiver_signed`. This is strictly an ORM definition fix.
3. **Approval Request**:
   - Team Leader approval is requested to apply this 15-line diff to `app/models/activity_booking.py`.
