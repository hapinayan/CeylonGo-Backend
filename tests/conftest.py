import os

import pytest

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-characters-long-example!!"
os.environ["JWT_ALGORITHM"] = "HS256"
os.environ["JWT_ACCESS_TOKEN_EXPIRE_MINUTES"] = "30"


@pytest.fixture
def traveler_otp_mailbox(monkeypatch):
    from app.services import traveler_email_verification as service

    mailbox = {}

    def deliver(email, otp):
        mailbox[email] = otp
        return True

    service.send_limiter.reset()
    service.verify_limiter.reset()
    monkeypatch.setattr(service, "send_traveler_otp_email", deliver)
    yield mailbox
    service.send_limiter.reset()
    service.verify_limiter.reset()


@pytest.fixture
def verify_traveler_signup(traveler_otp_mailbox):
    def verify(client, pending, prefix="/api/v1/auth"):
        assert pending.status_code == 202, pending.text
        challenge = pending.json()
        email = challenge["email"]
        response = client.post(
            f"{prefix}/verify-email",
            json={
                "email": email,
                "registration_id": challenge["registration_id"],
                "otp": traveler_otp_mailbox[email],
            },
        )
        assert response.status_code == 200, response.text
        return response

    return verify


@pytest.fixture
def seed_mvp_account():
    from app.core.security import hash_password
    from app.models.mvp import MVPProviderProfile, MVPUser

    def seed(sessions, *, email, name, password, role, business=None):
        with sessions() as db:
            user = MVPUser(email=email, full_name=name, role=role,
                           hashed_password=hash_password(password))
            db.add(user)
            if business:
                db.add(MVPProviderProfile(user=user, business_name=business[0],
                                         phone=business[1], district=business[2],
                                         verification_status="approved"))
            db.commit()

    return seed
