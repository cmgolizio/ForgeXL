"""Central backend configuration.

Every backend module reads its settings from here rather than defining its own
constants, so hosts, ports, paths and limits exist in exactly one place
(build plan Phase 1.6 and section 20).

Defaults are local-only and require no secrets. Each value may be overridden
through an environment variable. The variables are prefixed with ``FORGEXL_``
so they cannot collide with the generic ``HOST`` / ``PORT`` variables that
other local tooling (including ``next dev``) also reads.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# backend/app/config.py -> backend/app -> backend -> repository root
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

_data_directory_override = os.environ.get("FORGEXL_DATA_DIRECTORY")

#: Root directory for all locally generated application data.
DATA_DIRECTORY: Path = (
    Path(_data_directory_override).expanduser().resolve()
    if _data_directory_override
    else PROJECT_ROOT / "data"
)

#: One subdirectory per Run is created here.
RUNS_DIRECTORY: Path = DATA_DIRECTORY / "runs"

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

#: Loopback only. The application is deliberately not exposed to the network.
HOST: str = os.environ.get("FORGEXL_BACKEND_HOST", "127.0.0.1")

PORT: int = int(os.environ.get("FORGEXL_BACKEND_PORT", "8000"))

# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------

#: Maximum accepted size of a single uploaded file (250 MB by default).
MAX_UPLOAD_BYTES: int = int(
    os.environ.get("FORGEXL_MAX_UPLOAD_BYTES", str(250 * 1024 * 1024))
)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

#: The local Next.js development server, addressed both ways a browser may
#: reach it. Wildcard origins are never used (build plan section 19).
DEFAULT_FRONTEND_ORIGINS: tuple[str, ...] = (
    "http://127.0.0.1:3000",
    "http://localhost:3000",
)


def _parse_origins(raw: str | None) -> list[str]:
    """Split a comma-separated origin list, ignoring blank entries."""
    if raw is None:
        return list(DEFAULT_FRONTEND_ORIGINS)
    origins = (origin.strip() for origin in raw.split(","))
    return [origin for origin in origins if origin]


ALLOWED_FRONTEND_ORIGINS: list[str] = _parse_origins(
    os.environ.get("FORGEXL_ALLOWED_FRONTEND_ORIGINS")
)