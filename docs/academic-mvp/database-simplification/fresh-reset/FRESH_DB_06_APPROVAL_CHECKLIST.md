# Fresh DB 06 ? Independent approval gates

| Gate | Approval required | Evidence to present |
|---|---|---|
| 1 | Read-only SQL Server preflight | Named machine/instance, named restricted Windows identity, explicit database list, permitted master/msdb/catalog/DMV scope and owner attestation of dedicated non-production target. |
| 2 | Backup of current CeylonGoDB | Exact source, path, timestamp, available capacity, expected effects, permissions and recovery objective. Separate permission for media verification and each rehearsal restore, with exact target and no overwrite. |
| 3 | Fresh schema and data rehearsal | Approve source changes, exact bootstrap artifact hashes, disposable target, prerequisites, proposed users/rights and permitted synthetic writes. Provisioning, permissions and any existing-data export/transfer need separate explicit scopes. |
| 4 | Integration acceptance | Approve write-capable suites, target, synthetic scope and cleanup method. Verify full isolated success including FK, constraint, rollback and concurrent capacity behavior. Approve FastAPI/client contract and demo payment mode. |
| 5 | Original database cutover | Approved change window, exact source/candidate/archive identities and names, checked backup/restore, freezes and dependency handling, rollback procedure, legacy data access and decision authority. Explicit separate approval for each destructive or potentially disruptive step. |
| 6 | Go-live and rollback execution | Validate catalog ID, user mappings, checkout/runbook and acceptance results before enabling writers. Confirm rollback authorization for namespace reversal and any backup/restore action, and approve reconciliation if fresh writes exist. |
| 7 | Optional cleanup | Individually identify objects and database IDs, show owner approval, verified backup and retention, and demonstrate no unique data or active dependencies. Ask for separate explicit approval for each deletion. |

Open decisions: Adventure source and scope, motorized driver evidence, room/unit allocation and slot granularity, cancellation/completion actor rules, source of provider approval records, demo retry, planner persistence, single Admin mapping, and legacy data/financial-history access. No operation beyond offline planning is approved. **Next approval requested:** Gate 1 only, once the coordinator confirms the exact SQL instance, restricted Windows principal, and permitted catalog scope.
