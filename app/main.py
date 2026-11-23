"""
CeylonGo Academic MVP — Application Entry Point
Directs execution to the dedicated 17+1 Academic MVP FastAPI runtime.
Legacy 90-mapper runtime is preserved in app/main_legacy.py.
"""

import os
from dotenv import load_dotenv
from pathlib import Path

_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_env_path)

if os.getenv("USE_LEGACY_RUNTIME", "false").lower() == "true":
    from app.main_legacy import app
else:
    from app.main_mvp import app

__all__ = ["app"]
