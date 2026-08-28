"""Where a Run's runtime state lives (build plan 6B.2-6B.6).

The Action Engine must not care whether runtime state happens to sit in
memory, on disk, in a database or in object storage. :class:`RunStore` is the
narrow interface it depends on; :class:`InMemoryRunStore` is the V1
implementation, and it is process memory and nothing else.

Consequences of the V1 choice, both accepted by the Phase 6 architectural
rules (14 and 15): run history lives only in the running backend process, and
restarting the development server clears it. No database, no queue and no
persistent infrastructure is introduced to avoid that — a later
``PersistentRunStore`` implements the same five methods and nothing above this
module changes.

Module-level :func:`create_run`, :func:`get_run`, :func:`update_run`,
:func:`delete_run` and :func:`list_runs` read the application's default store,
mirroring how :mod:`app.actions.registry` exposes the application registry.
Business logic calls those rather than reaching into any dictionary, so
swapping the implementation is a one-line change here (build plan 6B.5).
"""

from __future__ import annotations

import abc
import threading

from app.errors import UnknownRunError
from app.models.run import Run, parse_run_id


class DuplicateRunIdError(ValueError):
    """Raised when a Run ID is created twice.

    Overwriting silently would mean one Run's record replacing another's, so
    creation fails loudly instead — the same rule the Action registry applies
    to duplicate Action IDs.
    """


class RunStore(abc.ABC):
    """The runtime-state interface every store implementation satisfies.

    Deliberately five methods. Anything wider would leak the storage medium
    into the callers this abstraction exists to protect.
    """

    @abc.abstractmethod
    def create_run(self, run: Run) -> Run:
        """Record a new Run and return it.

        Raises:
            DuplicateRunIdError: a Run with that ID is already recorded.
        """

    @abc.abstractmethod
    def get_run(self, run_id: str) -> Run:
        """Return the Run with `run_id`.

        Raises:
            UnknownRunError: the ID is malformed, or no such Run is recorded.
        """

    @abc.abstractmethod
    def update_run(self, run: Run) -> Run:
        """Replace the recorded state of an existing Run and return it.

        Raises:
            UnknownRunError: no Run with that ID is recorded.
        """

    @abc.abstractmethod
    def delete_run(self, run_id: str) -> bool:
        """Forget a Run, releasing the state it held (build plan 6B.6).

        Returns True if a Run was removed, False if there was nothing to
        remove. Deleting an unknown or malformed ID is not an error: deletion
        is about reaching a state, and that state is already reached.
        """

    @abc.abstractmethod
    def list_runs(self) -> list[Run]:
        """Return every recorded Run, oldest first."""


class InMemoryRunStore(RunStore):
    """The V1 store: one dictionary in the backend process.

    Guarded by a lock because Uvicorn runs synchronous endpoints in a thread
    pool, so two Runs really can touch the store at once. The lock protects the
    check-then-write pairs; the Runs themselves are frozen values and need no
    protection.
    """

    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    def create_run(self, run: Run) -> Run:
        with self._lock:
            if run.run_id in self._runs:
                raise DuplicateRunIdError(
                    f"Run id {run.run_id!r} is already recorded."
                )
            self._runs[run.run_id] = run
        return run

    def get_run(self, run_id: str) -> Run:
        validated = parse_run_id(run_id)
        with self._lock:
            run = self._runs.get(validated)
        if run is None:
            raise UnknownRunError(
                "No Run exists with that ID.", details={"run_id": validated}
            )
        return run

    def update_run(self, run: Run) -> Run:
        with self._lock:
            if run.run_id not in self._runs:
                raise UnknownRunError(
                    "No Run exists with that ID.", details={"run_id": run.run_id}
                )
            self._runs[run.run_id] = run
        return run

    def delete_run(self, run_id: str) -> bool:
        validated = _known_form(run_id)
        if validated is None:
            return False
        with self._lock:
            return self._runs.pop(validated, None) is not None

    def list_runs(self) -> list[Run]:
        with self._lock:
            return list(self._runs.values())

    def __len__(self) -> int:
        return len(self._runs)

    def __contains__(self, run_id: object) -> bool:
        validated = _known_form(run_id) if isinstance(run_id, str) else None
        return validated is not None and validated in self._runs


def _known_form(run_id: str) -> str | None:
    """Return the canonical form of `run_id`, or None if it is not a Run ID.

    Used where a miss is an answer rather than an error: deleting or asking
    about an ID that could never have been issued is simply False.
    """
    try:
        return parse_run_id(run_id)
    except UnknownRunError:
        return None


#: The application's Run Store. Replacing V1's in-memory state with something
#: persistent means assigning a different :class:`RunStore` here; no caller
#: below changes, because none of them knows what this is.
RUN_STORE: RunStore = InMemoryRunStore()


def create_run(run: Run) -> Run:
    """Record `run` in the application's Run Store."""
    return RUN_STORE.create_run(run)


def get_run(run_id: str) -> Run:
    """Return the application's Run with `run_id`."""
    return RUN_STORE.get_run(run_id)


def update_run(run: Run) -> Run:
    """Replace `run`'s recorded state in the application's Run Store."""
    return RUN_STORE.update_run(run)


def delete_run(run_id: str) -> bool:
    """Forget `run_id` in the application's Run Store."""
    return RUN_STORE.delete_run(run_id)


def list_runs() -> list[Run]:
    """Return every Run the application's Run Store holds, oldest first."""
    return RUN_STORE.list_runs()