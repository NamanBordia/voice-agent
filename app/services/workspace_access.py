"""Explicit-origin workspace access for the loopback companion backend."""

from ipaddress import ip_address
import re
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.services.runtime_config import get_cached_runtime_config

WORKSPACE_PATH = re.compile(
    r"^(?:/api/interviews(?:/[^/]+/plan|/[^/]+/activate|/sessions/[^/]+/(?:report|review))?"
    r"|/api/workspace/(?:config|calls(?:/browser_[0-9a-f]{32})?)"
    r"|/api/offer|/api/sessions/browser_[0-9a-f]{32}/end)$"
)


def is_loopback_request(scope: Scope) -> bool:
    """Require both the network peer and the HTTP Host to name this device."""
    try:
        host = urlsplit(f"http://{Headers(scope=scope).get('host', '')}").hostname
        return bool(scope.get("client") and ip_address(scope["client"][0]).is_loopback
                    and host in {"localhost", "127.0.0.1", "::1"})
    except ValueError:
        return False


class WorkspaceCORSMiddleware:
    """Permit listed APIs only from the exact approved origin on this device."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not WORKSPACE_PATH.fullmatch(scope["path"]):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        origin = headers.get("origin", "")
        if not origin or origin == f"{scope['scheme']}://{headers.get('host', '')}":
            await self.app(scope, receive, send)
            return
        config = get_cached_runtime_config()
        if not (config and origin == config.ops.workspace_frontend_origin and is_loopback_request(scope)):
            await JSONResponse({"detail": "Workspace frontend origin is not allowed"}, status_code=403,
                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "Vary": "Origin"})(scope, receive, send)
            return

        async def authorized(inner: Scope, receiver: Receive, sender: Send) -> None:
            inner.setdefault("state", {})["hosted_workspace_authorized"] = True
            await self.app(inner, receiver, sender)

        cors = CORSMiddleware(authorized, allow_origins=[origin],
            allow_methods=["GET", "POST", "PUT", "PATCH", "OPTIONS"],
            allow_headers=["Content-Type"], allow_credentials=False, max_age=600)
        await cors(scope, receive, send)
