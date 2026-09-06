"""The ForgeXL performance harness (build plan 7G, 7H, 7I).

    cd backend && .venv/bin/python -m tests.benchmarks.run

Build plan 7G asks for CSV timings at roughly 10,000 / 50,000 / 100,000 rows,
broken into the stages a Run actually passes through:

    upload/save time, parse time, validation time,
    Action execution time, export time, total time

7H asks for the same for XLSX, recorded **separately** — the two formats are
not comparable and the build plan says so explicitly. 7I asks for preview
timings on a large result, at the first page and at a deep offset, and for
evidence that the backend does not send the whole dataset.

Two rules the build plan states and this harness follows:

* **Repeat every meaningful measurement.** Build plan 7G: do not claim
  performance from one anecdotal timing. Every figure below is the median of
  several runs, and the minimum and maximum are printed beside it so a reader
  can see whether the runs agreed. A median that sits far from its own minimum
  is a warning that the number means less than it looks.
* **Generate the data locally.** Build plan 7G: no customer or company data.
  Every fixture comes from `tests.fixtures.spreadsheets.large_table`, which is
  arithmetic on a row index and depends on nothing but the row count.

The stages are measured through the **real** services, in the order the runner
calls them, not through a re-implementation:

    storage.read_upload      the bytes into memory, bounded by the limit
    parser.parse_tabular_bytes   CSV or XLSX to a DataFrame
    the runner's own checks   required columns, emptiness
    action.run               the transformation
    export.to_csv_bytes / to_xlsx_bytes   the download

`execute_run` is timed separately as the whole pipeline, so the stage figures
can be checked against it rather than assumed to add up.

Nothing here writes to the repository. The one thing it prints is the table.
"""

from __future__ import annotations

import gc
import statistics
import sys
import time
import tracemalloc
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import polars as pl

from app.actions.exact_duplicate_remover import ExactDuplicateRemoverAction
from app.actions.product_master_builder import (
    PRODUCT_COLUMNS,
    ProductMasterBuilderAction,
)
from app.services import export, parser, preview, run_store, storage
from app.services.runner import PendingUpload, execute_run

from tests.fixtures import spreadsheets as fx

#: Row counts build plan 7G names for CSV.
CSV_ROW_COUNTS: tuple[int, ...] = (10_000, 50_000, 100_000)

#: Row counts build plan 7H names for XLSX. 100,000 is included: it is "if
#: reasonable", and the measurement below is what decides whether it is.
XLSX_ROW_COUNTS: tuple[int, ...] = (10_000, 50_000, 100_000)

#: How many times each measurement is repeated (build plan 7G).
REPEATS = 5

#: Rows in the result the preview is measured against (build plan 7I).
PREVIEW_ROW_COUNT = 100_000

#: Build plan section 3.4's thresholds, for a 100,000-row CSV.
DESIRED_SECONDS = 5.0
ACCEPTANCE_SECONDS = 15.0


@dataclass
class Measurement:
    """Several timings of one thing, kept rather than averaged away."""

    label: str
    seconds: list[float] = field(default_factory=list)

    @property
    def median(self) -> float:
        return statistics.median(self.seconds)

    @property
    def low(self) -> float:
        return min(self.seconds)

    @property
    def high(self) -> float:
        return max(self.seconds)

    @property
    def spread(self) -> float:
        """How far the slowest run is from the median, as a proportion.

        Printed because build plan 7G asks that a performance claim not rest on
        one timing: a large spread means the median is a weaker statement than
        it appears.
        """
        return (self.high - self.median) / self.median if self.median else 0.0

    def __str__(self) -> str:
        return (
            f"{self.median * 1000:9.1f} ms  "
            f"[{self.low * 1000:8.1f} – {self.high * 1000:8.1f}]"
        )


def measure(label: str, work: Callable[[], object], repeats: int = REPEATS) -> Measurement:
    """Time `work` `repeats` times, discarding what it returns.

    Garbage collection is run before each timing and disabled during it, so a
    collection triggered by the previous iteration is not charged to this one.
    """
    result = Measurement(label)
    for _ in range(repeats):
        gc.collect()
        gc.disable()
        try:
            start = time.perf_counter()
            work()
            result.seconds.append(time.perf_counter() - start)
        finally:
            gc.enable()
    return result


def _fresh_store() -> None:
    """Drop every recorded Run, so the store's size never affects a timing."""
    run_store.RUN_STORE = run_store.InMemoryRunStore()


