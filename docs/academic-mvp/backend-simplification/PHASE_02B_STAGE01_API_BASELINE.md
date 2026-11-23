# Phase 02B Stage 01 API baseline

Application baseline: develop at `3bf9822f6be99780d0e25c8752ed263ac407c3c6`. Isolated branch: `feature/academic-mvp-backend-simplification`. Scope: [Academic MVP V1.1](../CEYLONGO_ACADEMIC_MVP_MASTER_REQUIREMENTS_V1.1.md), blob `31859af08c1bd0f88084205178b7cb0fdbe88582`.

| Metric | Verified |
|---|---:|
| Mounted unique application method/path pairs | 648 |
| Included routers | 75 |
| OpenAPI operations | 628 |
| Hidden mounted operations | 20 |
| Duplicate exact pairs | 0 |

The mounted fixture records method, path, handler and OpenAPI visibility. It matches every Phase 02A CSV row on those fields. The full OpenAPI JSON fixture preserves operations and schemas. Both use sorted JSON keys and UTF-8 with LF. Health is included. Framework documentation routes are excluded from the application count. This check does not detect overlapping dynamic routes.

- [Mounted snapshot](../../../tests/fixtures/academic_mvp/mounted_operations.json)
- [OpenAPI snapshot](../../../tests/fixtures/academic_mvp/openapi.json)
- [Baseline and native Adventure regression tests](../../../tests/test_academic_mvp_api_baseline.py)

## Offline results

**37 passed, 0 failed, 0 skipped, 2 dependency deprecation warnings, 17.75 seconds.** Ten new cases cover both snapshots, anonymous requests to seven protected routes, and native Adventure availability, pay-at-location booking and final-place exhaustion. The other 27 cases are focused existing traveler, provider, Admin, Homestay, Rental, Adventure/Eco and Tour Guide regressions. Scope includes wrong-owner checks in Guide/Eco, rental overlap, and Homestay blackout. The native Adventure case is sequential. SQL Server locking and true concurrent final-place behavior remain unverified.

The initial harness run was invalid because Windows asyncio uses a local socket pair and the inherited pytest temp directory was inaccessible. The rerun allowed only the socket pair's loopback connect during its construction and used a unique pytest temp directory. DNS, application socket connects and ODBC connects were blocked before import. The final run recorded **zero blocked connection attempts**. The test runner used in-process ASGI requests, isolated SQLite fixtures and targeted mocks. No external service or migration was started.

### Reproduce the bounded run from the isolated worktree

```powershell
$env:TESTING='1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
@'
import os,socket,pyodbc,threading,tempfile
from pathlib import Path
attempts=[]
def deny(*args,**kwargs):
    attempts.append('connection')
    raise AssertionError('External connection forbidden by Stage 01 test guard')
state=threading.local()
real_pair=socket.socketpair
real_connect=socket.socket.connect
def local_pair(*args,**kwargs):
    state.pair=True
    try:
        return real_pair(*args,**kwargs)
    finally:
        state.pair=False
def guarded_connect(sock,address):
    if getattr(state,'pair',False) and isinstance(address,tuple) and address[0] in ('127.0.0.1','::1'):
        return real_connect(sock,address)
    return deny()
socket.socketpair=local_pair
socket.socket.connect=guarded_connect
pyodbc.connect=socket.socket.connect_ex=socket.create_connection=socket.getaddrinfo=deny
def timeout():
    print('Stage 01 test timeout after 120 seconds',flush=True)
    os._exit(124)
watchdog=threading.Timer(120,timeout)
watchdog.daemon=True
watchdog.start()
import pytest
result=pytest.main(['-q','--tb=short','-p','no:cacheprovider','--basetemp', str(Path(tempfile.mkdtemp(prefix='ceylongo-stage01-')) / 'pytest'), *["tests/test_academic_mvp_api_baseline.py","tests/test_auth.py::test_register_success","tests/test_auth.py::test_login_success","tests/test_provider_registration.py::test_final_submit_success_new_provider","tests/test_provider_password_setup.py::test_provider_login_success","tests/test_provider_password_setup.py::test_provider_login_blocked_for_pending_rejected_and_incomplete","tests/test_admin_auth.py::test_admin_login_succeeds_for_all_approved_and_legacy_roles","tests/test_admin_auth.py::test_admin_me_rejects_unauthenticated_invalid_and_expired_tokens","tests/test_admin_auth.py::test_traveler_and_provider_login_flows_remain_unchanged","tests/test_homestay.py::test_homestay_location_search","tests/test_homestay_phase1_implementation.py::test_availability_blackout_blocks_and_booking_validation","tests/test_rental_phase2_batch1.py::test_discovery_returns_only_approved_and_available_vehicles","tests/test_rental_phase1_models_and_booking.py::test_create_booking_success","tests/test_rental_phase1_models_and_booking.py::test_create_booking_detects_overlap_pending_and_active","tests/test_eco_experiences_api.py::test_public_discovery_filters_categories_districts_and_privacy","tests/test_eco_reservations.py::test_per_person_lifecycle_cash_history_and_access","tests/test_tour_guide_phase2.py::test_public_discovery_hides_all_unapproved_suspended_and_inactive_guides","tests/test_tour_guide_phase3.py::test_create_bookings_server_side_pricing_duration_and_side_effects","tests/test_tour_guide_phase3.py::test_booking_validation_tampering_group_size_and_authorization","tests/test_tour_guide_phase3.py::test_overlap_protection_duplicate_confirmation_and_availability_sync","tests/test_activity_booking_management.py::test_traveler_can_view_own_activity_bookings","tests/test_activity_booking_management.py::test_non_traveler_cannot_view_activity_bookings"]])
watchdog.cancel()
print('Blocked connection attempts:',len(attempts))
raise SystemExit(1 if attempts else result)
'@ | ..\CeylonGo-Backend\.venv\Scripts\python.exe -B -
```
