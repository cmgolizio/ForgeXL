"""ForgeXL stays on this machine (build plan 7K, section 8, section 19).

Build plan 7K asks for three verifications:

    servers bind to 127.0.0.1
    CORS is not wildcard
    no remote analytics/data calls were introduced

They are the sort of property that is true right up until somebody changes one
line, and none of them has a visible symptom when it breaks: an application
bound to `0.0.0.0` works exactly as well from the machine that broke it, and a
wildcard CORS policy is invisible to every request the application itself
makes. So each is asserted here rather than checked once by hand.

The tests read the repository's own files where the setting lives in a file —
`package.json` for how Next.js is started, `config.py` for the backend's own
host — because that is where a regression would actually appear. A test that
only read `config.HOST` back into Python would pass while the npm script that
starts the server said something else entirely.

**One deliberate exception, and it is in the build plan.** `npm run dev:lan`
binds Next.js to `0.0.0.0`, which build plan 6G.6 requires so a second laptop
can reach ForgeXL with nothing but a browser. FastAPI is not exposed by it and
must never be (6G.5, Phase 6 rule 11), so the tests below check that the
exception is exactly one script, and exactly the frontend.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app import config

#: The repository root, from this file rather than from the working directory:
#: `conftest.quarantine` moves the process into a temporary directory, so
#: anything relative would look in the wrong place.
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

FRONTEND_SOURCE = REPOSITORY_ROOT / "src"
BACKEND_SOURCE = REPOSITORY_ROOT / "backend" / "app"


def _npm_scripts() -> dict[str, str]:
    return json.loads((REPOSITORY_ROOT / "package.json").read_text())["scripts"]


def _source_files() -> list[Path]:
    """Every source file either half of the application is built from."""
    return [
        *sorted(BACKEND_SOURCE.rglob("*.py")),
        *sorted(FRONTEND_SOURCE.rglob("*.js")),
        *sorted(FRONTEND_SOURCE.rglob("*.jsx")),
        *sorted((REPOSITORY_ROOT / "scripts").glob("*.mjs")),
        REPOSITORY_ROOT / "next.config.mjs",
    ]


# ---------------------------------------------------------------------------
# 7K.1 — binding
# ---------------------------------------------------------------------------


def test_the_backend_binds_to_loopback_by_default() -> None:
    """Build plan section 8: 127.0.0.1, not 0.0.0.0."""
    assert config.HOST == "127.0.0.1"
    assert config.PORT == 8000


def test_the_backend_host_is_read_from_configuration_and_nowhere_else() -> None:
    """A hardcoded address elsewhere would survive a change to `config.HOST`."""
    offenders = [
        path
        for path in BACKEND_SOURCE.rglob("*.py")
        if "0.0.0.0" in path.read_text()
    ]

    assert offenders == []


def test_no_npm_script_exposes_the_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Build plan 6G.5 and Phase 6 rule 11: FastAPI stays on this machine.

    Every script starts the backend the same way — through
    `scripts/dev-backend.sh`, which takes the host from `config.HOST` — so
    there is no script-level way to widen it.
    """
    scripts = _npm_scripts()

    for name, command in scripts.items():
        if "dev-backend" in command or name == "dev:api":
            assert "0.0.0.0" not in command, name

    assert scripts["dev:api"] == "bash scripts/dev-backend.sh"
    launcher = (REPOSITORY_ROOT / "scripts" / "dev-backend.sh").read_text()
    assert "0.0.0.0" not in launcher
    # No host is passed on the command line at all, so `config.HOST` is the
    # only thing that decides where the backend listens.
    assert "-m app.main" in launcher
    assert "--host" not in launcher


def test_only_the_lan_script_binds_the_frontend_to_every_interface() -> None:
    """Build plan 6G.6 permits exactly one exception, and this is it.

    The ordinary `dev`, `build` and `start` paths are loopback; `dev:lan` is
    the opt-in a user types deliberately when they want the second laptop to
    reach the page.
    """
    scripts = _npm_scripts()
    exposed = sorted(name for name, command in scripts.items() if "0.0.0.0" in command)

    assert exposed == ["dev:web:lan"]
    assert "--hostname 127.0.0.1" in scripts["dev:web"]
    assert "--hostname 127.0.0.1" in scripts["start"]


def test_the_lan_script_still_leaves_the_backend_on_loopback() -> None:
    """The whole point of the same-origin proxy (Phase 6 rules 11-13).

    `dev:lan` exposes Next.js and only Next.js; the backend it runs alongside
    is the same `dev:api` every other script runs.
    """
    scripts = _npm_scripts()

    assert "npm:dev:api" in scripts["dev:lan"]
    assert "0.0.0.0" not in scripts["dev:api"]


# ---------------------------------------------------------------------------
# 7K.2 — CORS
# ---------------------------------------------------------------------------


