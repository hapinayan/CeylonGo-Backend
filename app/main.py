
from fastapi import FastAPI

app = FastAPI(
    title="CeylonGo API",
    description="Backend API for the CeylonGo tourism platform",
    version="1.0.0",
)


@app.get("/api/v1/health", tags=["Health"])
def health_check():
    return {
        "status": "healthy",
        "service": "CeylonGo Backend",
    }
