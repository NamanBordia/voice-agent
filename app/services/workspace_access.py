"""Temporary, origin-bound recruiter pairing for the loopback companion backend."""

from dataclasses import dataclass
import hashlib
import hmac
from ipaddress import ip_address
import re
import secrets
import time
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.services.runtime_config import get_cached_runtime_config

PAIRING_LIFETIME_SECONDS = 7200
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


@dataclass
class WorkspacePairing:
    """One revocable credential per backend process; never persisted or logged."""

    digest: str = ""
    origin: str = ""
    expires_at: float = 0

    def issue(self, origin: str) -> str:
        """Replace any previous credential, binding the new one to the saved origin."""
        token = secrets.token_urlsafe(32)
        self.digest = hashlib.sha256(token.encode()).hexdigest()
        self.origin = origin
        self.expires_at = time.monotonic() + PAIRING_LIFETIME_SECONDS
        return token

    def revoke(self) -> None:
        """Invalidate the current credential immediately."""
        self.digest, self.origin, self.expires_at = "", "", 0

    def accepts(self, origin: str, authorization: str) -> bool:
        """Check expiry, origin binding and the token using a constant-time comparison."""
        if (not self.digest or origin != self.origin or time.monotonic() >= self.expires_at
                or not authorization.startswith("Bearer ") or len(authorization) > 200):
            return False
        digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
        return hmac.compare_digest(self.digest, digest)


class WorkspaceCORSMiddleware:
    """Permit only listed management APIs, an exact origin and paired credentials."""

    def __init__(self, app: ASGIApp, pairing: WorkspacePairing) -> None:
        self.app, self.pairing = app, pairing

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
            if not self.pairing.accepts(origin, headers.get("authorization", "")):
                await JSONResponse({"detail": "Workspace pairing expired or invalid. Pair again from local Configuration."},
                    status_code=401, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})(inner, receiver, sender)
                return
            inner.setdefault("state", {})["hosted_workspace_authorized"] = True
            await self.app(inner, receiver, sender)

        cors = CORSMiddleware(authorized, allow_origins=[origin],
            allow_methods=["GET", "POST", "PUT", "PATCH", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"], allow_credentials=False, max_age=600)
        await cors(scope, receive, send)
