"""
CeylonGo Academic MVP — Database Session & Dependencies
Provides database session dependency for the 17+1 MVP models.
"""

from typing import Generator
from sqlalchemy.orm import Session, sessionmaker
from app.database.connection import engine, get_db

MVPSessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)

# Unified session dependency: aliases get_db for 100% compatibility across MVP and Admin tests
get_mvp_db = get_db
