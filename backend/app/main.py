"""FastAPI application for the Local Data Workbench backend.

Composes the application: configuration, CORS, the API routers and the single
place internal exceptions become HTTP responses. Endpoint logic lives in
``app.api``; ``GET /health`` stays here because it reports on this process
rather than on any part of the domain.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import config
from app.api import actions, runs
from app.errors import WorkbenchError

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Local Data Workbench",
    description="Local, deterministic data-processing backend.",
    version="0.1.0",
)

# Exact local origins only. Never "*" (build plan section 19).
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_FRONTEND_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(actions.router)
app.include_router(runs.router)


@app.exception_handler(WorkbenchError)
async def handle_workbench_error(
    request: Request, exc: WorkbenchError
) -> JSONResponse:
    """Render an internal error as the structured API error of section 22.

    This is the only place backend exceptions become HTTP responses. The body
    carries a stable `code`, a plain-language `message` and structured
    `details`; a traceback is logged locally and never returned to the browser.
    """
    if exc.http_status >= 500:
        logger.error(
            "%s %s failed: %s", request.method, request.url.path, exc.message
        )
    return JSONResponse(status_code=exc.http_status, content=exc.as_response_body())


@app.get("/health")
def health() -> dict[str, str]:
    """Report that the backend process is up and serving requests."""
    return {"status": "ok"}


def main() -> None:
    """Run the development server on the configured loopback address.

    Invoked by ``scripts/dev-backend.sh`` (and therefore by ``npm run dev``)
    as ``python -m app.main`` from the ``backend`` directory.
    """
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=config.HOST,
        port=config.PORT,
        reload=True,
        reload_dirs=[str(config.PROJECT_ROOT / "backend" / "app")],
    )


if __name__ == "__main__":
    main()