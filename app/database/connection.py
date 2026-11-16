import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker

# Load .env reliably from the backend root regardless of working directory
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=_env_path)

DATABASE_URL = URL.create(
    "mssql+pyodbc",
    host=os.getenv("DB_SERVER", r".\SQLEXPRESS"),
    database=os.getenv("DB_NAME", "CeylonGoDB"),
    query={
        "driver": os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server"),
        "Trusted_Connection": "yes",
        "TrustServerCertificate": os.getenv("DB_TRUST_CERTIFICATE", "no"),
    },
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
