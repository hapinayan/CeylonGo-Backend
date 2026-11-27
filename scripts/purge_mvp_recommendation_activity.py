"""Scheduled maintenance for expired recommendation activity in CeylonGoDB."""

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database.mvp_session import MVPSessionLocal
from app.services.mvp_account import now
from app.services.mvp_recommendation_activity import installed, purge_expired


def main():
    try:
        with MVPSessionLocal() as db:
            if (
                db.get_bind().dialect.name != "mssql"
                or db.scalar(text("SELECT DB_NAME()")) != "CeylonGoDB"
            ):
                print("Recommendation retention requires CeylonGoDB")
                return 1
            if not installed(db):
                print("Recommendation retention requires MVP schema 1.0.9")
                return 1
            count = purge_expired(db, now())
            db.commit()
    except SQLAlchemyError:
        print("Recommendation retention failed")
        return 1
    print(f"Expired recommendation events removed: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
