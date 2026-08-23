"""FastAPI application for the Local Data Workbench backend.

Phase 1 deliberately exposes a single endpoint, ``GET /health``, which the
frontend uses to show whether the backend is reachable. Action discovery and
Run execution arrive in later phases.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import config

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