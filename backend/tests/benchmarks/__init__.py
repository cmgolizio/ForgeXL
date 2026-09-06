"""Performance measurement for ForgeXL (build plan 7G, 7H, 7I).

Deliberately **not** part of the automated test suite. `pytest.ini` sets
`testpaths = tests`, and pytest collects only modules named `test_*.py`, so
nothing here runs during `python -m pytest`. That is the intent: a benchmark
asserts nothing, takes minutes rather than seconds, and produces numbers that
belong in `docs/implementation-status.md` rather than in a pass/fail line.

Run it directly:

    cd backend && .venv/bin/python -m tests.benchmarks.run

See `run.py` for what it measures and how to read what it prints.
"""