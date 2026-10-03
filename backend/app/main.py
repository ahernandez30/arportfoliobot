from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import health, routes_admin, routes_auth, routes_me
from app.config import get_settings
from app.db import get_engine
from app.errors import validation_handler

app = FastAPI(title="AR Portfolio Bot", docs_url=None, redoc_url=None, openapi_url=None)
app.add_exception_handler(RequestValidationError, validation_handler)
app.include_router(routes_auth.router)
app.include_router(routes_me.router)
app.include_router(routes_admin.router)

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@app.middleware("http")
async def same_origin_only(request: Request, call_next):
    """Refuse changes sent from another website (on top of SameSite=Strict cookies)."""
    if request.method not in SAFE_METHODS:
        origin = request.headers.get("origin")
        if origin is not None and urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Request refused."}, status_code=403)
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/health")
def api_health() -> JSONResponse:
    result = health.check(get_engine(), get_settings().worker_stale_seconds)
    return JSONResponse(result, status_code=503 if result["status"] == "error" else 200)
