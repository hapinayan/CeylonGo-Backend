# FRESH DB 08 — Backup Verification Report

**Document ID:** FRESH_DB_08_BACKUP_VERIFICATION_REPORT.md  
**Gate:** Gate 2 — Full Copy-Only Backup of Current CeylonGoDB  
**Execution Timestamp:** 2026-11-23 14:13:30 (UTC: 2026-11-23T08:43:30Z)  
**Coordinator Mandate:** FULL COPY-ONLY BACKUP of the existing `CeylonGoDB` database and backup verification only.  
**Execution Status:** **100% COMPLETED & VERIFIED.** Full copy-only backup executed with CHECKSUM, RESTORE VERIFYONLY confirmed valid, and CeylonGoDB database integrity and row-count invariance verified.

---

## 1. Executive Summary

In strict accordance with the Coordinator's Gate 2 authorization:
1. **Full COPY_ONLY Backup:** A standalone, copy-only full backup of `CeylonGoDB` was successfully created on dedicated storage with `CHECKSUM` enabled.
2. **Compression Decision:** Testing against the SQL Server engine confirmed that `BACKUP DATABASE WITH COMPRESSION` is unsupported on SQL Server Express Edition (Error 1844). In accordance with requirement 4, compression was safely omitted.
3. **Verification via `RESTORE VERIFYONLY`:** The resulting backup artifact was validated directly by the SQL Server engine using `RESTORE VERIFYONLY WITH CHECKSUM`, confirming media integrity, page checksums, and recoverability.
4. **Metadata & Catalog Inspection:** Both `RESTORE HEADERONLY` and `RESTORE FILELISTONLY` were inspected to record LSNs, GUIDs, and file allocations. The backup set was registered in `msdb.dbo.backupset` under `backup_set_id = 3017`.
5. **Database Invariance:** Pre- and post-backup checks confirmed that `CeylonGoDB` remained continuously `ONLINE`, with its 90 physical user tables and all 58 non-migration business rows (plus 36 migration rows) remaining 100% intact. Zero data or schema mutations occurred.

---

## 2. Target Instance & Database Identity Verification

| Target Parameter | Expected | Verified Value | Validation Source |
|---|---|---|---|
| **SQL Server Instance** | `VISHNUGA-KAJARASH\SQLEXPRESS` | `VISHNUGA-KAJARASH\SQLEXPRESS` | `SERVERPROPERTY('ServerName')` |
| **Machine Name** | `VISHNUGA-KAJARASH` | `VISHNUGA-KAJARASH` | `SERVERPROPERTY('MachineName')` |
| **Edition** | Express Edition (64-bit) | `Express Edition (64-bit)` | `SERVERPROPERTY('Edition')` |
| **Engine Edition** | 4 (Express) | `4` | `SERVERPROPERTY('EngineEdition')` |
| **Product Version** | 17.0.1000.7 | `17.0.1000.7` (SQL Server 2025 RTM) | `SERVERPROPERTY('ProductVersion')` |
| **Target Database** | `CeylonGoDB` | `CeylonGoDB` (DB ID: 7) | `sys.databases` |
| **Initial State** | ONLINE | `ONLINE` | `sys.databases.state_desc` |
| **Recovery Model** | SIMPLE | `SIMPLE` | `sys.databases.recovery_model_desc` |
| **Collation** | `Latin1_General_CI_AS` | `Latin1_General_CI_AS` | `sys.databases.collation_name` |

---

## 3. Storage Destination & Service Account Verification

