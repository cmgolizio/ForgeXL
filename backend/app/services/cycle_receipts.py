"""Small durable source receipts. Reports and Runs remain in memory."""
from __future__ import annotations

import os
from pathlib import Path
import uuid

from pydantic import ValidationError

from app import config
from app.errors import DataLibraryError, UnknownDatasetVersionError
from app.models.library import parse_period, parse_version_id
from app.models.monthly_workflow import CycleReceipt


class CycleReceiptStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()

    def save(self, receipt: CycleReceipt) -> None:
        # IDs are generated internally but checked at this path boundary too.
        directory = self.root / parse_period(receipt.period)
        destination = directory / (parse_version_id(receipt.cycle_id) + ".json")
        temporary = directory / ("." + uuid.uuid4().hex + ".tmp")
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with temporary.open("xb") as handle:
                handle.write(receipt.model_dump_json(indent=2).encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            if destination.exists():
                raise DataLibraryError("That reporting cycle is already recorded.")
            os.replace(temporary, destination)
        except OSError as error:
            raise DataLibraryError("Source data is saved, but its reporting receipt could not be saved. Validate the saved period again to capture its versions.", details={"period": receipt.period, "reason": type(error).__name__}) from error
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # Never replace the structured save failure with cleanup.

    def get(self, period: str, cycle_id: str) -> CycleReceipt:
        period, cycle_id = parse_period(period), parse_version_id(cycle_id)
        path = self.root / period / (cycle_id + ".json")
        if not path.is_file():
            raise UnknownDatasetVersionError("That reporting cycle is not available.", details={"period": period, "cycle_id": cycle_id})
        try:
            receipt = CycleReceipt.model_validate_json(path.read_bytes())
        except (OSError, ValidationError) as error:
            raise DataLibraryError("The saved reporting receipt cannot be read.", details={"period": period, "cycle_id": cycle_id}) from error
        if receipt.period != period or receipt.cycle_id != cycle_id:
            raise DataLibraryError("The reporting receipt does not match its recorded identity.")
        return receipt

    def list(self, period: str) -> tuple[CycleReceipt, ...]:
        directory = self.root / parse_period(period)
        if not directory.is_dir():
            return ()
        records = [self.get(period, path.stem) for path in directory.glob("*.json")]
        return tuple(sorted(records, key=lambda item: (item.created_at, item.cycle_id), reverse=True))


CYCLE_RECEIPTS = CycleReceiptStore(config.LIBRARY_DIRECTORY / ".reporting-cycles")