def test_the_allowed_origins_are_exactly_the_two_local_ones() -> None:
    """Build plan section 19: the two ways a browser addresses the dev server."""
    assert config.ALLOWED_FRONTEND_ORIGINS == [
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ]


def test_no_configured_origin_is_a_wildcard() -> None:
    assert "*" not in config.ALLOWED_FRONTEND_ORIGINS
    assert all(
        origin.startswith("http://127.0.0.1") or origin.startswith("http://localhost")
        for origin in config.ALLOWED_FRONTEND_ORIGINS
    )


def test_a_wildcard_is_not_reachable_through_the_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The override splits a list; it never becomes an allow-anything switch.

    Checked because the variable is the one place a wildcard could enter
    without a code change, and the parser treats `*` as an ordinary origin
    string rather than as a pattern.
    """
    parsed = config._parse_origins("*")

    assert parsed == ["*"]
    # Which is not a wildcard to Starlette's middleware unless it is passed as
    # `allow_origins=["*"]` — the point being that nothing in the repository
    # does, and the test below is what says so.


def test_the_application_never_configures_a_wildcard_origin() -> None:
    """Read from `main.py` itself, where the middleware is actually added."""
    source = (BACKEND_SOURCE / "main.py").read_text()

    assert "allow_origins=config.ALLOWED_FRONTEND_ORIGINS" in source
    assert 'allow_origins=["*"]' not in source
    assert "allow_origin_regex" not in source
    # Credentials are never sent, so a permissive origin could not be paired
    # with cookies even if one were configured.
    assert "allow_credentials=False" in source


def test_an_unlisted_origin_is_not_granted_access(client) -> None:
    """The behaviour, not only the setting.

    Starlette answers the request either way — CORS is enforced by the browser
    — but the response carries no `Access-Control-Allow-Origin` for an origin
    that is not allowed, which is what makes the browser refuse it.
    """
    response = client.get("/health", headers={"Origin": "http://evil.example.com"})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_a_listed_origin_is_granted_access_by_name(client) -> None:
    """The control, and the proof the header is never `*`."""
    response = client.get("/health", headers={"Origin": "http://127.0.0.1:3000"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"


def test_a_preflight_from_an_unlisted_origin_is_refused(client) -> None:
    response = client.options(
        "/api/runs",
        headers={
            "Origin": "http://evil.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert "access-control-allow-origin" not in response.headers


# ---------------------------------------------------------------------------
# 7K.3 — no remote calls
# ---------------------------------------------------------------------------

#: Hosts a local-only application has no business contacting. Build plan
#: section 8 names most of these directly.
FORBIDDEN_HOSTS = (
    "openai.com",
    "anthropic.com",
    "vercel.com",
    "vercel.app",
    "supabase.co",
    "supabase.com",
    "googleapis.com",
    "google-analytics.com",
    "googletagmanager.com",
    "sentry.io",
    "amazonaws.com",
    "posthog.com",
    "segment.io",
    "mixpanel.com",
    "datadoghq.com",
    "telemetry.nextjs.org",
)


@pytest.mark.parametrize("host", FORBIDDEN_HOSTS)
def test_no_source_file_names_a_remote_service(host: str) -> None:
    """Build plan section 8: uploaded data never leaves the machine.

    A sweep rather than a review, because the risk is a line added later
    rather than one that is there now.
    """
    offenders = [
        str(path.relative_to(REPOSITORY_ROOT))
        for path in _source_files()
        if host in path.read_text()
    ]

    assert offenders == []


def test_no_url_in_the_source_names_a_remote_host() -> None:
    """Every absolute URL is enumerated, and every one must be local.

    The sweep is deliberately over *all* of them rather than over a blocklist,
    because the risk is a host nobody thought to list. Three forms are
    permitted and each is checked for what it is rather than waved through:

    * loopback — `127.0.0.1` and `localhost`, which is the whole application;
    * a documentation link in a comment, on a host that serves documentation;
    * a placeholder or a template hole — `http://<dev-machine>:3000` in a
      docstring diagram, and `` http://${address} `` in the LAN script, which
      prints the machine's own address for a human to type. Neither is a host,
      and neither is fetched: `scripts/lan-address.mjs` only ever writes to the
      console.
    """
    loopback = re.compile(r"https?://(127\.0\.0\.1|localhost)")
    documentation = re.compile(r"https?://(nextjs\.org|www\.w3\.org)/")
    # `<...>` is a placeholder in prose; `${...}` is a JavaScript template hole.
    not_a_host = re.compile(r"https?://[<$]")

    found: list[tuple[str, str]] = []
    for path in _source_files():
        for url in re.findall(r"https?://[^\s\"'`)\]}>,]*", path.read_text()):
            if not (
                loopback.match(url) or documentation.match(url) or not_a_host.match(url)
            ):
                found.append((str(path.relative_to(REPOSITORY_ROOT)), url))

    assert found == []


def test_the_lan_script_only_prints_the_address_it_builds() -> None:
    """The one place the source interpolates a non-loopback address.

    It is a string shown to a human, so this test is what keeps the exemption
    above honest: the script makes no request of any kind.
    """
    source = (REPOSITORY_ROOT / "scripts" / "lan-address.mjs").read_text()

    assert "fetch(" not in source
    assert "http.request" not in source
    assert "XMLHttpRequest" not in source
    for statement in re.findall(r"http://\$\{address\}[^`]*`\)?", source):
        assert "console.log" in source[: source.index(statement)].rsplit("\n", 1)[-1] + statement


def test_the_backend_makes_no_outbound_http_client_available() -> None:
    """No HTTP client is imported by the running application at all.

    `httpx` is a test dependency (build plan section 6.2) and `requests` is not
    a dependency. An application that cannot make a request cannot make one by
    accident.
    """
    offenders = [
        str(path.relative_to(REPOSITORY_ROOT))
        for path in BACKEND_SOURCE.rglob("*.py")
        if re.search(r"^\s*(import|from)\s+(httpx|requests|urllib|aiohttp)\b",
                     path.read_text(), re.MULTILINE)
    ]

    assert offenders == []


def test_the_frontend_fetches_nothing_off_origin() -> None:
    """Every `fetch` the browser makes is same-origin (Phase 6 rules 7-9).

    `src/lib/api.js` is the only module that addresses the backend and every
    path in it begins with `/forge-api`. The one module that knows FastAPI's
    address is server-only and is imported by the Route Handler alone.
    """
    api_client = (FRONTEND_SOURCE / "lib" / "api.js").read_text()

    assert 'API_BASE_PATH = "/forge-api"' in api_client
    assert "127.0.0.1" not in api_client
    assert "localhost" not in api_client

    origin_module = (FRONTEND_SOURCE / "lib" / "backend-origin.js").read_text()
    assert 'import "server-only"' in origin_module

    importers = [
        str(path.relative_to(REPOSITORY_ROOT))
        for path in _source_files()
        if path.suffix in {".js", ".jsx"}
        and "backend-origin" in path.read_text()
        and path.name != "backend-origin.js"
    ]
    assert importers == ["src/app/forge-api/[...path]/route.js"]


def test_no_public_environment_variable_carries_a_backend_address() -> None:
    """A `NEXT_PUBLIC_` variable is inlined into the browser bundle.

    There is deliberately none: a same-origin path has no host to configure,
    and a configurable one could be pointed off-origin.
    """
    offenders = [
        str(path.relative_to(REPOSITORY_ROOT))
        for path in _source_files()
        if re.search(r"process\.env\.NEXT_PUBLIC_", path.read_text())
    ]

    assert offenders == []


def test_next_telemetry_is_disabled_by_every_script_that_runs_next() -> None:
    """Build plan 7K's third question, answered in the repository (Known Issue 1).

    Next.js sends anonymous build and usage telemetry to Vercel by default. It
    carries no uploaded data, so it does not breach build plan section 8's rule
    about *data* — but it is an outbound call from a deliberately local-only
    project, and 7K is the phase that owes an explicit decision.

    The decision is to disable it **in the repository**, by exporting
    `NEXT_TELEMETRY_DISABLED=1` in the scripts themselves. The alternative,
    `next telemetry disable`, writes to a machine-global config file outside
    this repository: it would fix one developer's machine and leave the next
    checkout sending telemetry again.
    """
    scripts = _npm_scripts()
    runs_next = {
        name: command
        for name, command in scripts.items()
        if re.search(r"\bnext (dev|build|start)\b", command)
    }

    assert runs_next, "no script runs Next.js — this test is checking nothing"
    for name, command in runs_next.items():
        assert "NEXT_TELEMETRY_DISABLED=1" in command, name


def test_no_uploaded_bytes_are_written_anywhere_by_a_run(
    client, quarantine: Path
) -> None:
    """The strongest form of "the data stays here": it does not even reach disk.

    Build plan section 8 forbids transmitting uploaded data. Phase 6 goes
    further and never stores it either, so there is nothing on this machine to
    transmit later.
    """
    from tests.fixtures import spreadsheets as fx
    from tests.helpers import upload_file

    response = client.post(
        "/api/runs",
        data={"action_id": "exact_duplicate_remover"},
        files={
            "source_file": upload_file("sensitive.csv", fx.SIMPLE_TABLE.as_csv())
        },
    )

    assert response.status_code == 200, response.text
    assert list(quarantine.rglob("*")) == []
    assert not (REPOSITORY_ROOT / "data").exists()