# ---------------------------------------------------------------------------
# 7G / 7H — the stages of a Run
# ---------------------------------------------------------------------------


def measure_pipeline(
    row_count: int, extension: str
) -> tuple[list[Measurement], dict[str, object]]:
    """Measure every stage of one Run, at `row_count` rows, in one format.

    Returns the measurements and the facts a reader needs to interpret them:
    the payload size, the rows in and out, and the Action that ran.
    """
    table = fx.large_table(row_count)
    payload = table.payload(extension)
    action = ExactDuplicateRemoverAction()
    slot = action.inputs[0].id

    # Each stage is measured on its own, from the same inputs every time, so a
    # stage is never timed against work a previous stage happened to leave
    # behind.
    import io

    read = measure(
        "upload into memory",
        lambda: storage.read_upload(slot, f"bench{extension}", io.BytesIO(payload)),
    )

    parsed = parser.parse_tabular_bytes(payload, extension)
    frame = parsed.frame

    parse = measure(
        "parse", lambda: parser.parse_tabular_bytes(payload, extension)
    )

    # What the runner checks before an Action sees the data: that the dataset
    # has rows, and that every required column is present. The Exact Duplicate
    # Remover requires no columns, so the Product Master Builder's six are used
    # to measure the check that actually does work.
    def validate() -> object:
        present = set(frame.columns)
        return (
            frame.height == 0,
            [name for name in PRODUCT_COLUMNS if name not in present],
        )

    validation = measure("validate", validate)

    execution = measure("Action execution", lambda: action.run({slot: frame}))

    result = action.run({slot: frame}).outputs["deduplicated_data"]

    # Export is measured against a result of *this* size, not against the
    # Action's own output. `large_table` cycles through 250 distinct products,
    # so both proof Actions collapse 100,000 rows to 250 — a real and correct
    # outcome, but timing an export against it would report the cost of writing
    # 250 rows under a heading that says 100,000. The input frame is a result
    # of the full size and is what "export time at this row count" means.
    csv_export = measure("export CSV (full size)", lambda: export.to_csv_bytes(frame))
    xlsx_export = measure(
        "export XLSX (full size)",
        lambda: export.to_xlsx_bytes(frame, worksheet="Deduplicated Data"),
        repeats=max(3, REPEATS - 2),
    )
    csv_result_export = measure(
        f"export CSV (this Action's {result.height} rows)",
        lambda: export.to_csv_bytes(result),
    )

    def whole_run() -> object:
        _fresh_store()
        return execute_run(
            action,
            {slot: PendingUpload(f"bench{extension}", io.BytesIO(payload))},
        )

    total = measure("whole Run (execute_run)", whole_run)
    _fresh_store()

    return (
        [
            read,
            parse,
            validation,
            execution,
            csv_export,
            xlsx_export,
            csv_result_export,
            total,
        ],
        {
            "rows in": f"{row_count:,}",
            "rows out": f"{result.height:,}",
            "columns": frame.width,
            "payload": f"{len(payload) / 1_048_576:.2f} MiB",
            "engine": parsed.parser_engine,
        },
    )


# ---------------------------------------------------------------------------
# 7I — preview
# ---------------------------------------------------------------------------


def measure_preview(row_count: int = PREVIEW_ROW_COUNT) -> None:
    """Build plan 7I: rows 1–100 and rows 10,001–10,100 of a large result.

    Also measures a full page (500) and the deepest page in the dataset, so the
    claim being made is visible: a page's cost is a function of the page, not
    of the dataset behind it.
    """
    table = fx.large_table(row_count)
    frame = parser.parse_tabular_bytes(table.as_csv(), ".csv").frame

    print(f"\n7I — preview, against a {frame.height:,}-row result "
          f"({frame.width} columns)")
    print("-" * 78)

    pages = (
        ("rows 1–100", 0, 100),
        ("rows 10,001–10,100", 10_000, 100),
        ("rows 1–500 (the maximum)", 0, 500),
        ("the last 100 rows", max(0, frame.height - 100), 100),
    )
    for label, offset, limit in pages:
        page = measure(
            label,
            lambda offset=offset, limit=limit: preview.read_preview(
                frame, offset=offset, limit=limit
            ),
            repeats=REPEATS * 2,
        )
        rows = preview.read_preview(frame, offset=offset, limit=limit).rows
        print(f"  {label:28} {page}   {len(rows):>3} rows returned")

    # The claim 7I actually asks to verify: the response carries a page, not a
    # dataset. Measured as the size of what a client receives.
    import json

    whole = preview.read_preview(frame, offset=0, limit=100)
    one_page_bytes = len(json.dumps(whole.rows, default=str).encode())
    everything_bytes = len(json.dumps(frame.rows(), default=str).encode())
    print()
    print(f"  a 100-row page serialises to {one_page_bytes:,} bytes")
    print(f"  the whole result would be    {everything_bytes:,} bytes "
          f"({everything_bytes / one_page_bytes:,.0f}x larger)")
    print(f"  page rows returned: {len(whole.rows)} of {whole.total_rows:,} total")


