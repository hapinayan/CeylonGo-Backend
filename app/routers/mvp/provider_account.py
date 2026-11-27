"""Provider-specific preferences and dashboard."""

from fastapi import APIRouter

from app.core.sensitive_route import SensitiveInputRoute
from app.routers.mvp.providers import DB, Provider
from app.schemas.mvp_provider_account import (
    ProviderDashboard,
    ProviderSettings,
    ProviderSettingsPatch,
)
from app.services import mvp_provider_account as account

router = APIRouter(
    prefix="/provider",
    tags=["MVP Provider Account"],
    route_class=SensitiveInputRoute,
)


@router.get("/settings", response_model=ProviderSettings)
def get_settings(db: DB, user: Provider):
    profile = account.own_profile(db, user)
    return account.settings(account.details(db, profile))


@router.patch("/settings", response_model=ProviderSettings)
def update_settings(payload: ProviderSettingsPatch, db: DB, user: Provider):
    profile = account.own_profile(db, user, write=True)
    data = account.details(db, profile)
    changes = payload.model_dump(exclude_unset=True)
    if changes:
        values = data.setdefault("account", {}).setdefault("settings", {})
        values.update(changes)
        account.save(db, profile, data)
    return account.settings(data)


@router.get("/dashboard", response_model=ProviderDashboard)
def dashboard(db: DB, user: Provider):
    return account.dashboard(db, user)
