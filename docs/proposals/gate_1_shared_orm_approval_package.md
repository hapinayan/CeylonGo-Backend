# Gate 1 Technical Review & Approval Package
## Shared ORM Model Alignment: `ActivityBookingParticipant`

**Document Version**: 1.0  
**Date**: 20 November 2026  
**Target Repository**: `CeylonGo-Backend` (`develop` branch)  
**Target File**: `app/models/activity_booking.py`  
**Target Class**: `ActivityBookingParticipant` (lines 144–198)  
**Responsible Approver**: Backend Team Leader  
**Status**: **PENDING APPROVAL**  
**Recommendation**: **READY FOR APPROVAL**  

---

## 1. Issue Description & Problem Statement

The shared SQLAlchemy model `ActivityBookingParticipant` in `app/models/activity_booking.py` diverges significantly from the physical Microsoft SQL Server database schema established in `migrations/025_extend_activities_eco_module.sql`.

### Specific Discrepancies
1. **Phantom Column `id_or_passport`**:
   - The ORM model defines `id_or_passport: Mapped[str | None] = mapped_column(Unicode(50), nullable=True)`.
   - **Physical Schema Truth**: The physical table `dbo.activity_booking_participants` has **no column** named `id_or_passport`.
   - **Runtime Consequence**: Any query attempting to load participants via standard ORM relationship (e.g., `booking.participants_list` or `db.query(ActivityBookingParticipant)`) executes `SELECT ... [id_or_passport] ...` and crashes in SQL Server with **Error 207: "Invalid column name 'id_or_passport'"**.
2. **Missing Physical Columns in ORM**:
   - The physical SQL Server table contains three operational columns that are completely missing from the ORM model:
     - `emergency_contact_name`: `NVARCHAR(150) NULL`
     - `emergency_contact_phone`: `NVARCHAR(30) NULL`
     - `waiver_signed`: `BIT NOT NULL DEFAULT ((0))`
3. **Column Length Mismatch on `special_requirements`**:
   - ORM defines `special_requirements` as `Unicode(500)`.
   - Physical table defines `special_requirements` as `NVARCHAR(1000)`.
4. **Check Constraint Discrepancy on `age`**:
   - ORM defines check constraint `"age IS NULL OR age > 0"` named `CK_activity_participants_age`.
   - Physical table defines constraint `([age] IS NULL OR [age]>=(0))` named `CK_activity_booking_participants_age`.

---

## 2. Physical Database Schema Verification (Evidence)

A read-only query against the actual live Microsoft SQL Server instance (`CeylonGoDB`) yields the following physical table definition:

### Physical Columns in `dbo.activity_booking_participants`
```
+--------------------------+---------------+---------------+-------------+-----------------------+
| COLUMN_NAME              | DATA_TYPE     | MAX_LENGTH    | IS_NULLABLE | COLUMN_DEFAULT        |
+--------------------------+---------------+---------------+-------------+-----------------------+
| id                       | int           | NULL          | NO          | NULL (IDENTITY)       |
| activity_booking_id      | int           | NULL          | NO          | NULL (FK)             |
| full_name                | nvarchar      | 150           | NO          | NULL                  |
| age                      | int           | NULL          | YES         | NULL                  |
| emergency_contact_name   | nvarchar      | 150           | YES         | NULL                  |
| emergency_contact_phone  | nvarchar      | 30            | YES         | NULL                  |
| special_requirements     | nvarchar      | 1000          | YES         | NULL                  |
| waiver_signed            | bit           | NULL          | NO          | ((0))                 |
| created_at               | datetime2     | NULL          | NO          | (sysutcdatetime())    |
+--------------------------+---------------+---------------+-------------+-----------------------+
```

### Physical Constraints in `dbo.activity_booking_participants`
- `PK_activity_booking_participants`: Primary key on `id`.
- `FK_activity_booking_participants_booking`: Foreign key on `activity_booking_id` -> `dbo.activity_bookings(id)`.
- `CK_activity_booking_participants_age`: Check clause `([age] IS NULL OR [age]>=(0))`.

---

## 3. Codebase Reference & Impact Analysis

### A. Module 03 (Standard Activities) Usage
- **Findings**:
  - Module 03 uses `participants` as an integer count on `ActivityBooking` for capacity reservation (`app/services/activity_capacity.py`).
  - Module 03 **never** references `id_or_passport`.
  - Module 03 **never** instantiates or queries `ActivityBookingParticipant` directly.
- **Impact**: Zero negative impact on Module 03. Aligning the ORM model to the physical schema fixes a latent bug and prevents crashes if Module 03 ever lazy-loads `booking.participants_list`.

### B. Module 04 (Eco-Tourism) Usage
- **Findings**:
  - In [app/routers/eco_bookings.py](file:///c:/Users/komaruthan/Desktop/project%203/CeylonGo-Backend/app/routers/eco_bookings.py) and [app/services/eco_booking_service.py](file:///c:/Users/komaruthan/Desktop/project%203/CeylonGo-Backend/app/services/eco_booking_service.py), Module 04 currently employs raw Core table reflection and explicit column selection (`_fetch_participants_by_booking_ids`) to safely circumvent the phantom `id_or_passport` column.
- **Impact**: Patching the ORM model resolves the root cause, enabling clean, idiomatic SQLAlchemy ORM relationship access across the entire repository.

---

## 4. Proposed Patch Diff (Do NOT Apply Until Sign-Off)

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
         nullable=True,
     )
+
+    waiver_signed: Mapped[bool] = mapped_column(
+        Boolean,
+        default=False,
+        nullable=False,
+    )
```

---

## 5. Risk Assessment & Test Requirements

| Assessment Metric | Evaluation |
|---|---|
| **Risk Level** | **VERY LOW**. This is an ORM alignment patch bringing Python declarations into 100% agreement with physical database columns already deployed in SQL Server since Migration 025. |
| **Database Migration Required?** | **NO**. Zero SQL migrations or physical schema changes required. |
| **Breaking Changes** | **NONE**. No existing code reads or writes `id_or_passport`. |
| **Pre-Patch Test Suite** | 487 passing regression tests on SQLite doubles. |
| **Post-Patch Verification Plan** | 1. Run `pytest -k "eco"` (all 487+ tests must pass).<br>2. Run `pytest tests/test_eco_bookings_router_staged.py` (all 64 tests must pass).<br>3. Verify `ActivityBooking.participants_list` loads via ORM on SQL Server without Error 207. |
| **Rollback Procedure** | Single git command: `git checkout app/models/activity_booking.py`. |

---

## 6. Team Leader Approval Checklist

- [ ] Confirm physical columns of `dbo.activity_booking_participants` match the proposed ORM fields.
- [ ] Confirm `id_or_passport` is not used in any Module 03 or Module 04 business logic.
- [ ] Approve the 15-line diff in `app/models/activity_booking.py`.
- [ ] Sign off on Gate 1.