# ---------------------------------------------------------------------------
# 7J — what a Run holds while it holds it
# ---------------------------------------------------------------------------


def measure_memory(row_count: int = PREVIEW_ROW_COUNT) -> None:
    """What one Run costs in memory, measured rather than reasoned about.

    Build plan 7J asks whether the upload is read into memory more than once
    and whether whole frames are converted unnecessarily. The figures here are
    what that question is really about: the payload's size, the frame's size,
    and the peak Python allocation while a Run is executed.
    """
    import io

    table = fx.large_table(row_count)
    payload = table.as_csv()
    action = ExactDuplicateRemoverAction()
    slot = action.inputs[0].id

    _fresh_store()
    gc.collect()
    tracemalloc.start()
    outcome = execute_run(
        action, {slot: PendingUpload("bench.csv", io.BytesIO(payload))}
    )
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    result = outcome.run.result
    assert result is not None
    retained = result.primary.estimated_size()

    print(f"\n7J — memory, one {row_count:,}-row CSV Run")
    print("-" * 78)
    print(f"  uploaded payload                 {len(payload) / 1_048_576:8.2f} MiB")
    print(f"  result frame Polars holds        {retained / 1_048_576:8.2f} MiB")
    print(f"  peak Python allocation, the Run  {peak / 1_048_576:8.2f} MiB")
    retained_runs = len(run_store.RUN_STORE.list_runs())
    print(f"  runs retained by the store       {retained_runs:8d}")

    # The uploaded bytes must not still be reachable: the runner releases them
    # once they have become a DataFrame (build plan 6D.8).
    import weakref

    holder = weakref.ref(result.primary)
    run_id = outcome.run.run_id
    del outcome, result
    gc.collect()
    print(f"  result alive while the Run is recorded: {holder() is not None}")
    run_store.delete_run(run_id)
    gc.collect()
    print(f"  result alive after delete_run:          {holder() is not None}")
    _fresh_store()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def report(title: str, row_counts: Sequence[int], extension: str) -> None:
    print(f"\n{title}")
    print("=" * 78)
    for row_count in row_counts:
        measurements, facts = measure_pipeline(row_count, extension)
        detail = "  ".join(f"{key} {value}" for key, value in facts.items())
        print(f"\n{row_count:,} rows — {detail}")
        print("-" * 78)
        for entry in measurements:
            flag = "   <- varies" if entry.spread > 0.25 else ""
            print(f"  {entry.label:38} {entry}{flag}")

        total = measurements[-1]
        if row_count == 100_000:
            if total.median < DESIRED_SECONDS:
                verdict = "within desired"
            elif total.median < ACCEPTANCE_SECONDS:
                verdict = "within acceptance"
            else:
                verdict = "OVER THRESHOLD"
            print()
            print(
                f"  build plan 3.4: desired < {DESIRED_SECONDS:.0f}s, "
                f"acceptance < {ACCEPTANCE_SECONDS:.0f}s  ->  "
                f"whole Run {total.median:.3f}s ({verdict})"
            )


def main() -> int:
    print("ForgeXL performance measurements (build plan 7G, 7H, 7I)")
    print(f"Python {sys.version.split()[0]}  polars {pl.__version__}  "
          f"{REPEATS} repeats per figure; median [min – max]")
    print("CSV and XLSX are measured and reported separately: they are "
          "different formats\nand build plan 7H asks that they not be "
          "compared as though they were not.")

    report("7G — CSV", CSV_ROW_COUNTS, ".csv")
    report("7H — XLSX", XLSX_ROW_COUNTS, ".xlsx")
    measure_preview()
    measure_memory()
    print("\nDone. Record these in docs/implementation-status.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())