"""Opt-in CORS for candidate APIs served by the loopback companion backend."""

from ipaddress import ip_address
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.services.runtime_config import get_cached_runtime_config


def approved_candidate_origin(scope: Scope, origin: str) -> bool:
    """Require the exact configured origin and a loopback peer and Host."""
    config = get_cached_runtime_config()
    if not config or not config.ops.candidate_frontend_origin or origin != config.ops.candidate_frontend_origin:
        return False
    try:
        local_peer = bool(scope.get("client") and ip_address(scope["client"][0]).is_loopback)
        host = urlsplit(f"http://{Headers(scope=scope).get('host', '')}").hostname
        return local_peer and host in {"localhost", "127.0.0.1", "::1"}
    except ValueError:
        return False


class CandidateCORSMiddleware:
    """Apply Starlette CORS only to candidate API paths, never recruiter routes."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/candidate/"):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        origin = headers.get("origin")
        if not origin or origin == f"{scope['scheme']}://{headers.get('host', '')}":
            await self.app(scope, receive, send)
            return
        if not approved_candidate_origin(scope, origin):
            response = JSONResponse({"detail": "Candidate frontend origin is not allowed"}, status_code=403,
                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "Vary": "Origin"})
            await response(scope, receive, send)
            return
        cors = CORSMiddleware(self.app, allow_origins=[origin], allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"], allow_credentials=False, max_age=600)
        await cors(scope, receive, send)
