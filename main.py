"""Local browser voice agent application."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from ipaddress import ip_address

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app.config import get_settings
from app.logging_config import setup_logging
from app.routers.browser import router as browser_router
from app.routers.dashboard import router as dashboard_router
from app.routers.interviews import router as interview_router
from app.interviews.service import InterviewService
from app.services.browser_session import BrowserSessionManager
from app.services.runtime_config import close_runtime_config, seed_runtime_config_if_missing
from app.services.warmup import run_local_warmup

STATIC_DIR = Path(__file__).resolve().parent / "app" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize local resources; provider sockets are opened only per conversation."""
    setup_logging()
    settings = get_settings()
    for asset in ("demo.js", "htmx.min.js", "recruiter.js", "candidate.js", "camera-worker.js"):
        if not (STATIC_DIR / asset).is_file():
            raise RuntimeError("Browser assets missing. Run npm --prefix frontend ci and npm --prefix frontend run build")
    config = await seed_runtime_config_if_missing(settings)
    setup_logging(log_level=config.ops.debug_log_level)
    await run_local_warmup(settings)
    manager = BrowserSessionManager(settings)
    app.state.browser_sessions = manager
    app.state.interviews = InterviewService(settings)
    await app.state.interviews.recover()
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
        candidate_path = request.url.path.startswith(("/candidate/", "/api/candidate/", "/static/"))
        if not candidate_path:
            try:
                local_peer = bool(request.client and ip_address(request.client.host).is_loopback)
            except ValueError:
                local_peer = False
            if not local_peer or request.url.hostname not in {"localhost", "127.0.0.1", "::1"}:
                return JSONResponse({"detail": "Recruiter management is local-only"}, status_code=403)
        if request.url.path.startswith("/api/candidate/"):
            try:
                length = int(request.headers.get("content-length", "0") or "0")
            except ValueError:
                return JSONResponse({"detail": "Invalid content length"}, status_code=400)
            if length < 0 or length > 110000:
                return JSONResponse({"detail": "Request too large"}, status_code=413)
        origin = request.headers.get("origin")
        if request.method in {"POST", "PATCH", "PUT", "DELETE"} and origin:
            parsed = urlsplit(origin)
            if parsed.scheme != request.url.scheme or parsed.netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Use the application's own browser page"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        if candidate_path:
            # The vision worker can fetch local assets only. The page preserves the
            # voice SDK's existing Daily device helper, using its avoidEval mode.
            daily_hosts = "https://*.daily.co https://*.dailywebrtc.com https://*.dailywebrtc.net"
            voice_allowance = "" if request.url.path == "/static/camera-worker.js" else f" blob: {daily_hosts}"
            response.headers["Content-Security-Policy"] = (
                f"default-src 'self'; script-src 'self' 'wasm-unsafe-eval'{voice_allowance}; "
                f"style-src 'self' 'unsafe-inline'; connect-src 'self'{voice_allowance}; "
                f"worker-src 'self'{(' blob:' if voice_allowance else '')}; img-src 'self'; "
                "media-src 'self' blob:; frame-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
            )
        return response

    app.include_router(browser_router)
    app.include_router(dashboard_router)
    app.include_router(interview_router)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR), check_dir=False), name="static")
    return app


app = create_app()
