
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ActivityApprovalRequest(BaseModel):
    """Admin approval request."""

    model_config = ConfigDict(extra="forbid")


class ActivityRejectionRequest(BaseModel):
    """Admin rejection request with a mandatory reason."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        min_length=10,
        max_length=500,
        description="Reason for rejecting the activity",
    )

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        value = value.strip()

        if len(value) < 10:
            raise ValueError(
                "Rejection reason must contain at least 10 characters"
            )

        return value
