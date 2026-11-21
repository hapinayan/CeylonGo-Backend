import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker

# Load .env reliably from the backend root regardless of working directory
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=_env_path)

# Normalize server host to prevent unintended duplicate backslashes (e.g. '.\\SQLEXPRESS' -> '.\SQLEXPRESS')
raw_server = os.getenv("DB_SERVER", r".\SQLEXPRESS")
db_server = raw_server
while "\\\\" in db_server:
    db_server = db_server.replace("\\\\", "\\")

db_user = os.getenv("DB_USER") or None
db_password = os.getenv("DB_PASSWORD") or None

# Configure connection parameters:
# Note: For mssql+pyodbc, when username is None, SQLAlchemy automatically adds
# Trusted_Connection=Yes to the generated ODBC connection string.
# Omitting it here prevents duplicate 'Trusted_Connection' parameters.
query_params: dict[str, str] = {
    "driver": os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server"),
    "TrustServerCertificate": os.getenv("DB_TRUST_CERTIFICATE", "yes"),
}
if os.getenv("DB_ENCRYPT"):
    query_params["Encrypt"] = os.getenv("DB_ENCRYPT")

DATABASE_URL = URL.create(
    "mssql+pyodbc",
    username=db_user,
    password=db_password,
    host=db_server,
    database=os.getenv("DB_NAME", "CeylonGoDB"),
    query=query_params,
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
