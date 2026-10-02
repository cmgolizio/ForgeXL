"""Refuse browser-origin writes that bypass the validated Next.js transport."""
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from app import config


class BrowserWriteGuard:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] == "POST":
            headers = Headers(scope=scope)
            origin = headers.get("origin")
            if headers.get("sec-fetch-site") == "cross-site" or (origin is not None and origin not in config.ALLOWED_FRONTEND_ORIGINS):
                response = JSONResponse(status_code=403, content={"error": {
                    "code": "CROSS_ORIGIN_REQUEST", "message": "Use the ForgeXL page to change local data.", "details": {}}})
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
