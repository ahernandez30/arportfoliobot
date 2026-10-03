from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app import health
from app.config import get_settings
from app.db import get_engine

app = FastAPI(title="AR Portfolio Bot", docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/api/health")
def api_health() -> JSONResponse:
    result = health.check(get_engine(), get_settings().worker_stale_seconds)
    return JSONResponse(result, status_code=503 if result["status"] == "error" else 200)
