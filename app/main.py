import os
from typing import List

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers.auth import router as auth_router

load_dotenv()

app = FastAPI(
    title="CeylonGo API",
    description="Backend API for the CeylonGo tourism platform",
    version="1.0.0",
)


def get_allowed_origins() -> List[str]:
    # Default Angular development origins
    origins = [
        "http://localhost:4200",
        "http://127.0.0.1:4200",
    ]
    # Read additional comma-separated origins from environment configuration
    custom_origins = os.getenv("CORS_ORIGINS", "")
    if custom_origins:
        for origin in custom_origins.split(","):
            cleaned = origin.strip()
            if cleaned and cleaned not in origins:
                origins.append(cleaned)
    return origins


# Configure CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Accept",
        "Origin",
        "X-Requested-With",
    ],
)

# Register routers
app.include_router(auth_router)


@app.get("/api/v1/health", tags=["Health"])
def health_check():
    return {
        "status": "healthy",
        "service": "CeylonGo Backend",
    }
