"""Bounded, memory-only form intake for Run requests.

Starlette's default multipart parser rolls uploads larger than 1 MiB onto
disk, before the runner can enforce ForgeXL's upload limit. Keep its multipart
framing and resource cleanup, but check each file's size in the data callback,
before any bytes are queued for writing. The spool threshold equals that limit,
so no accepted file can trigger a disk rollover. This is per-request state;
Starlette's global defaults are never changed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Request
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header
from starlette.datastructures import FormData
from starlette.formparsers import FormParser, MultiPartException, MultiPartParser

from app import config
from app.errors import InvalidRequestError, UploadTooLargeError
from app.services.storage import display_filename


class _MemoryMultiPartParser(MultiPartParser):
    def __init__(self, request: Request) -> None:
        super().__init__(request.headers, request.stream())
        self.file_limit = config.MAX_UPLOAD_BYTES
        # Zero means "unlimited" to SpooledTemporaryFile, so use at least one.
        self.spool_max_size = max(1, self.file_limit)
        self._file_bytes = 0
        self._field_names: set[str] = set()
        self._complete = False

    def on_part_begin(self) -> None:
        super().on_part_begin()
        self._file_bytes = 0

    def on_headers_finished(self) -> None:
        super().on_headers_finished()
        name = self._current_part.field_name
        if name in self._field_names:
            raise InvalidRequestError(
                "Each form field must be submitted only once.",
                details={"field": name},
            )
        self._field_names.add(name)

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        file = self._current_part.file
        if file is not None:
            self._file_bytes += end - start
            if self._file_bytes > self.file_limit:
                filename = file.filename or ""
                raise UploadTooLargeError(
                    f"{display_filename(filename)} is larger than the "
                    f"{self.file_limit} byte upload limit.",
                    details={
                        "slot_id": self._current_part.field_name,
                        "limit_bytes": self.file_limit,
                        "original_filename": filename,
                    },
                )
        super().on_part_data(data, start, end)

    def on_end(self) -> None:
        self._complete = True

    async def parse(self) -> FormData:
        form = await super().parse()
        if not self._complete:
            await form.close()
            # A partial final file is not in form.items yet.
            for file in self._files_to_close_on_error:
                file.close()
            raise InvalidRequestError("The multipart upload is incomplete.")
        return form


@asynccontextmanager
async def read_run_form(request: Request) -> AsyncIterator[FormData]:
    """Close every upload on success, rejection or disconnect.

    A request rejected during intake has not created a Run yet. The runner
    retains its own size check for callers that do not use HTTP.
    """
    content_type, _ = parse_options_header(request.headers.get("content-type"))
    try:
        if content_type == b"multipart/form-data":
            form = await _MemoryMultiPartParser(request).parse()
        elif content_type == b"application/x-www-form-urlencoded":
            form = await FormParser(request.headers, request.stream()).parse()
        else:
            form = FormData()
    except (MultiPartException, MultipartParseError) as error:
        raise InvalidRequestError("The submitted form could not be read.") from error

    try:
        names = [name for name, _ in form.multi_items()]
        if len(names) != len(set(names)):
            raise InvalidRequestError("Each form field must be submitted only once.")
        yield form
    finally:
        await form.close()