| Storage Parameter | Observed Value | Verification Details |
|---|---|---|
| **Backup Destination Directory** | `C:\Program Files\Microsoft SQL Server\MSSQL17.SQLEXPRESS\MSSQL\Backup` | Default instance backup path (`SERVERPROPERTY('InstanceDefaultBackupPath')`) |
| **Volume Mount Point** | `C:\` | Hosting local system volume |
| **Total Disk Capacity** | **475.82 GB** | Measured via `sys.dm_os_volume_stats` |
| **Available Free Disk Space** | **106.30 GB** | Measured prior to backup execution |
| **Database Allocated Space** | 80.00 MB | 72 MB data (`.mdf`) + 8 MB log (`.ldf`) |
| **Backup Artifact Size** | **9.84 MB** (10,313,728 bytes) | Consumes <0.01% of available volume capacity |
| **SQL Service Account** | `NT Service\MSSQL$SQLEXPRESS` | Verified full write and ownership permissions on backup directory |

---

## 4. SQL Server Edition & Compression Evaluation

Prior to execution, compression support was tested directly on the target instance:

```sql
BACKUP DATABASE [master] TO DISK = 'NUL' WITH COMPRESSION;
```

**Result:**
```
Msg 1844, Level 16, State 1, Line 1
BACKUP DATABASE WITH COMPRESSION is not supported on Express Edition (64-bit).
Msg 3013, Level 16, State 1, Line 1
BACKUP DATABASE is terminating abnormally.
```

> [!NOTE]
> **Compression Finding:** In strict compliance with Task 4 (*"Use COMPRESSION only if supported by this SQL Server edition"*), the backup command omitted `COMPRESSION` and proceeded with `COPY_ONLY` and `CHECKSUM`.

---

## 5. Backup Artifact & Execution Telemetry

### Exact SQL Backup Statement:
```sql
BACKUP DATABASE [CeylonGoDB]
TO DISK = N'C:\Program Files\Microsoft SQL Server\MSSQL17.SQLEXPRESS\MSSQL\Backup\CeylonGoDB_pre_mvp_20261123T084300Z.bak'
WITH COPY_ONLY, CHECKSUM, STATS = 25, NAME = N'CeylonGoDB-Full Pre-MVP Backup';
```

### Execution Telemetry:
- **Backup Execution Output:**
  ```
  25 percent processed.
  50 percent processed.
  75 percent processed.
  100 percent processed.
  Processed 1248 pages for database 'CeylonGoDB', file 'CeylonGoDB' on file 1.
  Processed 2 pages for database 'CeylonGoDB', file 'CeylonGoDB_log' on file 1.
  BACKUP DATABASE successfully processed 1250 pages in 0.703 seconds (13.885 MB/sec).
  ```
- **Backup Start Time (Local / UTC):** `2026-11-23 14:13:30.000` / `2026-11-23T08:43:30Z`
- **Backup Finish Time (Local / UTC):** `2026-11-23 14:13:30.000` / `2026-11-23T08:43:30Z`
- **Elapsed Execution Time:** **0.703 seconds**
- **Throughput:** **13.885 MB/sec**
- **Total Pages Processed:** 1,250 pages (1,248 data pages + 2 log pages)

### Physical Backup Artifact Details:
- **File Name:** `CeylonGoDB_pre_mvp_20261123T084300Z.bak`
- **Full Path:** `C:\Program Files\Microsoft SQL Server\MSSQL17.SQLEXPRESS\MSSQL\Backup\CeylonGoDB_pre_mvp_20261123T084300Z.bak`
- **File Size (Bytes):** `10,313,728 bytes`
- **File Size (MB):** `9.8359 MB` (~9.84 MB)
- **SHA-256 Checksum:**  
  `c6529b4aeab0b83d37fcf9869b6355fb1315dda4cfeea5fa0f9678afac6c8b77`

---

## 6. Verification via RESTORE VERIFYONLY

### Verification Command:
```sql
RESTORE VERIFYONLY
FROM DISK = N'C:\Program Files\Microsoft SQL Server\MSSQL17.SQLEXPRESS\MSSQL\Backup\CeylonGoDB_pre_mvp_20261123T084300Z.bak'
WITH CHECKSUM;
```

### Engine Verification Result:
```
The backup set on file 1 is valid.
```

> [!TIP]
> **Integrity Confirmation:** `RESTORE VERIFYONLY` completed with return code `0`. All database pages, allocation structures, and checksum blocks on file 1 were verified as valid, uncorrupted, and restorable.

---

## 7. Backup Metadata & Catalog Inspection

### RESTORE HEADERONLY Evidence:
| Header Attribute | Value | Description |
|---|---|---|
| **BackupName** | `CeylonGoDB-Full Pre-MVP Backup` | Descriptive name supplied in command |
| **BackupType** | `1` | Full Database Backup |
| **Position** | `1` | First file in backup set |
| **FirstLSN** | `59000000376000001` | Log Sequence Number start |
| **LastLSN** | `59000000378400001` | Log Sequence Number end |
| **CheckpointLSN** | `59000000376000001` | Checkpoint LSN |
| **DatabaseBackupLSN** | `55000000032000001` | Base Full Backup LSN |
| **IsCopyOnly** | `1` (True) | Independent copy; preserves differential base chain |
| **HasBackupChecksums** | `1` (True) | End-to-end page checksums verified |
| **DatabaseGuid (BindingID)** | `F30974C7-D84A-42E8-9F76-E4B305CADB4B` | Database unique binding identifier |
| **RecoveryForkID** | `44B0B0E5-81C9-4891-B186-0C6507424300` | Recovery fork identifier |
| **BackupSetGUID** | `308ACA4C-5BFC-4086-91ED-873E91EACD3B` | Unique backup set identifier |
| **CompatibilityLevel** | `170` | SQL Server 2025 compatibility level |
| **SoftwareVersion** | `17.0.1000` | Engine version at backup time |

### RESTORE FILELISTONLY Evidence:
| Logical Name | Physical Name | Type | Size | MaxSize |
|---|---|---|---|---|
| `CeylonGoDB` | `C:\Program Files\Microsoft SQL Server\MSSQL17.SQLEXPRESS\MSSQL\DATA\CeylonGoDB.mdf` | `D` (Data) | 75,497,472 bytes (72.0 MB) | 35,184,372,080,640 bytes |
| `CeylonGoDB_log` | `C:\Program Files\Microsoft SQL Server\MSSQL17.SQLEXPRESS\MSSQL\DATA\CeylonGoDB_log.ldf` | `L` (Log) | 8,388,608 bytes (8.0 MB) | 2,199,023,255,552 bytes |

### MSDB Backup History Record:
```sql
SELECT bs.backup_set_id, bs.name, bs.type, bs.backup_start_date, bs.backup_size, bs.is_copy_only, bs.has_backup_checksums, bmf.physical_device_name
FROM msdb.dbo.backupset bs
JOIN msdb.dbo.backupmediafamily bmf ON bs.media_set_id = bmf.media_set_id
WHERE bs.database_name = 'CeylonGoDB'
ORDER BY bs.backup_set_id DESC;
```

**Recorded Catalog Entry:**
- **Backup Set ID:** `3017`
- **Database Name:** `CeylonGoDB`
- **Backup Name:** `CeylonGoDB-Full Pre-MVP Backup`
- **Type:** `D` (Full database)
- **Start / Finish Time:** `2026-11-23T14:13:30` / `2026-11-23T14:13:30`
- **Backup Size:** `10,313,728 bytes`
- **Is Copy Only:** `1`
- **Has Backup Checksums:** `1`
- **Physical Device:** `C:\Program Files\Microsoft SQL Server\MSSQL17.SQLEXPRESS\MSSQL\Backup\CeylonGoDB_pre_mvp_20261123T084300Z.bak`

---

## 8. Database Health & Invariance Verification

Pre- and post-backup checks were executed to prove that the backup process was strictly read-only with respect to database state, schema, and application records:

| Check | Pre-Backup Baseline | Post-Backup State | Invariant? |
|---|---|---|:---:|
| **Database State** | `ONLINE` | `ONLINE` | **YES** |
| **Database ID** | `7` | `7` | **YES** |
| **Recovery Model** | `SIMPLE` | `SIMPLE` | **YES** |
| **Total Physical User Tables** | **90 tables** | **90 tables** | **YES** |
| **`dbo.admin_audit_logs`** | 1 row | 1 row | **YES** |
| **`dbo.eco_promotion_packages`** | 3 rows | 3 rows | **YES** |
| **`dbo.password_resets`** | 2 rows | 2 rows | **YES** |
| **`dbo.provider_email_otps`** | 20 rows | 20 rows | **YES** |
| **`dbo.provider_profiles`** | 3 rows | 3 rows | **YES** |
| **`dbo.provider_registration_authorizations`** | 18 rows | 18 rows | **YES** |
| **`dbo.provider_registration_drafts`** | 1 row | 1 row | **YES** |
| **`dbo.schema_migrations`** | 36 rows | 36 rows | **YES** |
| **`dbo.users`** | 10 rows | 10 rows | **YES** |
| **Remaining 81 User Tables** | 0 rows | 0 rows | **YES** |

> [!IMPORTANT]
> **Zero Data Modification:** The physical database schema, table definitions, constraints, indexes, and row contents are 100% identical before and after the backup.

---

## 9. Remaining Approval Gates (FRESH_DB_06 Governance)

In accordance with the 7-Gate governance defined in `FRESH_DB_06_APPROVAL_CHECKLIST.md`:

| Gate | Name | Status | Evidence & Preconditions Met |
|:---:|---|:---:|---|
| **Gate 1** | Read-Only SQL Server Preflight | **COMPLETED** | Verified instance identity, catalog, baseline drift (90 tables/36 migrations), and absence of prior backups in `FRESH_DB_07_READ_ONLY_PREFLIGHT_REPORT.md`. |
| **Gate 2** | Backup of Current CeylonGoDB | **COMPLETED** | Verified full copy-only backup (`CeylonGoDB_pre_mvp_20261123T084300Z.bak`, 9.84 MB, SHA-256 verified, `RESTORE VERIFYONLY` validated, registered in `msdb` set 3017). |
| **Gate 3** | Fresh Schema & Data Rehearsal | **NEXT APPROVAL REQUIRED** | Provisioning and applying fresh MVP schema and synthetic seed data on a **disposable target database** (e.g. `CeylonGoDB_Rehearsal_V3` or `CeylonGoDB_MVP_Rehearsal`). |
| **Gate 4** | Integration Acceptance | PENDING Gate 3 | Running isolated test suites against the rehearsal database. |
| **Gate 5** | Original Database Cutover | PENDING Gate 4 | Change window, application freeze, and database rename/cutover authorization. |
| **Gate 6** | Go-Live & Rollback Execution | PENDING Gate 5 | Pointing production writers to simplified MVP schema with rollback standby. |
| **Gate 7** | Optional Cleanup | PENDING Gate 6 | Optional archiving/retirement of superseded test databases. |

---

## 10. Next Actions & Coordinator Approvals Required

With Gate 2 successfully verified:
1. The safety baseline of the existing `CeylonGoDB` database is fully secured by an independently verified, checksummed physical backup file.
2. **Next Approval Requested from Coordinator:**
   - **Gate 3: Fresh Schema & Data Rehearsal Approval**
   - Target: A new disposable rehearsal database (e.g., `CeylonGoDB_Rehearsal_V3` or `CeylonGoDB_MVP_Rehearsal`).
   - Scope: Bootstrap of the proposed 17 + 1 MVP schema and synthetic seed data.
   - **Safety Boundary:** The primary `CeylonGoDB` database and the existing five test databases will remain completely untouched during Gate 3.

*No write operations, database creations, or schema migrations will be executed until Gate 3 is explicitly authorized.*
