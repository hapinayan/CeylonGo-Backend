# CeylonGo Backend Development Instructions

## Project and baseline
- Backend: Python FastAPI, SQLAlchemy, and Microsoft SQL Server.
- Primary database: `CeylonGoDB` on `.\SQLEXPRESS`.
- Clients: Angular 21 and Flutter 3.
- User-provided database baseline: 88 tables and 32 applied migrations. Do not treat this as a fresh live database verification.
- Repository migration inventory is `migrations/001_*.sql` through `032_*.sql`.

## Architecture and conventions
- Follow existing repository architecture and nearby coding conventions. Reuse shared services, models, authentication, authorization, database dependencies, and infrastructure.
- Application entry point: `app/main.py`. Existing areas include `app/routers`, `app/api`, `app/models`, `app/schemas`, `app/services`, `app/core`, and `app/database`. Follow the relevant module's established placement rather than reorganizing these areas.
- Never rewrite completed modules unnecessarily or expand task scope without instruction.
- Dependencies are pinned in `requirements.txt`; `.flake8` specifies an 88-character line limit and ignores E203.
- Consult `docs/DATABASE_MIGRATION_GUIDE.md` and existing module documentation when relevant. Older examples in documentation may predate the current migration inventory.

## Database and migration safety
- Never modify applied migrations 001–032, including cosmetic edits that change their checksums.
- The next migration number is 033, subject to explicit approval. Do not create or apply it without approval.
- Use the existing migration infrastructure in `scripts/migrate.py` for authorized migration work.
- Never drop tables or delete existing database records.
- Never run write-based tests against the primary database. Use isolated test environments only.
- Do not assume migration inspection commands are read-only: inspect their behavior before execution; the runner can initialize migration tracking infrastructure.
- Preserve the fail-closed checks in `app/core/database_guard.py`. The existing approved SQL Server integration database is `CeylonGoDB_IntegrationTest`; verify the actual target before any integration writes.

## Testing
- Tests use pytest. `tests/conftest.py` sets test JWT environment values but does not globally isolate database access.
- Inspect each selected suite's database setup before running it. Override FastAPI database dependencies and use mocks or isolated databases as appropriate.
- Existing isolated SQLite harnesses include `tests/run_test_backend.py` and `tests/isolated_backend_e2e.py`; inspect their setup and temporary-file behavior before reuse.
- SQL Server integration runners are under `scripts/`, including `run_sqlserver_integration_tests.py` and `run_phase12_sqlserver_integration.py`; verify their guards and target configuration before use.
- Use bounded test execution with an explicit wall-clock timeout. Stop and report hanging tests, including the command, timeout, and last useful output. Do not leave test servers or child processes running.
- Run focused checks appropriate to the change. Documentation-only initialization does not require application tests or a database connection.

## Workspace and secrets
- Inspect Git status before editing and preserve unrelated local changes, untracked files, and documentation.
- Never expose credentials or secrets. Do not print `.env` contents or credential-bearing connection strings in output, logs, or documentation.
- Never commit, push, or merge without explicit approval.

## Current work boundary
- Homestay backend is completed for academic scope; preserve completed behavior.
- The next task is Local Tour Guide Module 05. An initial audit has already been completed; the supplied baseline is nine dedicated database tables and four provider profile API operations.
- Do not repeat the full Tour Guide audit or begin implementation until the user gives the next development instruction.

## Academic MVP V1.1 Requirements

1. Read [the approved Academic MVP V1.1 Master Requirements](docs/academic-mvp/CEYLONGO_ACADEMIC_MVP_MASTER_REQUIREMENTS_V1.1.md) before starting development.
2. Use the approved Academic MVP scope as the feature boundary.
3. Inspect existing code and its current behavior before implementing functionality.
4. Reuse existing APIs and database tables where they meet the requirement.
5. Avoid duplicate implementation.
6. Develop only approved features.
7. Do not modify production or the main SQL Server database without explicit approval.
8. Preserve existing Git changes and unrelated work.
9. Run relevant tests in an approved environment.
10. Report complete, partial, and missing requirements with the available evidence.
