from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class SendOtpRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr = Field(
        ...,
        max_length=254,
        description="Service provider email address to receive verification OTP",
        examples=["provider@example.com"],
    )


class SendOtpResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    message: str = Field(
        ...,
        description="Operation status message",
        examples=["Verification code sent successfully."],
    )
    email: str = Field(
        ...,
        description="Normalized email address to which the OTP was dispatched",
        examples=["provider@example.com"],
    )


class VerifyOtpRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr = Field(
        ...,
        max_length=254,
        description="Service provider email address",
        examples=["provider@example.com"],
    )
    otp: str = Field(
        ...,
        min_length=6,
        max_length=6,
        pattern=r"^[0-9]{6}$",
        description="Six-digit numeric verification code",
        examples=["123456"],
    )


class VerifyOtpResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    verified: bool = Field(
        ...,
        description="Whether email OTP verification was successful",
        examples=[True],
    )
    next_action: Literal[
        "continue_registration",
        "resubmit_application",
        "create_password",
        "application_pending",
        "login_required",
    ] | None = Field(
        None,
        description="Required next action for the client workflow",
        examples=["continue_registration"],
    )
    authorization_token: str | None = Field(
        None,
        description="Cryptographically secure single-use temporary authorization token",
    )
    expires_in: int | None = Field(
        None,
        description="Authorization token lifespan in seconds (e.g. 1800 for 30 minutes)",
        examples=[1800],
    )
    message: str | None = Field(
        None,
        description="Informational message or account status guidance",
        examples=["Email verified successfully."],
    )
