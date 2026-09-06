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
#
# **Running a Run still writes nothing.** ForgeXL processes spreadsheets
# entirely in memory: an upload is never written, a result is held as a
# DataFrame, and CSV/XLSX bytes are generated per request (build plan Phase 6
# architectural rules 1-3). Phase 6I removed the ``DATA_DIRECTORY`` /
# ``RUNS_DIRECTORY`` settings along with the last of the on-disk model, and
# nothing has reinstated them.
#
# Phase 9 adds one location, and it is deliberately not that one. The Data
# Library holds *business data* — sales history, sample history, account
# ownership snapshots — which is persistent by definition and is a different
# thing from a Run's runtime state (build plan, "Architectural Rule: Run State
# and Business Data Are Different"). It is the only place the backend is
# configured to write.

# backend/app/config.py -> backend/app -> backend -> repository root
#: Used to point ``uvicorn --reload`` at the backend source tree, and as the
#: base of the Data Library's default location below.
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

#: Where the persistent Data Library keeps its datasets (build plan 9C).
#:
#: Absolute by default, and derived from the repository root rather than from
#: the working directory, so the library a command reaches does not depend on
#: where the command was run from. Git ignores ``data/`` in its entirety:
#: company data is never committed.
#:
#: The directory is created when the first version is committed, not at import
#: time. Reading a library that does not exist yet reports no datasets rather
#: than creating one.
LIBRARY_DIRECTORY: Path = Path(
    os.environ.get(
        "FORGEXL_LIBRARY_DIRECTORY", str(PROJECT_ROOT / "data" / "library")
    )
).expanduser()

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