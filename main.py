"""Local browser voice agent application."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app.config import get_settings
from app.logging_config import setup_logging
from app.routers.browser import router as browser_router
from app.routers.dashboard import router as dashboard_router
from app.services.browser_session import BrowserSessionManager
from app.services.runtime_config import close_runtime_config, seed_runtime_config_if_missing
from app.services.warmup import run_local_warmup

STATIC_DIR = Path(__file__).resolve().parent / "app" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize local resources; provider sockets are opened only per conversation."""
    setup_logging()
    settings = get_settings()
    for asset in ("demo.js", "htmx.min.js"):
        if not (STATIC_DIR / asset).is_file():
            raise RuntimeError("Browser assets missing. Run npm --prefix frontend ci and npm --prefix frontend run build")
    config = await seed_runtime_config_if_missing(settings)
    setup_logging(log_level=config.ops.debug_log_level)
    await run_local_warmup(settings)
    manager = BrowserSessionManager(settings)
    app.state.browser_sessions = manager
    try:
        yield
    finally:
        await manager.close()
        await close_runtime_config()


def create_app() -> FastAPI:
    """Create the single-user, same-origin FastAPI application."""
    app = FastAPI(title="Browser Voice Agent", version="0.2.0", lifespan=lifespan)

    @app.middleware("http")
    async def same_origin(request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Reject browser mutations from another origin without introducing CORS."""
        origin = request.headers.get("origin")
        if request.method in {"POST", "PATCH", "PUT", "DELETE"} and origin:
            parsed = urlsplit(origin)
            if parsed.scheme != request.url.scheme or parsed.netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Use the application's own browser page"}, status_code=403)
        return await call_next(request)

    app.include_router(browser_router)
    app.include_router(dashboard_router)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR), check_dir=False), name="static")
    return app


app = create_app()
