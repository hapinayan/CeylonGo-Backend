"""
CeylonGo Academic MVP — Database Session & Dependencies
Provides database session dependency for the 17+1 MVP models.
"""

from typing import Generator
from sqlalchemy.orm import Session, sessionmaker
from app.database.connection import engine

MVPSessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)

def get_mvp_db() -> Generator[Session, None, None]:
    """Dependency that provides an active MVP database session."""
    db = MVPSessionLocal()
    try:
        yield db
    finally:
        db.close()
