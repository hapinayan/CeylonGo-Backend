from datetime import datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.models.eco_promotion import EcoSponsoredPromotion
from app.models.provider_profile import ProviderProfile
from app.models.shared import PromotionCampaign
from app.models.user import User
from app.routers.admin_promotions import list_campaigns
from tests.test_admin_dashboard import dashboard_sql
from tests.test_admin_rbac_matrix import _create_user_and_headers, client, db_session, role_headers
from tests.test_eco_promotions_campaigns_p11c import create_experience, create_package


PATH = "/api/v1/admin/promotions/campaigns"
CREATED = datetime(2026, 1, 1)


@pytest.fixture
def campaigns(db_session, role_headers):
    user = db_session.query(User).filter_by(role="provider").one()
    profile = ProviderProfile(user_id=user.id, business_name="Private company", provider_type="eco_tourism",
                              district="Kandy", phone="0771234567")
    db_session.add(profile)
    db_session.flush()
    experience = create_experience(db_session, profile)
    package = create_package(db_session)
    for campaign_id, module, status in ((1, "homestay", "draft"), (2, "homestay", "completed"),
                                        (3, "rental_vehicle", "draft")):
        db_session.add(PromotionCampaign(
            id=campaign_id, target_module=module, provider_profile_id=profile.id, campaign_name="Private campaign name",
            status=status, budget=100, created_at=CREATED, starts_at=CREATED, ends_at=CREATED,
        ))
    for campaign_id, status in ((1, "pending_approval"), (2, "expired")):
        db_session.add(EcoSponsoredPromotion(
            id=campaign_id, eco_experience_id=experience.id, provider_profile_id=profile.id, package_id=package.id,
            campaign_status=status, created_at=CREATED, package_price_snapshot=200, duration_days_snapshot=7,
            payment_reference="private-payment-reference", review_reason="private-review-note",
        ))
    db_session.commit()


@pytest.mark.parametrize("role", ["super_admin", "operations_admin", "finance_admin", "admin",
                                     "content_moderator", "support_admin", "traveler", "provider"])
def test_campaign_queue_roles_and_denied_sql(client, role_headers, dashboard_sql, role):
    dashboard_sql.clear()
    response = client.get(PATH, params={"module": "homestay"}, headers=role_headers[role])
    if role in {"super_admin", "operations_admin", "finance_admin", "admin"}:
        assert response.status_code == 200
        assert response.json() == {"module": "homestay", "items": [], "total": 0,
                                   "page": 1, "page_size": 20, "total_pages": 0}
    else:
        assert response.status_code == 403
        assert not any("promotion_campaigns" in sql or "eco_sponsored_promotions" in sql for sql in dashboard_sql)


def test_inactive_and_unauthenticated_callers_cannot_query_campaigns(client, db_session, dashboard_sql):
    _, inactive = _create_user_and_headers(db_session, email="inactive-campaign@example.com",
                                          role="operations_admin", is_active=False)
    dashboard_sql.clear()
    assert client.get(PATH, params={"module": "eco_experience"}).status_code == 401
    assert client.get(PATH, params={"module": "eco_experience"}, headers=inactive).status_code == 403
    assert not any("promotion_campaigns" in sql or "eco_sponsored_promotions" in sql for sql in dashboard_sql)


