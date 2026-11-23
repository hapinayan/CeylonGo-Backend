# DB-02 Stage 01 dependency test report

Verified branch feature/academic-mvp-backend-simplification at 3bf9822f6be99780d0e25c8752ed263ac407c3c6 and approved requirements blob 31859af08c1bd0f88084205178b7cb0fdbe88582. Validated 99 ORM mappings, the 49-table retain set from DB-01, and its mapped outbound FK closure.

**53 passed, 0 failed, 0 skipped in 20.05 seconds**, with two existing dependency deprecation warnings. This includes all 37 Phase 02B baseline cases, 14 targeted additional regressions, and two new profile tests. The run reported zero blocked connection attempts. No application lifespan, SQL Server, migration, or external service ran.

The tracer's raw SQL join/DML test deliberately accesses an out-of-profile table and verifies the audit fails that access. During regression calls, every known discrepancy is pinned per test. Newly observed tables outside the allowed set or disappearance of an expected discrepancy fail the run for review. The 53 passes confirm the observed behavior against that evidence baseline. They do not imply a 49-table deployment would pass.

Observed 67 distinct tables across 1,817 traced cursor executions, including 22 outside the DB-01 list. Statements are captured only during test calls, including any explicit test setup/assertions inside the body. No SQL parameters or row values were recorded. The legacy, untested, and dialect-specific paths remain in the full schema until independently verified.

## Reproduce the guarded run

```powershell
$env:TESTING='1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
@'

import json,os,re,runpy,socket,threading,tempfile
from pathlib import Path
import pyodbc
attempts=[]
def deny(*a,**k):
 attempts.append(1)
 raise AssertionError('DB-02 blocks external network and ODBC')
local=threading.local()
pair,connect=socket.socketpair,socket.socket.connect
def local_pair(*a,**k):
 local.pair=True
 try: return pair(*a,**k)
 finally: local.pair=False
def guarded(sock,address):
 if getattr(local,'pair',False) and isinstance(address,tuple) and address[0] in ('127.0.0.1','::1'):
  return connect(sock,address)
 return deny()
socket.socketpair=local_pair
socket.socket.connect=guarded
pyodbc.connect=socket.socket.connect_ex=socket.create_connection=socket.getaddrinfo=deny
def timeout():
 print('Offline test timeout after 120 seconds',flush=True)
 os._exit(124)
timer=threading.Timer(120,timeout)
timer.daemon=True
timer.start()
import pytest
doc=Path('docs/academic-mvp/backend-simplification/PHASE_02B_STAGE01_API_BASELINE.md').read_text('utf-8')
args=json.loads(re.search(r'\*?(\["tests/test_academic_mvp_api_baseline.py".*?\])\s*\]\)',doc).group(1))
extra={
 'admin_providers':['approve_provider_success','traveler_access_denied'],
 'community_phase1':['create_post_authenticated_and_reject_unauthenticated_writes','hard_delete_post_is_fk_safe_with_likes_nested_comments_bookmarks_hashtags_media'],
 'community_phase5c_governance':['generic_post_visibility_uses_audited_moderation'],
 'interactive_map':['aggregation_bounds_categories_and_pagination'],
 'weather':['current_and_cache_key','forecast_grouping_and_recommendations'],
 'eco_t09_mock_payments':['t09_successful_payment_lifecycle_http','t09_cash_booking_cannot_initiate_online_checkout'],
 'tour_guide_phase3':['provider_cash_payment_lifecycle_and_strict_online_separation'],
}
args+=['tests/test_academic_mvp_db_profile.py']+[f'tests/test_{module}.py::test_{name}' for module, names in extra.items() for name in names]
from tests.test_academic_mvp_db_profile import SQLiteTableTrace,retained_tables
allowed=retained_tables()
expected={"test_admin_login_succeeds_for_all_approved_and_legacy_roles[finance_admin-finance_admin]":["advertising_payments","eco_availability_slots","eco_reservations","payment_refunds","provider_settlements"],"test_public_discovery_filters_categories_districts_and_privacy":["eco_age_limits","eco_availability_slots","eco_cancellation_policies","eco_evidences","eco_experience_images","eco_practices","eco_promotion_analytics","eco_safety_rules","eco_season_restrictions","eco_sponsored_promotions","eco_verifications","eco_wishlists"],"test_per_person_lifecycle_cash_history_and_access":["eco_availability_slots","eco_promotion_analytics","eco_reservations","eco_safety_rules","eco_season_restrictions","eco_sponsored_promotions","eco_verifications","payment_refunds"],"test_t09_cash_booking_cannot_initiate_online_checkout":["eco_availability_slots","eco_reservations","eco_safety_rules","eco_season_restrictions","eco_verifications"],"test_t09_successful_payment_lifecycle_http":["eco_availability_slots","eco_reservations","eco_safety_rules","eco_season_restrictions","eco_verifications"],"test_homestay_location_search":["advertising_payments","homestay_promotions"],"test_availability_blackout_blocks_and_booking_validation":["homestay_promotions"],"test_aggregation_bounds_categories_and_pagination":["artisan_listings","eco_experience_images"],"test_discovery_returns_only_approved_and_available_vehicles":["promotion_campaigns","rental_vehicle_documents"],"test_public_discovery_hides_all_unapproved_suspended_and_inactive_guides":["promotion_campaigns"],"test_create_bookings_server_side_pricing_duration_and_side_effects":["eco_promotion_attributions","promotion_events"],"test_overlap_protection_duplicate_confirmation_and_availability_sync":["eco_promotion_attributions","promotion_events"],"test_provider_cash_payment_lifecycle_and_strict_online_separation":["eco_promotion_attributions","promotion_events"]}
class Audit:
 def __init__(self): self.rows={}
 @pytest.hookimpl(wrapper=True)
 def pytest_runtest_call(self,item):
  if item.path.name=='test_academic_mvp_db_profile.py':
   return (yield)
  with SQLiteTableTrace() as trace:
   try:
    outcome=yield
   finally:
    self.rows[item.nodeid]={'statements':trace.statements,'tables':{k:sorted(v) for k,v in sorted(trace.access.items())}}
  known=set(expected.get(item.name,[]))
  trace.require_profile(allowed | known)
  assert trace.access.keys() - allowed == known, 'Recorded out-of-profile dependency disappeared; reconcile the profile'
  return outcome
audit=Audit()
result=pytest.main(['-q','--tb=short','-p','no:cacheprovider','--basetemp',str(Path(tempfile.mkdtemp(prefix='ceylongo-db02-'))/'pytest'),*args],plugins=[audit])
timer.cancel()
print('ATTEMPTS',len(attempts))
print('TRACE',json.dumps(audit.rows,sort_keys=True))
raise SystemExit(1 if attempts else result)

'@ | ..\CeylonGo-Backend\.venv\Scripts\python.exe -B -
```
