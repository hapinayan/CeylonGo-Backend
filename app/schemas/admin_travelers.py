from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StrictBool, StringConstraints, model_validator


class TravelerAccount(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    email: str
    is_active: bool
    created_at: datetime


class TravelerDirectory(BaseModel):
    items: list[TravelerAccount]
    total: int
    page: int
    page_size: int
    total_pages: int


class TravelerStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: StrictBool
    reason_code: Literal[
        "policy_violation", "fraud_suspected", "security_risk", "user_requested", "administrative_action",
        "appeal_approved", "security_cleared", "issue_resolved", "administrative_review",
    ]
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]

    @model_validator(mode="after")
    def check_reason_for_status(self):
        reactivation_reasons = {"appeal_approved", "security_cleared", "issue_resolved", "administrative_review"}
        if (self.reason_code in reactivation_reasons) != self.is_active:
            raise ValueError("reason_code is invalid for the requested status")
        return self


class TravelerStatusDetails(TravelerStatusUpdate):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)

    previous_is_active: StrictBool

    @model_validator(mode="after")
    def check_state_transition(self):
        if self.previous_is_active == self.is_active:
            raise ValueError("Status history requires an actual transition")
        return self


class TravelerStatusEvent(TravelerStatusDetails):
    admin_user_id: int
    created_at: datetime