@pytest.mark.parametrize("module,table,ordered", [
    ("homestay", "promotion_campaigns", [(2, "completed"), (1, "draft")]),
    ("eco_experience", "eco_sponsored_promotions", [(2, "expired"), (1, "pending_approval")]),
])
def test_module_projection_sorting_and_repeat_reads(client, role_headers, dashboard_sql, campaigns, module, table, ordered):
    headers = role_headers["operations_admin"]
    dashboard_sql.clear()
    base = {"module": module, "page_size": 1}
    for page, (record_id, status) in enumerate(ordered, start=1):
        params = base | {"page": page}
        response = client.get(PATH, params=params, headers=headers)
        assert response.status_code == 200
        timestamp = CREATED.isoformat()
        date = timestamp if module == "homestay" else None
        assert response.json() == {
            "module": module, "items": [{"id": record_id, "status": status, "created_at": timestamp,
                                        "starts_at": date, "ends_at": date}],
            "total": 2, "page": page, "page_size": 1, "total_pages": 2,
        }
        assert client.get(PATH, params=params, headers=headers).json() == response.json()

    overflow = client.get(PATH, params=base | {"page": 9}, headers=headers).json()
    assert overflow["items"] == [] and overflow["total"] == 2 and overflow["total_pages"] == 2
    maximum = client.get(PATH, params={"module": module, "page_size": 100}, headers=headers).json()
    assert maximum["page_size"] == 100 and maximum["total_pages"] == 1
    filtered = client.get(PATH, params={"module": module, "status": ordered[0][1]}, headers=headers).json()
    assert filtered["total"] == 1 and filtered["total_pages"] == 1
    assert [item["id"] for item in filtered["items"]] == [ordered[0][0]]
    assert all(sql.startswith("select ") for sql in dashboard_sql)
    campaign_sql = [sql for sql in dashboard_sql if "promotions" in sql or "campaigns" in sql]
    assert len(campaign_sql) == 14
    for sql in campaign_sql:
        assert f"from {table}" in sql
        assert all(field not in sql for field in ("payment", "review_reason", "campaign_name", "provider", "budget", "audit"))
    count_sql, page_sql = campaign_sql[-2:]
    assert count_sql.split("where")[1].strip() == page_sql.split("where")[1].split("order by")[0].strip()


@pytest.mark.parametrize("params", [
    {}, {"module": "rental_vehicle"}, {"module": "tour_guide"}, {"module": "homestay", "status": "expired"},
    {"module": "eco_experience", "status": "paused"}, {"module": "eco_experience", "status": "paid"},
    {"module": "homestay", "page": 0}, {"module": "homestay", "page": 1_000_001},
    {"module": "eco_experience", "page_size": 101}, {"module": "eco_experience", "status": ""},
])
def test_invalid_campaign_filters_do_not_query_data(client, role_headers, dashboard_sql, params):
    dashboard_sql.clear()
    assert client.get(PATH, params=params, headers=role_headers["finance_admin"]).status_code == 422
    assert not any("promotion_campaigns" in sql or "eco_sponsored_promotions" in sql for sql in dashboard_sql)


@pytest.mark.parametrize("module,statuses", [
    ("homestay", ("draft", "pending_approval", "approved", "active", "paused", "completed", "cancelled", "rejected")),
    ("eco_experience", ("pending_approval", "approved", "active", "rejected", "expired", "cancelled")),
])
def test_native_states_are_accepted(client, role_headers, module, statuses):
    for status in statuses:
        response = client.get(PATH, params={"module": module, "status": status}, headers=role_headers["admin"])
        assert response.status_code == 200 and response.json()["total"] == 0


def test_existing_module_queues_keep_their_contracts(client, role_headers, campaigns):
    headers = role_headers["finance_admin"]
    homestay = client.get("/api/v1/admin/homestays/campaigns", params={"status_filter": "draft"}, headers=headers)
    assert homestay.status_code == 200
    assert [item["id"] for item in homestay.json()] == [1]
    eco = client.get("/api/v1/admin/eco-promotions/campaigns", params={"campaign_status": "expired"}, headers=headers)
    assert eco.status_code == 200
    assert eco.json()["total"] == 1 and eco.json()["items"][0]["id"] == 2


def test_count_and_page_may_observe_an_intervening_commit(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'campaigns.sqlite').as_posix()}")
    table = PromotionCampaign.__table__
    try:
        table.create(engine)
        with engine.begin() as writer:
            writer.execute(table.insert().values(id=1, provider_profile_id=1, target_module="homestay",
                                                 campaign_name="Campaign", status="draft", budget=100,
                                                 created_at=CREATED, starts_at=CREATED, ends_at=CREATED))

        @event.listens_for(engine, "before_cursor_execute", once=False)
        def update_between_queries(_conn, _cursor, statement, _params, _context, _many):
            if statement.lstrip().startswith("SELECT") and "ORDER BY" in statement:
                with engine.begin() as writer:
                    writer.execute(table.update().where(table.c.id == 1).values(status="completed"))

        with Session(engine) as reader:
            result = list_campaigns(reader, "homestay", status="draft", page=1, page_size=20)
        assert result.total == 1 and result.total_pages == 1
        assert result.items == []
    finally:
        engine.dispose()
