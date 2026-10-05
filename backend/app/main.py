import asyncio
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import (health, live, routes_admin, routes_auth, routes_auto, routes_backtest, routes_capital, routes_market,
                 routes_me, routes_paper, routes_strategy)
from app.config import get_settings
from app.db import get_engine
from app.errors import validation_handler



@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Background jobs of the web server: forward live prices, and report what is on screen."""
    stop = asyncio.Event()
    tasks = [asyncio.create_task(live.listen(stop)), asyncio.create_task(live.refresh_demand(stop))]
    try:
        yield
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="AR Portfolio Bot", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_exception_handler(RequestValidationError, validation_handler)
app.include_router(routes_auth.router)
app.include_router(routes_me.router)
app.include_router(routes_admin.router)
app.include_router(routes_market.router)
app.include_router(routes_capital.router)
app.include_router(routes_paper.router)
app.include_router(routes_strategy.router)
app.include_router(routes_auto.router)
app.include_router(routes_backtest.router)
app.include_router(live.router)

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
